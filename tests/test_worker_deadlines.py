"""Deadline policy, including the drift guard against model_manager.

The original goal doc's 2s/30s/35s example was off by one to two orders of
magnitude for this product. These tests pin the corrected behaviour and, more
importantly, keep it tied to the single source of truth for execution budgets
so the two cannot silently diverge.
"""
from __future__ import annotations

import pytest

from worker import deadlines
from worker.deadlines import Deadlines, Operation, for_task


def test_execution_budget_matches_model_manager_exactly():
    """Drift guard.

    ``model_manager.generate_timeout_s`` is THE execution budget for local
    synthesis. If remote tasks computed their own, a change there would leave
    remote work being killed at a different time than local work — the class of
    bug that produced "exceeded 300s" reports when only two call sites were
    updated (#1190/#1202).
    """
    from services import model_manager

    for text in ("", "short", "x" * 1200, "x" * 5000, "x" * 50_000):
        assert deadlines._base_execution_seconds(text, execution_device="cpu") == pytest.approx(
            model_manager.generate_timeout_s(text, execution_device="cpu")
        )


def test_fallback_formula_matches_when_model_manager_is_unavailable(monkeypatch):
    """The control plane may run without torch on the path; the fallback must
    still agree with the real formula."""
    from services import model_manager

    real = model_manager.generate_timeout_s("x" * 4000, execution_device="cpu")

    def _boom(*a, **k):
        raise ImportError("no torch here")

    monkeypatch.setattr(model_manager, "generate_timeout_s", _boom)
    assert deadlines._base_execution_seconds("x" * 4000) == pytest.approx(real)


def test_torch_free_fallback_uses_explicit_target_and_custom_cpu(monkeypatch):
    from services import model_manager

    monkeypatch.setattr(model_manager, "generate_timeout_s", lambda *_a, **_k: (_ for _ in ()).throw(ImportError()))
    monkeypatch.setattr(deadlines, "_CPU_GENERATE_TIMEOUT_S", 733.0)
    monkeypatch.setattr(deadlines, "_GENERATE_TIMEOUT_S", 311.0)

    assert deadlines._base_execution_seconds("short", execution_device="cuda") == 311.0
    assert deadlines._base_execution_seconds("short", execution_device="cpu") == 733.0
    assert deadlines._base_execution_seconds("short", execution_device="unknown") == 733.0


def test_execution_budget_uses_target_worker_device(monkeypatch):
    """A CPU controller must not enlarge a remote CUDA worker's budget."""
    from services import model_manager

    monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 300.0)
    monkeypatch.setattr(model_manager, "CPU_JOB_TIMEOUT_S", 777.0)
    monkeypatch.setattr(model_manager, "_GENERATE_TIMEOUT_EXPLICIT", False)
    monkeypatch.setattr(model_manager, "_CONFIGURED_GPU_JOB_TIMEOUT_S", 300.0)

    assert for_task("tts", text="short", execution_device="cuda").execution_seconds == 300
    assert for_task("tts", text="short", execution_device="cpu").execution_seconds == 777


def test_universal_timeout_override_wins_on_cpu_worker(monkeypatch):
    """Operators can still lower the watchdog everywhere with ONE var — but
    only when they have NOT also set an explicit CPU-specific override; see
    test_cpu_specific_override_wins_over_universal_on_cpu_worker below
    (#1787 review fix, CodeRabbit/Greptile P1)."""
    from services import model_manager

    monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 444.0)
    monkeypatch.setattr(model_manager, "_GENERATE_TIMEOUT_EXPLICIT", True)

    assert for_task("tts", text="short", execution_device="cpu").execution_seconds == 444


def test_cpu_specific_override_wins_over_universal_on_cpu_worker(monkeypatch):
    """#1787 review fix: an explicit CPU-specific budget must win over the
    legacy universal override on the worker-deadlines path too (not just the
    local generate_timeout_s() call tested directly in
    test_generate_timeout_730.py) — otherwise a Settings save into the CPU
    row is silently ignored whenever the accelerated row is also set, which
    is the exact defect #1787 exists to remove."""
    from services import model_manager

    monkeypatch.setattr(model_manager, "GPU_JOB_TIMEOUT_S", 444.0)
    monkeypatch.setattr(model_manager, "_GENERATE_TIMEOUT_EXPLICIT", True)
    monkeypatch.setattr(model_manager, "CPU_JOB_TIMEOUT_S", 777.0)

    assert for_task("tts", text="short", execution_device="cpu").execution_seconds == 777


def test_accept_is_generous_enough_for_a_busy_worker():
    """The old 2s accept would time out against a worker mid-inference holding
    the GIL, then penalise it for being busy."""
    d = for_task("tts")
    assert d.accept_seconds >= 10


def test_cold_load_gets_far_more_than_the_old_30s():
    d = for_task("tts", model_resident=False, model_downloaded=False)
    assert d.model_load_seconds >= 1200


def test_resident_model_gets_a_short_load_budget():
    warm = for_task("tts", model_resident=True)
    cold = for_task("tts", model_resident=False, model_downloaded=True)
    undownloaded = for_task("tts", model_resident=False, model_downloaded=False)

    assert warm.model_load_seconds < cold.model_load_seconds < undownloaded.model_load_seconds


def test_execution_scales_with_input_length():
    short = for_task("tts", text="hello")
    long = for_task("tts", text="x" * 50_000)
    assert long.execution_seconds > short.execution_seconds


def test_dub_gets_far_longer_than_dictation():
    """Inference duration varies by orders of magnitude; one scheme cannot fit."""
    assert for_task("dub").execution_seconds > for_task("dictation").execution_seconds * 10


def test_long_operations_get_a_longer_grace_window():
    """Losing a 40-minute dub to a 45-second Wi-Fi blip and redoing it from
    zero is the expensive mistake."""
    assert for_task("dub").grace_seconds > for_task("dictation").grace_seconds


def test_reconnect_backoff_grace_and_progress_lease_are_strictly_ordered():
    from worker.transport.client import _MAX_BACKOFF_SECONDS

    for operation in Operation:
        budget = for_task(operation.value)
        assert _MAX_BACKOFF_SECONDS < budget.grace_seconds < budget.progress_lease_seconds


def test_media_length_scales_operations_measured_in_seconds():
    short = for_task("dub", input_seconds=10)
    long = for_task("dub", input_seconds=3600)
    assert long.execution_seconds > short.execution_seconds


def test_progress_lease_is_shorter_than_execution():
    """Silence is the failure signal, not slowness — so the lease must be able
    to fire well inside a long job."""
    d = for_task("dub")
    assert d.progress_lease_seconds < d.execution_seconds


def test_unknown_operation_falls_back_instead_of_raising():
    assert Operation.coerce("no-such-op") is Operation.TTS
    assert for_task("no-such-op").execution_seconds > 0


def test_deadlines_serialize_for_the_wire():
    payload = for_task("tts").to_dict()
    assert set(payload) == {
        "accept_seconds",
        "model_load_seconds",
        "execution_seconds",
        "progress_lease_seconds",
        "result_delivery_seconds",
        "grace_seconds",
    }
    assert all(isinstance(v, int) for v in payload.values())


def test_total_is_the_sum_of_the_phases():
    d = Deadlines(
        accept_seconds=1,
        model_load_seconds=2,
        execution_seconds=4,
        progress_lease_seconds=99,
        result_delivery_seconds=8,
        grace_seconds=99,
    )
    assert d.total_seconds == 15
