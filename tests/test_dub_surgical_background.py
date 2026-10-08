"""Regression: separation must not erase audience reactions between dialogue."""
import asyncio
import numpy as np
import pytest
import soundfile as sf

from services.dub_background import dialogue_intervals, splice_background, surgical_background


def test_original_stereo_samples_survive_outside_dialogue(tmp_path):
    sr = 48000
    rng = np.random.default_rng(42)
    original = rng.uniform(-.4, .4, (sr*3, 2)).astype('float32')
    bed = np.full_like(original, .02)
    src, bg, out = [str(tmp_path/p) for p in ('src.wav', 'bg.wav', 'out.wav')]
    sf.write(src, original, sr, subtype='FLOAT')
    sf.write(bg, bed, sr, subtype='FLOAT')
    splice_background(src, bg, out, [(1, 2)])
    mixed, _ = sf.read(out, dtype='float32')
    np.testing.assert_array_equal(mixed[:sr], original[:sr])
    np.testing.assert_array_equal(mixed[2*sr:], original[2*sr:])
    np.testing.assert_array_equal(mixed[sr+480:2*sr-480], bed[sr+480:2*sr-480])
    assert np.isfinite(mixed).all()


def test_overlap_is_one_replacement_region():
    assert dialogue_intervals([{'start':2,'end':3},{'start':1,'end':2.5}]) == [(1,3)]


@pytest.mark.parametrize('end', [0, float('nan'), float('inf')])
def test_timing_with_no_usable_interval_is_rejected(end):
    with pytest.raises(ValueError):
        dialogue_intervals([{'start':0,'end':end}])


def test_degenerate_cues_are_skipped_not_fatal():
    """#2616: one zero-length/odd cue among hundreds must not fail the export."""
    rows = [
        {'start': 1, 'end': 2},
        {'start': 3, 'end': 3},                 # zero length (SRT rounding)
        {'start': 5, 'end': 4},                 # reversed by a timeline edit
        {'start': float('nan'), 'end': 6},
        {'start': None, 'end': 7},
        {'start': -.5, 'end': .5},              # clamped to the media start
    ]
    assert dialogue_intervals(rows) == [(0, .5), (1, 2)]


def test_short_chunks_skip_atempo_but_keep_their_length():
    from services.dub_background import MIN_ATEMPO_S, retime_chunk_filter

    tiny = retime_chunk_filter(0, 10, 10.016, 1.5, 10)
    assert 'atempo' not in tiny and 'atrim=duration=0.024000000' in tiny
    assert 'atempo' not in retime_chunk_filter(0, 10, 12, 1.0, 10)
    assert 'atempo' in retime_chunk_filter(0, 10, 10 + MIN_ATEMPO_S, 1.5, 10)


def test_incomplete_background_is_not_silently_padded(tmp_path):
    src, bg, out = [str(tmp_path/p) for p in ('src.wav', 'bg.wav', 'out.wav')]
    sf.write(src, np.ones((48000,2))*.1, 48000)
    sf.write(bg, np.ones((100,2))*.02, 48000)
    with pytest.raises(ValueError, match='incomplete'):
        splice_background(src, bg, out, [(0,1)])


def test_real_ffmpeg_retime_and_cache_invalidation(tmp_path):
    sr = 48000
    src, bg = [str(tmp_path/p) for p in ('src.wav', 'bg.wav')]
    wave = np.ones((sr*3,2), dtype='float32')*.1
    sf.write(src, wave, sr, subtype='FLOAT')
    sf.write(bg, wave*.2, sr, subtype='FLOAT')
    async def run():
        segments=[{'start':1,'end':2}]
        plain=await surgical_background(src,bg,str(tmp_path),segments,[],3)
        assert await surgical_background(src,bg,str(tmp_path),segments,[],3) == plain
        changed=await surgical_background(src,bg,str(tmp_path),[{'start':.5,'end':2}],[],3)
        assert changed != plain
        retimed=await surgical_background(src,bg,str(tmp_path),segments,[{'orig_start':1,'orig_end':2,'stretch_ratio':2}],3)
        assert sf.info(retimed).duration == pytest.approx(4, abs=.01)
        values,_=sf.read(retimed)
        assert values[int(3.5*sr),0] == pytest.approx(.1,abs=.001)
    asyncio.run(run())


def test_missing_separation_blocks_preserved_export(monkeypatch):
    import api.routers.dub_export as de
    from fastapi import HTTPException
    monkeypatch.setattr(de, '_optional_dub_artifact', lambda *_: None)
    with pytest.raises(HTTPException) as error:
        asyncio.run(de._preserved_background({}, 'job', 'bn'))
    assert error.value.status_code == 409


def test_srt_cues_a_frame_apart_retime_with_real_ffmpeg(tmp_path):
    """#2616: subtitle cues separated by ~16 ms leave sub-window 1.0x gap chunks;
    atempo on those failed the whole ffmpeg batch ("Invalid data found when
    processing input"), so every Stretch Video export with the original
    background answered HTTP 409."""
    sr = 48000
    src, bg = [str(tmp_path/p) for p in ('src.wav', 'bg.wav')]
    wave = np.ones((sr*4, 2), dtype='float32')*.1
    sf.write(src, wave, sr, subtype='FLOAT')
    sf.write(bg, wave*.2, sr, subtype='FLOAT')
    segments = [{'start': .5, 'end': 1.5}, {'start': 1.516, 'end': 2.5}]
    plan = [
        {'orig_start': .5, 'orig_end': 1.5, 'stretch_ratio': 1.2},
        {'orig_start': 1.516, 'orig_end': 2.5, 'stretch_ratio': .9},
    ]
    out = asyncio.run(surgical_background(src, bg, str(tmp_path), segments, plan, 4))
    expected = .5 + 1.2 + .016 + .984*.9 + 1.5
    assert sf.info(out).duration == pytest.approx(expected, abs=.01)


def test_zero_length_plan_entry_does_not_block_the_export(tmp_path):
    """A zero-length cue gets ratio 0 in a Stretch Video plan; it renders
    nothing, so its ratio must not reject the whole background (#2616)."""
    sr = 48000
    src, bg = [str(tmp_path/p) for p in ('src.wav', 'bg.wav')]
    wave = np.ones((sr*3, 2), dtype='float32')*.1
    sf.write(src, wave, sr, subtype='FLOAT')
    sf.write(bg, wave*.2, sr, subtype='FLOAT')
    segments = [{'start': .5, 'end': 1.5}, {'start': 2, 'end': 2}]
    plan = [
        {'orig_start': .5, 'orig_end': 1.5, 'stretch_ratio': 1.5},
        {'orig_start': 2, 'orig_end': 2, 'stretch_ratio': 0.0},
    ]
    out = asyncio.run(surgical_background(src, bg, str(tmp_path), segments, plan, 3))
    assert sf.info(out).duration == pytest.approx(3.5, abs=.01)
