"""#2279: the longform chapter cache must survive what a reboot can change.

Rendered chapters were keyed by the reference audio's ABSOLUTE path, so any
change in how the data dir is reached (relocated in Settings, remounted, a
symlink or env override) silently re-keyed every chapter and segment. A
power-off could also leave a torn cache WAV or an empty resume manifest,
because neither write was flushed before its rename. Drives the real
``_render_chapter_cached`` / ``_remote_chapter_call`` with a stub synth (no
model/GPU).

App modules are resolved at call time, never at collection: other suites pop
and re-import ``core.config`` (and purge ``api.*``/``services.*``), so a
module-level ``import core.config`` would patch a stale module object that the
code under test no longer reads.
"""
from __future__ import annotations

import importlib
import json
import logging
import os
import types
import wave

import pytest
import torch

_SR = 24000


def _mod(name: str):
    return importlib.import_module(name)


def _render(*args, **kwargs):
    return _mod("api.routers.audiobook")._render_chapter_cached(*args, **kwargs)


def _chapter(*texts, pause=100):
    ab = _mod("services.audiobook")
    return ab.Chapter(title="Title", spans=[
        ab.Span(voice_id="v1", text=t, pause_ms_after=pause) for t in texts
    ])


def _synth(calls):
    def synth(text, voice_id, speed=None):
        calls.append(text)
        return torch.full((2400,), 0.1)
    return synth


def _ref(voices_dir) -> str:
    return os.path.join(str(voices_dir), "v1.wav")


def _resolver(voices_dir, ref_text="hello"):
    def resolve(_voice_id):
        return {"ref_audio": _ref(voices_dir), "ref_text": ref_text,
                "instruct": None, "seed": None}
    return resolve


def _legacy_sig(voices_dir, ref_text="hello"):
    return f"{_ref(voices_dir)}|{ref_text}|None|None"


@pytest.fixture
def data_dirs(tmp_path, monkeypatch):
    """Two spellings of one data dir; the cache itself stays in one place."""
    old, new = tmp_path / "old" / "voices", tmp_path / "moved" / "voices"
    old.mkdir(parents=True)
    new.mkdir(parents=True)
    cache = tmp_path / "cache"
    cache.mkdir()
    # Hand-built legacy keys below mirror an unmarked render.
    monkeypatch.setattr(_mod("services.watermark"), "will_mark", lambda: False)

    def use(voices_dir):
        monkeypatch.setattr(_mod("core.config"), "VOICES_DIR", str(voices_dir))
    return old, new, cache, use


def test_portable_ref_audio(tmp_path, monkeypatch):
    portable = _mod("api.routers.audiobook")._portable_ref_audio
    monkeypatch.setattr(_mod("core.config"), "VOICES_DIR", str(tmp_path / "voices"))
    inside = os.path.join(str(tmp_path / "voices"), "sub", "a.wav")
    assert portable(inside) == "voices:sub/a.wav"
    outside = str(tmp_path / "elsewhere.wav")
    assert portable(outside) == outside
    assert portable(None) is None


def test_portable_ref_audio_reads_the_live_config(tmp_path, monkeypatch):
    """A re-imported ``core.config`` (the suite's reload fixtures, a runtime
    data-dir change) is what the key follows — not a copy taken at import."""
    import sys

    first = str(tmp_path / "a" / "voices")
    monkeypatch.setattr(_mod("core.config"), "VOICES_DIR", first)
    portable = _mod("api.routers.audiobook")._portable_ref_audio
    fresh = types.ModuleType("core.config")
    fresh.VOICES_DIR = str(tmp_path / "b" / "voices")
    monkeypatch.setitem(sys.modules, "core.config", fresh)
    assert portable(os.path.join(fresh.VOICES_DIR, "x.wav")) == "voices:x.wav"


