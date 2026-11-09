"""Unit tests for backend/services/engine_routing.py (GPU compat matrix, PR 1).

``resolve_routing`` is a pure function over ``(gpu_compat, HostCaps)`` — these
tests construct ``HostCaps`` directly (no torch) and assert every rule + edge
from spec §2, plus the cross-OS determinism + never-emits-"n/a" guarantees.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from core.device_caps import DIRECTML_MARKER, KERNEL_RISK_MARKER, HostCaps
from services.engine_routing import (
    header_safe_reason,
    resolve_routing,
    routing_notice,
)


def _caps(family, *, notes=(), available=None):
    if available is None:
        available = (family, "cpu") if family != "cpu" else ("cpu",)
    return HostCaps(family=family, available_families=available, notes=tuple(notes))


# ── Rule 2: accelerated ──────────────────────────────────────────────────
def test_cuda_host_cuda_engine_accelerated():
    r = resolve_routing(("cuda", "cpu"), _caps("cuda"))
    assert r == {"effective_device": "cuda", "routing_status": "accelerated",
                 "routing_reason": None}


def test_mps_host_mps_engine_accelerated():
    r = resolve_routing(("mps", "cpu"), _caps("mps"))
    assert r["routing_status"] == "accelerated"
    assert r["effective_device"] == "mps"


def test_cuda_kernel_mismatch_falls_back_to_cpu(monkeypatch):
    # #3067: an sm_52 card on a sm_70+ wheel. The loader falls back to CPU, so
    # the badge must too instead of a green "accelerated".
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    note = f"Quadro M5000M (sm_52) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda", "cpu"), _caps("cuda", notes=[note]))
    assert r["routing_status"] == "cpu_fallback"
    assert r["effective_device"] == "cpu"
    assert KERNEL_RISK_MARKER in r["routing_reason"]
    assert "OMNIVOICE_FORCE_CUDA" in r["routing_reason"]


def test_cuda_kernel_mismatch_without_cpu_path_keeps_caveat(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    note = f"GPU (sm_52) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda",), _caps("cuda", notes=[note]))
    assert r["routing_status"] == "accelerated"
    assert KERNEL_RISK_MARKER in r["routing_reason"]


def test_rocm_kernel_mismatch_stays_a_caveat(monkeypatch):
    # The Linux ROCm loader may still remap with HSA_OVERRIDE_GFX_VERSION.
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    note = f"GPU (gfx1201) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda", "rocm", "cpu"), _caps("rocm", notes=[note]))
    assert r["routing_status"] == "accelerated"


def test_accelerated_with_kernel_risk_caveat(monkeypatch):
    # OMNIVOICE_FORCE_CUDA=1: the loader uses the GPU anyway, so the badge
    # stays accelerated with the kernel-launch caveat.
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    note = f"GPU (sm_120) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda", "cpu"), _caps("cuda", notes=[note]))
    assert r["routing_status"] == "accelerated"
    assert r["routing_reason"] is not None
    assert r["routing_reason"].startswith("CUDA selected, but:")
    assert KERNEL_RISK_MARKER in r["routing_reason"]


def test_accelerated_ignores_advisory_notes():
    # Multi-GPU / VRAM advisory notes must NOT downgrade the accelerated badge.
    r = resolve_routing(("cuda", "cpu"),
                        _caps("cuda", notes=["3 GPUs detected; routing reflects device 0"]))
    assert r["routing_status"] == "accelerated"
    assert r["routing_reason"] is None


# ── Rule 3: cpu-native engine (gpu_compat == ("cpu",)) is neutral anywhere ──
# A cpu-native engine (kittentts / moonshine / sherpa) has nothing to fall back
# FROM, so an accelerator host must NOT warn-tone it as a "CPU fallback" — it's
# benign cpu_only on ANY host. Regression for the badge-tone bug (#…).
def test_cpu_native_engine_neutral_on_mps_host():
    r = resolve_routing(("cpu",), _caps("mps"))
    assert r == {"effective_device": "cpu", "routing_status": "cpu_only",
                 "routing_reason": None}


def test_cpu_native_engine_neutral_on_cuda_host():
    # Was mis-classed cpu_fallback (warn) before the fix; now cpu_only (neutral).
    r = resolve_routing(("cpu",), _caps("cuda"))
    assert r["routing_status"] == "cpu_only"
    assert r["routing_reason"] is None


def test_cpu_native_engine_neutral_on_cpu_host():
    # A cpu host already routed cpu-native → cpu_only; unchanged by the new rule.
    r = resolve_routing(("cpu",), _caps("cpu"))
    assert r["routing_status"] == "cpu_only"


# ── Rule 4: cpu_fallback (the no-silent-fallback signal) ──────────────────
# Only a MULTI-target engine that genuinely could accelerate elsewhere but
# lacks THIS host's accelerator falls back (and warns).
def test_cuda_host_engine_without_cuda_path_falls_back():
    r = resolve_routing(("mps", "cpu"), _caps("cuda"))
    assert r["routing_status"] == "cpu_fallback"
    assert r["effective_device"] == "cpu"
    assert "no CUDA path" in r["routing_reason"]


def test_rocm_host_cuda_only_engine_falls_back_with_specific_reason():
    r = resolve_routing(("cuda", "cpu"), _caps("rocm"))
    assert r["routing_status"] == "cpu_fallback"
    assert r["routing_reason"] == "declares CUDA only; ROCm not in its compat set"


def test_xpu_host_cpu_engine_falls_back():
    r = resolve_routing(("cuda", "cpu"), _caps("xpu"))
    assert r["routing_status"] == "cpu_fallback"
    assert "no XPU path" in r["routing_reason"]


# ── Rule 5: cpu_only (benign) ─────────────────────────────────────────────
def test_cpu_host_cpu_engine_is_neutral():
    r = resolve_routing(("cuda", "cpu"), _caps("cpu"))
    assert r == {"effective_device": "cpu", "routing_status": "cpu_only",
                 "routing_reason": None}


def test_directml_host_gets_explanatory_reason_but_stays_neutral():
    note = f"{DIRECTML_MARKER} (Windows GPU); torch-family probe treats as non-accelerated"
    r = resolve_routing(("cuda", "cpu"), _caps("cpu", notes=[note]))
    assert r["routing_status"] == "cpu_only"   # never blocks
    assert "DirectML" in r["routing_reason"]


# ── Rule 6: unavailable ───────────────────────────────────────────────────
def test_cpu_host_gpu_only_engine_unavailable():
    r = resolve_routing(("cuda",), _caps("cpu"))
    assert r["routing_status"] == "unavailable"
    assert r["effective_device"] == "cuda"
    assert "requires cuda" in r["routing_reason"]
    assert "this host has cpu" in r["routing_reason"]


def test_xpu_host_gpu_only_no_cpu_unavailable():
    r = resolve_routing(("cuda", "mps"), _caps("xpu"))
    assert r["routing_status"] == "unavailable"


# ── Rule 1: defensive empty compat ────────────────────────────────────────
def test_empty_compat_is_defensive_cpu_only():
    r = resolve_routing((), _caps("cuda"))
    assert r["routing_status"] == "cpu_only"
    assert r["effective_device"] == "cpu"


# ── Contract guarantees ───────────────────────────────────────────────────
def test_never_emits_n_a():
    for fam in ("cuda", "rocm", "mps", "xpu", "npu", "cpu"):
        for compat in ((), ("cpu",), ("cuda",), ("cuda", "cpu"), ("mps", "cpu"), ("npu", "cpu")):
            assert resolve_routing(compat, _caps(fam))["routing_status"] != "n/a"


def test_deterministic_across_calls():
    caps = _caps("rocm")
    assert resolve_routing(("cuda", "cpu"), caps) == resolve_routing(("cuda", "cpu"), caps)


def test_runtime_compute_profile_async_keeps_event_loop_responsive():
    from services.engine_routing import runtime_compute_profile_async

    release = threading.Event()
    finished = threading.Event()
    event_loop_progressed = asyncio.Event()
    order: list[str] = []

    class BlockingRuntimeProfile:
        @classmethod
        def runtime_compute_profile(cls, caps):
            try:
                assert release.wait(timeout=1.0)
                order.append("profile")
                return {"marker": caps.family}
            finally:
                finished.set()

    async def release_after_event_loop_progress():
        order.append("event_loop")
        event_loop_progressed.set()
        release.set()

    loop = asyncio.new_event_loop()
    try:
        profile_task = loop.create_task(
            runtime_compute_profile_async(BlockingRuntimeProfile, _caps("cpu")),
        )
        release_task = loop.create_task(release_after_event_loop_progress())
        loop.run_until_complete(event_loop_progressed.wait())
        loop.run_until_complete(release_task)
        assert finished.wait(timeout=1.0)

        assert loop.run_until_complete(profile_task) == {"marker": "cpu"}
        assert order == ["event_loop", "profile"]
    finally:
        loop.close()


def test_reason_str_for_fallback_and_unavailable_none_for_clean():
    # A genuine fallback (multi-target engine lacking the host accel) carries a
    # reason; a cpu-native ("cpu",) engine is neutral (covered above).
    assert resolve_routing(("mps", "cpu"), _caps("cuda"))["routing_reason"] is not None
    assert resolve_routing(("cuda",), _caps("cpu"))["routing_reason"] is not None
    assert resolve_routing(("cuda", "cpu"), _caps("cuda"))["routing_reason"] is None


# ── routing_notice (synth-time surfacing decision) ──────────────────────────
def test_notice_emitted_for_cpu_fallback():
    r = resolve_routing(("mps", "cpu"), _caps("cuda"))
    n = routing_notice(r)
    assert n is not None and n[0] == "cpu_fallback" and n[1]


def test_notice_emitted_for_accelerated_with_caveat(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    note = f"sm_120 not in build — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda", "cpu"), _caps("cuda", notes=[note]))
    n = routing_notice(r)
    assert n is not None and n[0] == "accelerated"


def test_no_notice_for_clean_accelerated_or_cpu_only():
    assert routing_notice(resolve_routing(("cuda", "cpu"), _caps("cuda"))) is None
    assert routing_notice(resolve_routing(("cuda", "cpu"), _caps("cpu"))) is None


# ── header_safe_reason (latin-1/length/scrub safety) ────────────────────────
def test_header_reason_none_for_empty():
    assert header_safe_reason(None) is None
    assert header_safe_reason("") is None


def test_header_reason_strips_non_ascii():
    # Latin-1 accents + em-dash — all non-ASCII, must be stripped (no CJK so the
    # hardcoded-CJK guard stays out of it).
    out = header_safe_reason("CUDA on café — dríver naïve")
    assert out == out.encode("ascii").decode("ascii")  # round-trips as pure ASCII
    assert "é" not in out and "—" not in out


def test_header_reason_strips_control_chars_no_header_injection():
    # A CR/LF (or tab) must never survive into a header value.
    out = header_safe_reason("ok\r\nX-Injected: evil\tmore")
    assert "\r" not in out and "\n" not in out and "\t" not in out
    assert "X-Injected" in out  # text remains; only the control chars are gone


def test_header_reason_capped_at_256():
    assert len(header_safe_reason("x" * 500)) == 256


def test_header_reason_scrubs_home_path():
    out = header_safe_reason("failed at /home/alice/model")
    assert "/home/alice" not in out and "~" in out


def test_npu_compatible_engine_uses_accelerated_route():
    assert resolve_routing(("npu", "cpu"), _caps("npu")) == {
        "effective_device": "npu", "routing_status": "accelerated", "routing_reason": None,
    }


def test_sidecar_engine_kernel_mismatch_stays_a_caveat(monkeypatch):
    # Review on #3553: a sidecar runs its own torch and never gets the parent
    # loader's CPU fallback, so it must not be badged cpu_fallback.
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    note = f"GPU (sm_52) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    r = resolve_routing(("cuda", "cpu"), _caps("cuda", notes=[note]), parent_torch=False)
    assert r["routing_status"] == "accelerated"
    assert KERNEL_RISK_MARKER in r["routing_reason"]


@pytest.mark.parametrize("value", [None, "", "0", "1", "true", " Yes ", "on", "off", "no"])
def test_badge_and_loader_read_force_cuda_alike(monkeypatch, value):
    from core.device_caps import cuda_forced
    from services.model_manager import _env_flag

    if value is None:
        monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", value)
    assert cuda_forced() == _env_flag("OMNIVOICE_FORCE_CUDA")


def test_same_interpreter_sidecar_gets_the_loader_cpu_fallback(monkeypatch):
    # omnivoice-subprocess runs the parent's interpreter and its child calls
    # get_best_device(), which picks CPU for a card the wheel cannot drive. Its
    # profile must say so, or CPU synthesis gets the accelerated time budget.
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    note = f"Quadro M5000M (sm_52) not in this torch build's archs — {KERNEL_RISK_MARKER}"
    profile = OmniVoiceSubprocessBackend.runtime_compute_profile(_caps("cuda", notes=[note]))
    assert profile["routing_status"] == "cpu_fallback"
    assert profile["effective_device"] == "cpu"


def test_uses_parent_torch_by_interpreter_not_isolation():
    from services.engine_routing import uses_parent_torch

    class InProcess:
        pass

    class SharedSidecar:
        _is_subprocess_isolated = True

        @classmethod
        def shares_parent_torch(cls):
            return True

    class OwnVenvSidecar:
        _is_subprocess_isolated = True

        @classmethod
        def shares_parent_torch(cls):
            return False

    class BrokenProbe:
        _is_subprocess_isolated = True

        @classmethod
        def shares_parent_torch(cls):
            raise RuntimeError("boom")

    assert uses_parent_torch(InProcess)
    assert uses_parent_torch(SharedSidecar)
    assert not uses_parent_torch(OwnVenvSidecar)
    assert not uses_parent_torch(BrokenProbe)
