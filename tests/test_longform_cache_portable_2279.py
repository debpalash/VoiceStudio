"""#2279: the longform chapter cache must survive what a reboot can change.

Rendered chapters were keyed by the reference audio's ABSOLUTE path, so any
change in how the data dir is reached (relocated in Settings, remounted, a
symlink or env override) silently re-keyed every chapter and segment. A
power-off could also leave a torn cache WAV or an empty resume manifest,
because neither write was flushed before its rename. Drives the real
``_render_chapter_cached`` with a stub synth (no model/GPU).
"""
from __future__ import annotations

import json
import logging
import os
import wave

import pytest
import torch

import core.config
from api.routers.audiobook import _render_chapter_cached
from services.audiobook import Chapter, Span
from services.longform_render import SEGMENT_SUBDIR, chapter_cache_key, segment_cache_key

_SR = 24000


def _chapter(*texts, pause=100):
    return Chapter(title="Title", spans=[
        Span(voice_id="v1", text=t, pause_ms_after=pause) for t in texts
    ])


def _synth(calls):
    def synth(text, voice_id, speed=None):
        calls.append(text)
        return torch.full((2400,), 0.1)
    return synth


def _resolver(voices_dir, ref_text="hello"):
    def resolve(_voice_id):
        return {"ref_audio": os.path.join(str(voices_dir), "v1.wav"),
                "ref_text": ref_text, "instruct": None, "seed": None}
    return resolve


@pytest.fixture
def data_dirs(tmp_path, monkeypatch):
    """Two spellings of one data dir; the cache itself stays in one place."""
    old, new = tmp_path / "old" / "voices", tmp_path / "moved" / "voices"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    # Hand-built legacy keys below mirror an unmarked render.
    import services.watermark as watermark
    monkeypatch.setattr(watermark, "will_mark", lambda: False)

    def use(voices_dir):
        monkeypatch.setattr(core.config, "VOICES_DIR", str(voices_dir))
    return old, new, cache, use


def test_portable_ref_audio(tmp_path, monkeypatch):
    from api.routers.audiobook import _portable_ref_audio

    monkeypatch.setattr(core.config, "VOICES_DIR", str(tmp_path / "voices"))
    inside = os.path.join(str(tmp_path / "voices"), "sub", "a.wav")
    assert _portable_ref_audio(inside) == "voices:sub/a.wav"
    outside = str(tmp_path / "elsewhere.wav")
    assert _portable_ref_audio(outside) == outside
    assert _portable_ref_audio(None) is None


def test_chapter_hits_after_data_dir_moves(data_dirs):
    old, new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render_chapter_cached(_chapter("One line title."), _synth(calls), _SR, "eng",
                           _resolver(old), str(cache))
    assert calls == ["One line title."]

    use(new)  # same profile, same cache, data dir reached by another path
    calls.clear()
    _path, _dur, cached, _stats = _render_chapter_cached(
        _chapter("One line title."), _synth(calls), _SR, "eng", _resolver(new), str(cache))
    assert cached is True
    assert calls == []


def test_segments_hit_after_data_dir_moves(data_dirs):
    old, new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render_chapter_cached(_chapter("A.", "B."), _synth(calls), _SR, "eng",
                           _resolver(old), str(cache))
    use(new)
    calls.clear()
    # A changed pause misses the chapter key, but both segments are reused.
    _p, _d, cached, stats = _render_chapter_cached(
        _chapter("A.", "B.", pause=300), _synth(calls), _SR, "eng", _resolver(new), str(cache))
    assert cached is False
    assert calls == []
    assert stats == {"total": 2, "cached": 2}


def _legacy_sig(voices_dir, ref_text="hello"):
    return f"{os.path.join(str(voices_dir), 'v1.wav')}|{ref_text}|None|None"


def test_caches_written_by_released_versions_still_hit(data_dirs):
    """Entries keyed by the absolute path (every existing cache) are reused
    and moved to the portable key — no re-render after upgrading."""
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    wav, _d, _c, _s = _render_chapter_cached(_chapter("Hi."), _synth(calls), _SR, "eng",
                                             _resolver(old), str(cache))
    legacy_key = chapter_cache_key([("v1", "Hi.", 100, None)], sample_rate=_SR,
                                   engine_id="eng", voice_sig={"v1": _legacy_sig(old)})
    legacy = os.path.join(str(cache), f"{legacy_key}.wav")
    assert legacy != wav
    os.replace(wav, legacy)  # what a released build left on disk

    calls.clear()
    path, _d, cached, _s = _render_chapter_cached(_chapter("Hi."), _synth(calls), _SR, "eng",
                                                  _resolver(old), str(cache))
    assert cached is True and calls == []
    assert path == wav and os.path.isfile(wav) and not os.path.exists(legacy)


