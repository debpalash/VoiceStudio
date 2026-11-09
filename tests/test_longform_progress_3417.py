"""Long-form renders report per-chunk progress, a rolling ETA, and reuse the
warm engine (#3417).

A 34k-word book on an M5 Pro ran 8+ hours with progress frozen for long
stretches: the renderer spoke only at chapter boundaries, so an hour-long
chapter looked exactly like a hang. And on Apple Silicon (OmniVoice runs as a
sidecar subprocess there) every chapter built a fresh engine — a new sidecar
and a full model load per chapter, each old one kept alive by its atexit hook.

Engine layer stubbed throughout — no model loads, no GPU, no ffmpeg.
"""
import asyncio
import json
import os
import time

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest  # noqa: E402
import torch  # noqa: E402

from services.longform_progress import RenderProgress, chapter_weight  # noqa: E402


# ── pure tracker math ───────────────────────────────────────────────────────

class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def test_eta_follows_measured_rate_over_the_remaining_text():
    clock = _Clock()
    p = RenderProgress([1000, 1000], clock=clock)
    assert p.eta_seconds() is None  # nothing timed yet: no invented number
    p.start_chapter(0)
    clock.t = 10.0
    # 100 of 1000 chars took 10 s and produced 5 s of audio.
    p.chunk(0, 100, 1000, audio_s=5.0)
    assert p.fraction() == pytest.approx(0.05)
    assert p.realtime_factor() == pytest.approx(0.5)
    assert p.chunk_seconds() == pytest.approx(10.0)
    # 1900 chars left at 0.1 s/char (= remaining audio / realtime factor).
    assert p.eta_seconds() == pytest.approx(190.0)


def test_cached_work_advances_progress_but_never_the_rate():
    clock = _Clock()
    p = RenderProgress([1000, 1000, 1000], clock=clock)
    p.start_chapter(0)
    clock.t = 0.5
    p.finish_chapter(0)  # whole chapter from cache, near-instant
    assert p.fraction() == pytest.approx(1 / 3)
    assert p.eta_seconds() is None  # an instant cache hit is not a speed
    p.start_chapter(1)
    clock.t = 1.0
    p.chunk(1, 500, 1000, cached=True)  # segment-cache replay
    clock.t = 21.0
    p.chunk(1, 600, 1000, audio_s=8.0)  # fresh: 100 chars in 20 s
    assert p.fraction() == pytest.approx(1600 / 3000)
    assert p.eta_seconds() == pytest.approx(1400 * 0.2)


def test_rate_is_rolling_and_late_reports_from_finished_chapters_are_ignored():
    clock = _Clock()
    p = RenderProgress([10_000], window=2, clock=clock)
    p.start_chapter(0)
    for i, step in enumerate([100.0, 1.0, 1.0], start=1):
        clock.t += step
        p.chunk(0, i * 100, 10_000, audio_s=1.0)
    # The 100 s outlier fell out of the 2-chunk window.
    assert p.chunk_seconds() == pytest.approx(1.0)
    p.finish_chapter(0)
    p.chunk(0, 50, 10_000)  # abandoned thread reporting after the fact
    assert p.fraction() == 1.0


def test_textless_book_falls_back_to_chapter_count():
    p = RenderProgress([0, 0])
    p.finish_chapter(0)
    assert p.fraction() == 0.5
    snap = p.snapshot()
    assert snap["fraction"] == 0.5 and snap["eta_s"] is None


# ── synthesize_chapter reports every chunk ──────────────────────────────────

def test_synthesize_chapter_reports_each_chunk_and_reaches_the_total():
    from services.audiobook import Span, synthesize_chapter

    text = "Word word word word word. " * 100  # several 800-char chunks
    calls = []
    synthesize_chapter(
        [Span(voice_id=None, text=text), Span(voice_id=None, text="Short line.")],
        lambda t, v, s: torch.zeros(1, 2400), 24000,
        on_progress=lambda d, t, **kw: calls.append((d, t, kw)),
    )
    fresh = [c for c in calls if not c[2]["cached"]]
    assert len(fresh) >= 4  # fails before: no per-chunk signal existed
    assert all(c[2]["audio_s"] == pytest.approx(0.1) for c in fresh)
    dones = [c[0] for c in calls]
    assert dones == sorted(dones) and dones[-1] == calls[-1][1]


def test_segment_cache_hits_report_as_cached():
    from services.audiobook import Span, synthesize_chapter

    class _Cache:
        def load(self, span, nonce=0):
            return torch.zeros(1, 2400)

        def store(self, *a, **k):
            raise AssertionError("cached span must not re-store")

    calls = []
    synthesize_chapter([Span(voice_id=None, text="Hello there.")],
                       lambda *a: pytest.fail("cached span must not synthesize"), 24000,
                       segment_cache=_Cache(),
                       on_progress=lambda d, t, **kw: calls.append((d, t, kw["cached"])))
    assert calls == [(12, 12, True)]


def test_a_failing_progress_callback_never_breaks_the_render():
    from services.audiobook import Span, synthesize_chapter

    def boom(*a, **k):
        raise RuntimeError("ui went away")

    audio, dur = synthesize_chapter([Span(voice_id=None, text="Hello.")],
                                    lambda *a: torch.zeros(1, 2400), 24000,
                                    on_progress=boom)
    assert dur == pytest.approx(0.1)


