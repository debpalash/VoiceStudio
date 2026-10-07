"""#2609: on a CPU-only host (6-core Ryzen, OmniVoice) a healthy render hit the
flat 600 s compute budget with the worker still computing. The default CPU
budget now scales with input length at CPU speed, stays bounded so a wedged
engine is still caught, leaves GPU behaviour alone, and an explicit setting
stays authoritative. The same arithmetic backs the torch-free worker fallback
and the MCP client timeout, so a remote worker / tool never gives up first.
"""
from __future__ import annotations

import asyncio
import importlib
import sys
import threading
import types

import pytest


@pytest.fixture
def gb():
    """Resolved at test run time so a stale sys.modules entry can't leak in."""
    import core.generate_budget as module
    return module


@pytest.fixture
def mm(monkeypatch):
    for name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(name), "__file__", None) is None:
            sys.modules.pop(name, None)
    for var in ("OMNIVOICE_GENERATE_TIMEOUT_S", "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    import services.model_manager as m
    m = importlib.reload(m)
    yield m
    # Tests may set the budget env vars; clear them BEFORE the reload so no
    # explicit-budget state leaks into later suites.
    for var in ("OMNIVOICE_GENERATE_TIMEOUT_S", "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"):
        monkeypatch.delenv(var, raising=False)
    importlib.reload(m)


def _host(monkeypatch, family):
    import core.device_caps as caps
    monkeypatch.setattr(
        caps, "detect_host_caps", lambda: types.SimpleNamespace(family=family)
    )


def test_cpu_default_budget_covers_a_paragraph(mm, monkeypatch, gb):
    """Fail-before: a 400-char paragraph got 600 s (+0 length bonus) on CPU."""
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("x" * 400) >= 400 * gb.CPU_SECONDS_PER_CHAR
    assert mm.generate_timeout_s("x" * 400) > 600.0 * 2


def test_cpu_short_text_keeps_the_floor(mm, monkeypatch):
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("hi") == 600.0


def test_cpu_default_budget_is_monotonic_and_never_below_legacy(mm, monkeypatch):
    _host(monkeypatch, "cpu")
    prev = 0.0
    for n in (0, 10, 150, 400, 1200, 3000, 5000, 50_000):
        b = mm.generate_timeout_s("x" * n)
        assert b >= prev
        assert b >= 600.0 + max(0, n - 1200) / 40.0
        prev = b


def test_cpu_default_budget_has_a_hard_cap(mm, monkeypatch, gb):
    """A wedged engine must still be caught in finite time: the automatic
    ceiling applies to the FINAL value, even where the legacy length bonus
    alone (13,070 s at 500k chars) would exceed it."""
    _host(monkeypatch, "cpu")
    for n in (5_000, 300_000, 500_000, 5_000_000):
        assert mm.generate_timeout_s("x" * n) <= gb.CPU_AUTO_CAP_S
    assert mm.generate_timeout_s("x" * 500_000) == gb.CPU_AUTO_CAP_S


def test_explicit_cpu_setting_is_not_capped(mm, monkeypatch, gb):
    """Only the AUTOMATIC budget is capped; an explicit value is authoritative."""
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "21000")
    mm = importlib.reload(mm)
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("x" * 500_000) == 21000.0 + (500_000 - 1200) / 40.0


def test_gpu_budget_unchanged(mm, monkeypatch):
    _host(monkeypatch, "cuda")
    assert mm.generate_timeout_s("x" * 400) == 300.0
    assert mm.generate_timeout_s("x" * 2400) == 300.0 + 30.0


def test_explicit_cpu_setting_stays_authoritative(mm, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "700")
    mm = importlib.reload(mm)
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("x" * 400) == 700.0
    assert mm.generate_timeout_s("x" * 2400) == 700.0 + 30.0


def test_explicit_universal_setting_stays_authoritative_on_cpu(mm, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "123")
    mm = importlib.reload(mm)
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("x" * 400) == 123.0


def test_runtime_changed_cpu_setting_is_explicit(mm, monkeypatch):
    monkeypatch.setattr(mm, "CPU_JOB_TIMEOUT_S", 999.0)
    _host(monkeypatch, "cpu")
    assert mm.generate_timeout_s("x" * 400) == 999.0


# ── guard-level: moderately long text must not trip, a wedge still does ─────

class _Render:
    """A fake render that confirms worker entry and can be released/joined.

    Pool threads outlive their job, so "joined" means the render body returned
    (``finished``), not that the thread exited.
    """

    def __init__(self, duration=None):
        self.entered = threading.Event()
        self.finished = threading.Event()
        self.release = threading.Event()
        self.duration = duration

    def __call__(self):
        self.entered.set()
        try:
            self.release.wait(30 if self.duration is None else self.duration)
            return "audio"
        finally:
            self.finished.set()

    def drain(self):
        self.release.set()
        if self.entered.is_set():
            assert self.finished.wait(5), "released worker did not finish"


