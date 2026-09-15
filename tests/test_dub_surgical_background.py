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
def test_invalid_timing_is_rejected(end):
    with pytest.raises(ValueError):
        dialogue_intervals([{'start':0,'end':end}])


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