def test_legacy_segments_still_hit(data_dirs):
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render_chapter_cached(_chapter("Seg."), _synth(calls), _SR, "eng",
                           _resolver(old), str(cache))
    seg_dir = cache / SEGMENT_SUBDIR
    kw = dict(sample_rate=_SR, engine_id="eng", voice_id="v1", speed=None)
    current = seg_dir / f"{segment_cache_key('Seg.', voice_sig='voices:v1.wav|hello|None|None', **kw)}.wav"
    legacy = seg_dir / f"{segment_cache_key('Seg.', voice_sig=_legacy_sig(old), **kw)}.wav"
    assert current.is_file()
    os.replace(current, legacy)

    calls.clear()
    _p, _d, cached, stats = _render_chapter_cached(
        _chapter("Seg.", pause=250), _synth(calls), _SR, "eng", _resolver(old), str(cache))
    assert cached is False and calls == []
    assert stats == {"total": 1, "cached": 1}
    assert current.is_file() and not legacy.exists()


def test_torn_chapter_wav_rerenders(data_dirs):
    """A power-off can leave a header that promises audio the file lacks."""
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    wav, _d, _c, _s = _render_chapter_cached(_chapter("Torn."), _synth(calls), _SR, "eng",
                                             _resolver(old), str(cache))
    with open(wav, "r+b") as f:
        f.truncate(os.path.getsize(wav) // 2)
    for sub in (cache / SEGMENT_SUBDIR).iterdir():
        sub.unlink()  # force real synthesis so the call count proves the miss
    calls.clear()
    _p, _d, cached, _s = _render_chapter_cached(_chapter("Torn."), _synth(calls), _SR, "eng",
                                                _resolver(old), str(cache))
    assert cached is False and calls == ["Torn."]
    with wave.open(wav, "rb") as w:
        assert w.getnframes() > 0


def test_miss_names_the_changed_input(data_dirs, caplog):
    old, _new, cache, use = data_dirs
    use(old)
    _render_chapter_cached(_chapter("Why?"), _synth([]), _SR, "eng",
                           _resolver(old), str(cache))
    with caplog.at_level(logging.INFO, logger="omnivoice.audiobook"):
        _render_chapter_cached(_chapter("Why?"), _synth([]), _SR, "eng",
                               _resolver(old, ref_text="a new transcript"), str(cache))
    assert "voice v1 reference text" in caplog.text
    assert "reference audio" not in caplog.text
    # The record holds digests only — no script, transcript or lexicon text.
    records = list((cache / "inputs").iterdir())
    blob = "".join(p.read_text() for p in records)
    assert "Why?" not in blob and "transcript" not in blob
    assert all(isinstance(json.loads(p.read_text()), dict) for p in records)


def test_resume_manifest_and_chapter_wav_are_flushed(tmp_path, monkeypatch):
    """Both writes flush their data before the rename, so a power-off can't
    publish an empty file under the real name."""
    import core.durable_io as durable_io
    import services.longform_resume as longform_resume

    flushed: list = []
    monkeypatch.setattr(longform_resume, "flush_fd", lambda fd: flushed.append("manifest"))
    monkeypatch.setattr(durable_io, "flush_file", lambda p: flushed.append("wav"))
    monkeypatch.setattr(core.config, "OUTPUTS_DIR", str(tmp_path))
    assert longform_resume.write_manifest(longform_resume.build_manifest(
        job_id="abc123", job_type="audiobook", plan_chapters=[], params={}))
    assert flushed == ["manifest"]

    monkeypatch.setattr(core.config, "VOICES_DIR", str(tmp_path / "voices"))
    _render_chapter_cached(_chapter("Flush."), _synth([]), _SR, "eng",
                           _resolver(tmp_path / "voices"), str(tmp_path / "cache"))
    assert flushed.count("wav") == 2  # the segment and the chapter


def test_durable_io_flushes_real_files(tmp_path):
    from core.durable_io import flush_dir, flush_file

    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    flush_file(str(p))
    flush_dir(str(tmp_path))
    flush_file(str(tmp_path / "missing"))  # best-effort: never raises
    assert p.read_bytes() == b"data"