def test_chapter_hits_after_data_dir_moves(data_dirs):
    old, new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render(_chapter("One line title."), _synth(calls), _SR, "eng", _resolver(old), str(cache))
    assert calls == ["One line title."]

    use(new)  # same profile, same cache, data dir reached by another path
    calls.clear()
    _path, _dur, cached, _stats = _render(
        _chapter("One line title."), _synth(calls), _SR, "eng", _resolver(new), str(cache))
    assert cached is True
    assert calls == []


def test_segments_hit_after_data_dir_moves(data_dirs):
    old, new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render(_chapter("A.", "B."), _synth(calls), _SR, "eng", _resolver(old), str(cache))
    use(new)
    calls.clear()
    # A changed pause misses the chapter key, but both segments are reused.
    _p, _d, cached, stats = _render(
        _chapter("A.", "B.", pause=300), _synth(calls), _SR, "eng", _resolver(new), str(cache))
    assert cached is False
    assert calls == []
    assert stats == {"total": 2, "cached": 2}


def _chapter_key(voice_sig):
    lr = _mod("services.longform_render")
    return lr.chapter_cache_key([("v1", "Hi.", 100, None)], sample_rate=_SR,
                                engine_id="eng", voice_sig=voice_sig)


def test_caches_written_by_released_versions_still_hit(data_dirs):
    """Entries keyed by the absolute path (every existing cache) are reused
    and moved to the portable key — no re-render after upgrading."""
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    wav, _d, _c, _s = _render(_chapter("Hi."), _synth(calls), _SR, "eng",
                              _resolver(old), str(cache))
    legacy = os.path.join(str(cache), f"{_chapter_key({'v1': _legacy_sig(old)})}.wav")
    assert legacy != wav
    os.replace(wav, legacy)  # what a released build left on disk

    calls.clear()
    path, _d, cached, _s = _render(_chapter("Hi."), _synth(calls), _SR, "eng",
                                   _resolver(old), str(cache))
    assert cached is True and calls == []
    assert path == wav and os.path.isfile(wav) and not os.path.exists(legacy)


def test_released_caches_hit_after_upgrading_then_moving_the_data_dir(data_dirs):
    """Upgrade (the cache learns its voices root), THEN relocate: the legacy
    entries were keyed under the OLD root, and must still be found."""
    old, new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    wav, _d, _c, _s = _render(_chapter("Hi."), _synth(calls), _SR, "eng",
                              _resolver(old), str(cache))
    lr = _mod("services.longform_render")
    seg_dir = cache / lr.SEGMENT_SUBDIR
    kw = dict(sample_rate=_SR, engine_id="eng", voice_id="v1", speed=None)
    seg_now = seg_dir / f"{lr.segment_cache_key('Hi.', voice_sig='voices:v1.wav|hello|None|None', **kw)}.wav"
    seg_legacy = seg_dir / f"{lr.segment_cache_key('Hi.', voice_sig=_legacy_sig(old), **kw)}.wav"
    chapter_legacy = os.path.join(str(cache), f"{_chapter_key({'v1': _legacy_sig(old)})}.wav")
    os.replace(wav, chapter_legacy)
    os.replace(seg_now, seg_legacy)

    use(new)
    calls.clear()
    path, _d, cached, _s = _render(_chapter("Hi."), _synth(calls), _SR, "eng",
                                   _resolver(new), str(cache))
    assert cached is True and calls == [] and path == wav
    # The segment layer probes the old root too (changed pause → chapter miss).
    _p, _d, cached, stats = _render(_chapter("Hi.", pause=400), _synth(calls), _SR, "eng",
                                    _resolver(new), str(cache))
    assert cached is False and calls == [] and stats == {"total": 1, "cached": 1}
    assert seg_now.is_file() and not seg_legacy.exists()


