"""Deadlines applied to a sidecar while generate() waits on it (#2103)."""
from __future__ import annotations

import asyncio
import importlib
import sys
import textwrap
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from services import model_manager
from services import subprocess_backend as sb
from services.subprocess_backend import SubprocessBackend

SHORT_TEXT = "hi"
HUGE_TEXT = "y" * 200_000


def make_engine(name, **attrs):
    """Build a throwaway concrete SubprocessBackend subclass and instantiate it."""
    body = {
        "id": name,
        "is_available": classmethod(lambda cls: (True, "ok")),
        "sample_rate": property(lambda self: 24000),
        "supported_languages": property(lambda self: ["multi"]),
        **attrs,
    }
    return type(f"Engine_{name}", (SubprocessBackend,), body)()


# ── constants and class default ─────────────────────────────────────────────

def test_constants_keep_ping_and_generate_budgets_apart():
    assert sb.RECV_TIMEOUT_S == 60.0
    assert sb.GENERATE_RECV_TIMEOUT_S > sb.RECV_TIMEOUT_S
    assert sb.GENERATE_RECV_TIMEOUT_S >= model_manager.CPU_JOB_TIMEOUT_S


def test_unconfigured_engine_inherits_generate_budget():
    assert make_engine("plain").recv_timeout_s == sb.GENERATE_RECV_TIMEOUT_S


@pytest.mark.parametrize("module,cls", [
    ("engines.confucius4", "Confucius4Backend"),
    ("engines.dots_tts", "DotsTTSBackend"),
    ("engines.moss_tts_v15", "MossTTSV15Backend"),
    ("engines.supertonic3.backend", "Supertonic3Backend"),
])
def test_engines_that_used_the_ping_budget_now_exceed_it(module, cls):
    engine = getattr(importlib.import_module(module), cls)()
    assert engine.recv_timeout_s > sb.RECV_TIMEOUT_S


def test_every_sidecar_engine_covers_the_gpu_job_budget():
    for mod in ("engines.confucius4", "engines.dots_tts", "engines.moss_tts_v15",
                "engines.supertonic3.backend", "engines.omnivoice_subprocess"):
        importlib.import_module(mod)
    pending, found = list(SubprocessBackend.__subclasses__()), set()
    while pending:
        klass = pending.pop()
        pending.extend(klass.__subclasses__())
        if klass.__module__.startswith("engines."):
            found.add(klass)
    assert found
    for klass in found:
        try:
            instance = klass()
        except Exception:
            continue
        assert instance.recv_timeout_s >= model_manager.GPU_JOB_TIMEOUT_S, klass.__name__


# ── per-request deadline ────────────────────────────────────────────────────

def test_deadline_for_short_input_is_the_flat_default():
    assert make_engine("a")._effective_recv_timeout_s(SHORT_TEXT) == sb.GENERATE_RECV_TIMEOUT_S


def test_deadline_grows_with_huge_input():
    engine = make_engine("b")
    wanted = model_manager.generate_timeout_s(HUGE_TEXT, engine=engine, _include_sidecar_grace=False)
    got = engine._effective_recv_timeout_s(HUGE_TEXT)
    assert got > sb.GENERATE_RECV_TIMEOUT_S
    assert got == pytest.approx(wanted)


@pytest.mark.parametrize("text", [SHORT_TEXT, HUGE_TEXT])
def test_engine_declared_deadline_is_never_overridden(text):
    assert make_engine("c", recv_timeout_s=45.0)._effective_recv_timeout_s(text) == 45.0


@pytest.mark.parametrize("bad", [RuntimeError("no probe"), float("inf"), float("nan")])
def test_unusable_budget_leaves_flat_default(monkeypatch, bad):
    def fake(*_a, **_k):
        if isinstance(bad, Exception):
            raise bad
        return bad

    monkeypatch.setattr(model_manager, "generate_timeout_s", fake)
    assert make_engine("d")._effective_recv_timeout_s(HUGE_TEXT) == sb.GENERATE_RECV_TIMEOUT_S