def test_chapter_weight_counts_spoken_characters():
    from services.audiobook import Chapter, Span
    ch = Chapter(title="T", spans=[Span(voice_id=None, text="abc"),
                                   Span(voice_id=None, text="", pause_ms_after=500)])
    assert chapter_weight(ch) == 3


# ── the SSE stream carries live progress ────────────────────────────────────

@pytest.fixture
def env(tmp_path, monkeypatch):
    from api.routers import audiobook
    from core import config, db, job_store
    from services import ffmpeg_utils, longform_resume

    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "jobs.db"))
    monkeypatch.setattr(config, "OUTPUTS_DIR", str(tmp_path / "outputs"))
    os.makedirs(tmp_path / "outputs", exist_ok=True)
    monkeypatch.setattr(longform_resume, "work_dir",
                        lambda t, j: str(tmp_path / "work" / f"{t}-{j}"))
    monkeypatch.setattr(audiobook, "_resolve_default_language", lambda *_a: None)
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "ffmpeg")

    async def mux(command, **_kw):
        with open(command[-1], "wb") as fh:
            fh.write(b"m4b")
        return 0, b"", b""  # the render checks the exit code (#3017)
    monkeypatch.setattr(ffmpeg_utils, "run_ffmpeg", mux)

    def build_synth(default_voice=None, language=None, opts=None, voice_map=None, lease=None):
        def synth(text, voice_id, speed=None):
            time.sleep(0.02)  # measurable wall time per chunk
            return torch.zeros(1, 2400)
        return {"mode": "generic", "engine_id": "stub", "synth": synth,
                "sample_rate": 24000,
                "resolve": lambda _v: {"ref_audio": None, "ref_text": None,
                                       "instruct": None, "seed": None}}
    monkeypatch.setattr(audiobook, "_build_synth", build_synth)
    db.init_db()
    return audiobook, job_store


def test_render_streams_progress_with_eta_before_each_chapter_finishes(env):
    from services.audiobook import AudiobookPlan, Chapter, Span

    audiobook, job_store = env
    long = "Word word word word word. " * 120
    plan = AudiobookPlan(chapters=[
        Chapter(title="One", spans=[Span(voice_id=None, text=long)]),
        Chapter(title="Two", spans=[Span(voice_id=None, text=long)]),
    ])

    async def run():
        return [json.loads(f[len("data: "):])
                async for f in audiobook._render_longform_sse(
                    plan, default_voice=None, job_id="progjob")]

    events = asyncio.run(run())
    first_chapter = next(i for i, e in enumerate(events) if e["type"] == "chapter")
    live = [e for e in events[:first_chapter] if e["type"] == "progress"]
    assert live, "no progress until the chapter finished (the #3417 freeze)"
    assert all(e["index"] == 0 and 0 < e["fraction"] <= 0.5 for e in live)
    assert any(e["eta_s"] is not None and e["rtf"] > 0 and e["chunk_s"] > 0 for e in live)
    fractions = [e["fraction"] for e in events if e["type"] == "progress"]
    assert fractions == sorted(fractions) and fractions[-1] == 1.0
    assert events[-1]["type"] == "done"
    # Live-only: job history keeps the outcomes, not one row per chunk.
    persisted = [json.loads(r["payload"])["type"]
                 for r in job_store.events_since("progjob", 0)]
    assert "progress" not in persisted and "chapter" in persisted


# ── one warm engine for the whole book ──────────────────────────────────────

def test_generic_engine_is_built_once_not_per_chapter(monkeypatch):
    import api.routers.audiobook as ab
    import services.tts_backend as tb
    from services.tts_backend import TTSBackend

    built = []

    class _Sidecar(TTSBackend):
        id = "fake-sidecar-3417"
        display_name = "Fake sidecar (test)"
        gpu_compat = ("cpu",)

        def __init__(self):
            built.append(self)  # a real sidecar engine spawns a process here

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            return torch.zeros(1, 2400)

    monkeypatch.setattr(tb, "active_backend_id", lambda: _Sidecar.id)
    monkeypatch.setattr(tb, "get_backend_class", lambda _id: _Sidecar)
    monkeypatch.setattr(ab, "_resolve_voice", lambda _vid: {
        "ref_audio": None, "ref_text": None, "instruct": None, "seed": None})
    try:
        for _chapter in range(3):  # _run_chapter builds the synth per chapter
            ab._build_synth(None)["synth"]("hello", None)
        assert len(built) == 1  # fails before: one engine (sidecar) per chapter
    finally:
        tb._ENGINE_INSTANCES.pop(_Sidecar, None)


def test_a_fast_positive_measurement_never_reports_zero():
    from services.longform_progress import RenderProgress

    p = RenderProgress([1000], clock=lambda: 0.0)
    p.start_chapter(0, now=0.0)
    p.chunk(0, 100, 1000, audio_s=0.5, now=0.03)  # a 30 ms chunk
    snap = p.snapshot(now=0.03)
    assert snap["chunk_s"] == 0.1 and snap["rtf"] > 0