def test_legacy_segments_still_hit(data_dirs):
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    _render(_chapter("Seg."), _synth(calls), _SR, "eng", _resolver(old), str(cache))
    lr = _mod("services.longform_render")
    seg_dir = cache / lr.SEGMENT_SUBDIR
    kw = dict(sample_rate=_SR, engine_id="eng", voice_id="v1", speed=None)
    current = seg_dir / f"{lr.segment_cache_key('Seg.', voice_sig='voices:v1.wav|hello|None|None', **kw)}.wav"
    legacy = seg_dir / f"{lr.segment_cache_key('Seg.', voice_sig=_legacy_sig(old), **kw)}.wav"
    assert current.is_file()
    os.replace(current, legacy)

    calls.clear()
    _p, _d, cached, stats = _render(
        _chapter("Seg.", pause=250), _synth(calls), _SR, "eng", _resolver(old), str(cache))
    assert cached is False and calls == []
    assert stats == {"total": 1, "cached": 1}
    assert current.is_file() and not legacy.exists()


@pytest.mark.parametrize("cut", [4, 2400], ids=["short-tail", "half"])
def test_torn_chapter_wav_rerenders(data_dirs, cut):
    """A power-off can leave a header that promises audio the file lacks —
    even a tail shorter than the RIFF/chunk headers must count as torn."""
    old, _new, cache, use = data_dirs
    use(old)
    calls: list[str] = []
    wav, _d, _c, _s = _render(_chapter("Torn."), _synth(calls), _SR, "eng",
                              _resolver(old), str(cache))
    with open(wav, "r+b") as f:
        f.truncate(os.path.getsize(wav) - cut)
    calls.clear()
    # The torn segment is refused too, so the call count proves both misses.
    _p, _d, cached, _s = _render(_chapter("Torn."), _synth(calls), _SR, "eng",
                                 _resolver(old), str(cache))
    assert cached is False
    seg = next((cache / _mod("services.longform_render").SEGMENT_SUBDIR).iterdir())
    with open(seg, "r+b") as f:
        f.truncate(os.path.getsize(seg) - cut)
    with open(wav, "r+b") as f:
        f.truncate(os.path.getsize(wav) - cut)
    _p, _d, cached, stats = _render(_chapter("Torn."), _synth(calls), _SR, "eng",
                                    _resolver(old), str(cache))
    assert cached is False and calls == ["Torn."] and stats == {"total": 1, "cached": 0}
    with wave.open(wav, "rb") as w:
        assert w.getnframes() > 0
    assert _mod("services.longform_render").wav_is_complete(wav)


def test_wav_is_complete_rejects_non_wav(tmp_path):
    lr = _mod("services.longform_render")
    p = tmp_path / "x.wav"
    p.write_bytes(b"not a wav at all")
    assert not lr.wav_is_complete(str(p))
    assert not lr.wav_is_complete(str(tmp_path / "missing.wav"))


def test_miss_names_the_changed_input(data_dirs, caplog):
    old, _new, cache, use = data_dirs
    use(old)
    lexicon = {"Why": "LEXICON-SENTINEL-RESPELLING"}
    _render(_chapter("Why?"), _synth([]), _SR, "eng", _resolver(old), str(cache), lexicon)
    with caplog.at_level(logging.INFO, logger="omnivoice.audiobook"):
        _render(_chapter("Why?"), _synth([]), _SR, "eng",
                _resolver(old, ref_text="a new transcript"), str(cache), lexicon)
    assert "voice v1 reference text" in caplog.text
    assert "reference audio" not in caplog.text
    # The record holds digests only — no script, transcript or lexicon text.
    records = list((cache / "inputs").iterdir())
    blob = "".join(p.read_text() for p in records)
    for text in ("Why", "transcript", "hello", "LEXICON-SENTINEL", "v1.wav"):
        assert text not in blob
    assert all(isinstance(json.loads(p.read_text()), dict) for p in records)


# ── Remote chapter cache ────────────────────────────────────────────────────

def _remote(cache, monkeypatch, voices_dir):
    ab = _mod("api.routers.audiobook")
    monkeypatch.setattr(ab, "_map_span_voice", lambda _v, default, _m: default)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _pid: {
        "ref_audio": _ref(voices_dir), "ref_text": "hello", "instruct": None, "seed": None})
    call, path = ab._remote_chapter_call(
        _chapter("Remote."), engine_id="eng", default_voice="p1", voice_map=None,
        language=None, lexicon=None, opts=_mod("services.audiobook").ExpressiveOptions(),
        cache_dir=str(cache))
    return call, path