def _run_with_entry_sync(mm, render, budget):
    """Run the guarded job; the budget clock only matters once a worker has
    entered ``render`` (the guard starts it at pickup), and entry is asserted."""
    async def go():
        task = asyncio.ensure_future(
            mm.run_on_gpu_pool_guarded(render, what="TTS generate", timeout=budget)
        )
        for _ in range(500):
            if render.entered.is_set():
                break
            await asyncio.sleep(0.01)
        assert render.entered.is_set(), "worker never entered the render"
        return await task

    return asyncio.run(go())


def test_slow_cpu_render_with_default_budget_completes(mm, monkeypatch):
    """A render slower than the OLD budget but inside the new one succeeds."""
    _host(monkeypatch, "cpu")
    # Scale time down 1000x: old budget 0.6 s, a 400-char text now gets > 1.6 s.
    old = 600.0 / 1000
    new = mm.generate_timeout_s("x" * 400) / 1000
    assert new > old

    ok = _Render(duration=old + 0.3)
    try:
        assert _run_with_entry_sync(mm, ok, new) == "audio"
    finally:
        ok.drain()

    slow = _Render(duration=old + 0.3)
    try:
        with pytest.raises(mm.GpuJobTimeoutError):
            _run_with_entry_sync(mm, slow, old)
    finally:
        slow.drain()


def test_wedged_job_is_still_caught(mm):
    wedged = _Render()
    try:
        with pytest.raises(mm.GpuJobTimeoutError):
            _run_with_entry_sync(mm, wedged, 0.3)
    finally:
        wedged.drain()


# ── the mirrors must agree with the real budget ──────────────────────────────

@pytest.mark.parametrize("n", [0, 50, 400, 1200, 5000, 100_000])
def test_worker_fallback_matches_model_manager(mm, monkeypatch, n):
    from worker import deadlines

    _host(monkeypatch, "cpu")
    real = mm.generate_timeout_s("x" * n, execution_device="cpu")
    monkeypatch.setattr(
        mm, "generate_timeout_s",
        lambda *a, **k: (_ for _ in ()).throw(ImportError("no torch")),
    )
    monkeypatch.setattr(deadlines, "_CPU_GENERATE_TIMEOUT_S", 600.0)
    assert deadlines._base_execution_seconds("x" * n) == pytest.approx(real)