@pytest.mark.parametrize("declared", [None, 45.0])
@pytest.mark.parametrize("text", [SHORT_TEXT, HUGE_TEXT])
def test_pool_budget_outlasts_sidecar_deadline_by_grace(declared, text):
    engine = make_engine("e", **({} if declared is None else {"recv_timeout_s": declared}))
    assert model_manager.generate_timeout_s(text, engine=engine) >= (
        engine._effective_recv_timeout_s(text) + 5
    )


def test_explicit_budget_wins_over_engine_figures(monkeypatch):
    monkeypatch.setattr(model_manager, "_GENERATE_TIMEOUT_EXPLICIT", True)
    monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 9.0)
    assert model_manager.generate_timeout_s(SHORT_TEXT, engine=make_engine("f")) == 9.0


# ── failure text ────────────────────────────────────────────────────────────

def test_failure_reason_distinguishes_crash_from_stall():
    engine = make_engine("g")
    engine._last_recv_timed_out = False
    assert "closed pipe mid-generate" in engine._generate_failure_reason(1, 600)
    engine._last_recv_timed_out = True
    stalled = engine._generate_failure_reason(601.2, 600)
    assert "600s" in stalled and "stopped it" in stalled and "closed pipe" not in stalled
    assert "601s" in stalled


# ── real child process ──────────────────────────────────────────────────────

_MUTE_CHILD = textwrap.dedent('''
    import json, struct, sys, time
    hello = json.dumps({"op": "ready", "sample_rate": 24000}).encode()
    sys.stdout.buffer.write(struct.pack("!I", len(hello)) + hello)
    sys.stdout.buffer.flush()
    print("child-says-hello", file=sys.stderr, flush=True)
    sys.stdin.buffer.read(4)
    time.sleep(3600)
''')


@pytest.fixture
def mute_engine(tmp_path):
    script = tmp_path / "mute_child.py"
    script.write_text(_MUTE_CHILD)
    engine = make_engine(
        "mute",
        venv_python=classmethod(lambda cls: Path(sys.executable)),
        sidecar_script=classmethod(lambda cls: script),
    )
    yield engine
    engine.shutdown()


def _exited_soon(proc, within=10.0):
    deadline = time.monotonic() + within
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            return True
        time.sleep(0.05)
    return False


def test_mute_sidecar_hits_deadline_with_diagnostics(mute_engine):
    type(mute_engine).recv_timeout_s = 1.5
    with pytest.raises(RuntimeError) as caught:
        mute_engine.generate(SHORT_TEXT)
    message = str(caught.value)
    assert "1.5s" in message
    assert "stopped it" in message
    assert "closed pipe" not in message
    assert "child-says-hello" in message


def test_asr_guard_timeout_takes_the_sidecar_down(mute_engine):
    from services.asr_backend import ASRTimeoutError, run_transcribe_guarded

    type(mute_engine).recv_timeout_s = 300.0

    async def go():
        with ThreadPoolExecutor(max_workers=1) as pool:
            with pytest.raises(ASRTimeoutError):
                await run_transcribe_guarded(
                    pool, lambda: mute_engine.generate(SHORT_TEXT), what="probe", timeout=1.5
                )

    asyncio.run(go())
    assert mute_engine._proc is None or _exited_soon(mute_engine._proc)


def test_tts_guard_timeout_takes_the_sidecar_down(mute_engine):
    type(mute_engine).recv_timeout_s = 300.0

    async def go():
        with pytest.raises(TimeoutError):
            await model_manager.run_on_gpu_pool_guarded(
                lambda: mute_engine.generate(SHORT_TEXT), what="probe", timeout=1.5
            )

    asyncio.run(go())
    assert mute_engine._proc is None or _exited_soon(mute_engine._proc)