def _wav_file(path):
    import soundfile as sf
    sf.write(str(path), [0.1] * 2400, _SR, subtype="PCM_16")


def test_remote_cache_follows_the_data_dir_and_legacy_fallback(data_dirs, monkeypatch, tmp_path):
    old, new, cache, use = data_dirs
    use(old)
    _call, portable = _remote(cache, monkeypatch, old)
    _wav_file(portable)
    use(new)
    assert _remote(cache, monkeypatch, new)[1] == portable  # moved data dir → same key
    os.remove(portable)

    # What a released build keyed under the OLD root (absolute path) …
    use(old)
    with monkeypatch.context() as m:
        m.setattr(_mod("api.routers.audiobook"), "_portable_ref_audio", lambda r: r)
        legacy = _remote(cache, m, old)[1]
    _wav_file(legacy)
    # … is still a hit under the new root, used in place when the move fails.
    use(new)
    lr = _mod("services.longform_render")
    monkeypatch.setattr(lr, "adopt_cached_file", lambda legacy_path, path: legacy_path)
    call, path = _remote(cache, monkeypatch, new)
    assert path == legacy and os.path.isfile(legacy)
    # decode() reads the existing complete entry instead of overwriting it.
    got = call.decode(types.SimpleNamespace(path=str(tmp_path / "nope.wav")))
    assert got[0] == legacy and got[2] is False


def test_remote_decode_is_durable_and_replaces_a_torn_entry(data_dirs, monkeypatch, tmp_path):
    old, _new, cache, use = data_dirs
    use(old)
    call, path = _remote(cache, monkeypatch, old)
    with open(path, "wb") as f:
        f.write(b"RIFF\x00\x00\x00\x00WAVE")  # torn: header only
    produced = tmp_path / "remote.wav"
    _wav_file(produced)
    events = _record_durable_events(monkeypatch)
    wav, dur, _cached, _s = call.decode(types.SimpleNamespace(path=str(produced)))
    assert wav == path and dur > 0
    assert _mod("services.longform_render").wav_is_complete(path)
    assert events == ["flush-file", "rename", "flush-dir"]


# ── Durable publication ─────────────────────────────────────────────────────

def _record_durable_events(monkeypatch) -> list[str]:
    """Record file-flush, rename and dir-flush of manifest/WAV publications,
    in order, while still performing the real rename."""
    events: list[str] = []
    real_replace = os.replace

    def replace(src, dst):
        if str(dst).endswith((".wav", "resume.json", "voices_roots.json")):
            events.append("rename")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    durable_io = _mod("core.durable_io")
    monkeypatch.setattr(durable_io, "flush_file", lambda p: events.append("flush-file"))
    monkeypatch.setattr(durable_io, "flush_dir", lambda p: events.append("flush-dir"))
    monkeypatch.setattr(durable_io, "flush_fd", lambda fd: events.append("flush-file"))
    resume = _mod("services.longform_resume")
    monkeypatch.setattr(resume, "flush_fd", lambda fd: events.append("flush-file"))
    monkeypatch.setattr(resume, "flush_dir", lambda p: events.append("flush-dir"))
    return events


def test_resume_manifest_and_chapter_wav_are_published_durably(tmp_path, monkeypatch):
    """Each publication flushes its data BEFORE the rename and the directory
    AFTER it, so a power-off can't publish an empty file under the real name."""
    monkeypatch.setattr(_mod("services.watermark"), "will_mark", lambda: False)
    events = _record_durable_events(monkeypatch)
    resume = _mod("services.longform_resume")
    monkeypatch.setattr(_mod("core.config"), "OUTPUTS_DIR", str(tmp_path))
    assert resume.write_manifest(resume.build_manifest(
        job_id="abc123", job_type="audiobook", plan_chapters=[], params={}))
    assert events == ["flush-file", "rename", "flush-dir"]

    events.clear()
    monkeypatch.setattr(_mod("core.config"), "VOICES_DIR", str(tmp_path / "voices"))
    _render(_chapter("Flush."), _synth([]), _SR, "eng",
            _resolver(tmp_path / "voices"), str(tmp_path / "cache"))
    # The voices-roots index, the segment, then the chapter — each durable.
    assert events == ["flush-file", "rename", "flush-dir"] * 3