@pytest.mark.parametrize("raw", [
    "hi", "x" * 400, "x" * 5_000,
    "123456 " * 40,          # digits-heavy: normalization expands ~10x
    "999999 " * 700,
])
def test_every_client_waits_at_least_as_long_as_the_backend(mm, monkeypatch, raw):
    """One rule, identical inputs: the MCP wait is >= the backend budget for the
    text the backend ACTUALLY budgets (after real normalization), however much
    that expands."""
    for v in ("OMNIVOICE_MCP_TIMEOUT_S", "OMNIVOICE_GENERATE_TIMEOUT_S",
              "OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "OMNIVOICE_GPU_QUEUE_TIMEOUT_S"):
        monkeypatch.delenv(v, raising=False)
    import mcp_server
    from services.text_normalization import normalize_for_tts

    _host(monkeypatch, "cpu")
    expanded = normalize_for_tts(raw, "en")
    if "123456" in raw or "999999" in raw:
        assert len(expanded) > 4 * len(raw)  # the pathological case is real
    backend = mm.GPU_QUEUE_TIMEOUT_S + mm.generate_timeout_s(expanded, execution_device="cpu")
    assert mcp_server._post_timeout_s("generate", raw) > backend


def _route(monkeypatch, family, gpu_compat):
    """A host of ``family`` whose active engine declares ``gpu_compat``."""
    import core.device_caps as caps
    import services.tts_backend as tb

    host = caps.HostCaps(family=family, available_families=tuple({family, "cpu"}))
    engine = type("RoutedEngine", (), {"gpu_compat": gpu_compat, "min_vram_gb": 0.0})
    monkeypatch.setattr(caps, "detect_host_caps", lambda: host)
    monkeypatch.setattr(tb, "active_backend_id", lambda: "routed")
    monkeypatch.setattr(tb, "get_backend_class", lambda _id: engine)


def test_ceiling_is_reported_for_cpu_hosts(mm, monkeypatch, gb):
    _route(monkeypatch, "cpu", ("cpu",))
    assert mm.generate_budget_s()["cpuAutoCeiling"] == gb.CPU_AUTO_CAP_S


@pytest.mark.parametrize("family,compat", [
    ("cuda", ("cpu",)),            # CPU-only engine on a GPU host
    ("rocm", ("cuda", "cpu")),     # engine without a ROCm path -> CPU fallback
])
def test_ceiling_is_reported_when_a_gpu_host_routes_to_cpu(mm, monkeypatch, gb, family, compat):
    """Fail-before: the budget was keyed on host family only, so a job the
    backend budgets on the CPU rule got a zero ceiling and the UI backstop
    could abort it inside its 7200 s budget."""
    _route(monkeypatch, family, compat)
    assert mm.generate_timeout_s("x" * 5_000, execution_device="cpu") == gb.CPU_AUTO_CAP_S
    assert mm.generate_budget_s()["cpuAutoCeiling"] == gb.CPU_AUTO_CAP_S


def test_ceiling_is_zero_for_gpu_routed_jobs(mm, monkeypatch):
    _route(monkeypatch, "cuda", ("cuda", "cpu"))
    assert mm.generate_budget_s()["cpuAutoCeiling"] == 0.0


def test_ceiling_follows_the_requested_engine(mm, monkeypatch, gb):
    """?engine= reports the route of THAT engine, not only the active one."""
    import core.device_caps as caps
    import services.tts_backend as tb

    _route(monkeypatch, "cuda", ("cuda", "cpu"))
    cpu_engine = type("Cpu", (), {"gpu_compat": ("cpu",), "min_vram_gb": 0.0})
    gpu_engine = type("Gpu", (), {"gpu_compat": ("cuda", "cpu"), "min_vram_gb": 0.0})
    monkeypatch.setattr(
        tb, "get_backend_class", lambda eid: cpu_engine if eid == "cpu-eng" else gpu_engine
    )
    assert mm.generate_budget_s("cpu-eng")["cpuAutoCeiling"] == gb.CPU_AUTO_CAP_S
    assert mm.generate_budget_s("gpu-eng")["cpuAutoCeiling"] == 0.0


def test_ceiling_is_zero_with_an_explicit_budget(mm, monkeypatch):
    _route(monkeypatch, "cpu", ("cpu",))
    monkeypatch.setattr(mm, "CPU_JOB_TIMEOUT_S", 999.0)  # explicit CPU budget
    assert mm.generate_budget_s()["cpuAutoCeiling"] == 0.0


def test_explicit_cpu_budget_drops_the_client_ceiling(gb):
    legacy = gb.client_execution_budget_s(900.0, 20, cpu_auto_possible=False)
    assert legacy == 900.0
    assert gb.client_execution_budget_s(900.0, 20) == gb.CPU_AUTO_CAP_S


# ── the message names the concrete fix for a CPU user ────────────────────────

def test_cpu_timeout_message_names_concrete_remedies():
    from core.public_errors import stream_failure

    cpu = stream_failure("generation_timeout", device="cpu")
    assert cpu["code"] == "generation_timeout" and cpu["retryable"] is True
    for needle in ("CPU budget", "Settings → Performance & Device",
                   "Supertonic-3", "shorter", "restart"):
        assert needle in cpu["detail"]
    gpu = stream_failure("generation_timeout", device="cuda")
    assert gpu == stream_failure("generation_timeout")
    assert "CPU budget" not in gpu["detail"]


# ── explicit budgets: the legacy length bonus must also cover prepared text ──

_DIGIT_HEAVY = ["999999 " * 400, "123456 " * 700, "$999999 " * 300]


def test_normalizer_never_outgrows_the_client_expansion_factor(gb):
    """Guard: the factor clients size their wait with must stay above what the
    normalizer can do to ordinary text (worst measured ~11.5x)."""
    from services.text_normalization import normalize_for_tts

    for raw in _DIGIT_HEAVY + ["999999", "1999 2024 $9 9% 3:45"]:
        for lang in ("en", "fr", "es", "de"):
            assert len(normalize_for_tts(raw, lang)) <= gb.TEXT_EXPANSION_FACTOR * len(raw)


@pytest.mark.parametrize("raw", _DIGIT_HEAVY, ids=["999999x400", "123456x700", "usd999999x300"])
@pytest.mark.parametrize("cpu_budget", ["900", "7000"])
def test_mcp_wait_covers_prepared_text_with_an_explicit_budget(mm, monkeypatch, raw, cpu_budget):
    """Fail-before: with an explicit CPU budget the MCP wait sized the length
    bonus from 4x the typed length; real normalization expands digits ~11x."""
    for v in ("OMNIVOICE_MCP_TIMEOUT_S", "OMNIVOICE_GENERATE_TIMEOUT_S",
              "OMNIVOICE_GPU_QUEUE_TIMEOUT_S"):
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", cpu_budget)
    mm = importlib.reload(mm)
    import mcp_server
    from services.text_normalization import normalize_for_tts

    _host(monkeypatch, "cpu")
    expanded = normalize_for_tts(raw, "en")
    assert len(expanded) > 4 * len(raw)
    backend = mm.GPU_QUEUE_TIMEOUT_S + mm.generate_timeout_s(expanded, execution_device="cpu")
    assert mcp_server._post_timeout_s("generate", raw) > backend