def test_durable_io_flushes_real_files(tmp_path):
    durable_io = _mod("core.durable_io")
    p = tmp_path / "f.bin"
    p.write_bytes(b"data")
    durable_io.flush_file(str(p))
    durable_io.flush_dir(str(tmp_path))
    durable_io.flush_file(str(tmp_path / "missing"))  # best-effort: never raises
    durable_io.flush_dir(str(tmp_path / "missing"))
    assert p.read_bytes() == b"data"


def test_voices_roots_index_is_published_durably(tmp_path, monkeypatch):
    lr = _mod("services.longform_render")
    durable_io = _mod("core.durable_io")
    events: list[str] = []
    real_replace = os.replace

    def replace(src, dst):
        if str(dst).endswith(lr.VOICES_ROOTS_FILE):
            events.append("rename")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(durable_io, "flush_fd", lambda fd: events.append("flush-file"))
    monkeypatch.setattr(durable_io, "flush_dir", lambda p: events.append("flush-dir"))
    assert lr.remember_voices_root(str(tmp_path), "/a/voices") == []
    assert events == ["flush-file", "rename", "flush-dir"]
    events.clear()
    assert lr.remember_voices_root(str(tmp_path), "/a/voices") == []
    assert events == []  # unchanged root: no rewrite per chapter
    assert lr.remember_voices_root(str(tmp_path), "/b/voices") == ["/a/voices"]


def test_backend_startup_records_the_voices_root(tmp_path, monkeypatch):
    """Upgrade, launch (nothing rendered), move the data dir: the root the
    legacy entries were keyed under is already on record."""
    lr = _mod("services.longform_render")
    cfg = _mod("core.config")
    outputs = tmp_path / "outputs"
    monkeypatch.setattr(cfg, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(cfg, "VOICES_DIR", str(tmp_path / "voices"))
    lr.record_startup_voices_root()
    assert not outputs.exists()  # no cache → nothing legacy, nothing created
    cache = outputs / lr.LONGFORM_CACHE_SUBDIR
    cache.mkdir(parents=True)
    lr.record_startup_voices_root()
    roots = json.loads((cache / lr.VOICES_ROOTS_FILE).read_text())
    assert roots == [str(tmp_path / "voices")]


def test_phase_b_records_the_voices_root():
    import inspect

    src = inspect.getsource(_mod("main")._phase_b)
    assert "record_startup_voices_root()" in src


def test_adopted_cache_entry_is_published_durably(tmp_path, monkeypatch):
    """A legacy-key hit moved to its new key flushes the directory after the
    rename, so a power-off cannot undo the migration (#2279)."""
    lr = _mod("services.longform_render")
    durable_io = _mod("core.durable_io")
    legacy = tmp_path / "old.wav"
    legacy.write_bytes(b"x")
    new = tmp_path / "new.wav"
    events: list[str] = []
    real_replace = os.replace

    def replace(src, dst):
        events.append("rename")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", replace)
    monkeypatch.setattr(durable_io, "flush_dir", lambda p: events.append(f"flush-dir:{p}"))
    assert lr.adopt_cached_file(str(legacy), str(new)) == str(new)
    assert events == ["rename", f"flush-dir:{tmp_path}"]
    events.clear()
    assert lr.adopt_cached_file(str(tmp_path / "gone.wav"), str(new)) == str(tmp_path / "gone.wav")
    assert events == ["rename"]  # failed move: nothing to publish
