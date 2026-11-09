"""#1226 / #1222: a 4 GB card was told it was under-provisioned only *after*
waiting out the full compute budget.

Two users — GTX 1650 Ti (4 GB) and Quadro P2000 (4 GB) — ran the `omnivoice`
engine and got:

    TTS generate ran for more than 300s/372s of actual compute time and was
    abandoned … most often the GPU is VRAM-starved …

Everything about that is technically true and useless. The 300-vs-372 spread is
just text length (the budget is `300 + (len-1200)/40`, so 372s ⇒ ~4080 chars) —
the two reports are one bug. And until the moment it failed, routing showed a
clean green "accelerated": family membership was the ONLY thing anything
checked, so a 4 GB card and a 24 GB card were indistinguishable.

These tests pin the declared VRAM floor, the advisory it produces before the
user waits, and the after-the-fact message naming the actual card.
"""
from __future__ import annotations

from core.device_caps import KERNEL_RISK_MARKER, HostCaps
from services import model_manager
from services.engine_routing import resolve_routing, routing_notice
from services.tts_backend import OmniVoiceBackend, TTSBackend


def _gpu(vram_gb: float, name: str = "NVIDIA GeForce GTX 1650 Ti", notes=()) -> HostCaps:
    return HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        device_name=name,
        vram_gb=vram_gb,
        notes=tuple(notes),
    )


# ── the floor is declared ────────────────────────────────────────────────


def test_engines_declare_no_floor_by_default():
    assert TTSBackend.min_vram_gb == 0.0


def test_the_reported_engine_declares_a_floor():
    assert OmniVoiceBackend.min_vram_gb >= 6.0


# ── the user is warned BEFORE waiting ────────────────────────────────────


def test_a_4gb_card_gets_a_caveat_instead_of_a_clean_green():
    r = resolve_routing(("cuda", "mps", "cpu"), _gpu(4.0), OmniVoiceBackend.min_vram_gb)
    assert r["routing_status"] == "accelerated"  # it DOES run — advisory, not blocking
    reason = r["routing_reason"]
    assert reason and "4.0 GB" in reason
    assert "GTX 1650 Ti" in reason
    assert "lighter engine" in reason
    # routing_notice is what actually surfaces it to the user.
    assert routing_notice(r) == ("accelerated", reason)


def test_a_large_card_stays_silent():
    r = resolve_routing(("cuda", "mps", "cpu"), _gpu(24.0, "NVIDIA RTX 4090"),
                        OmniVoiceBackend.min_vram_gb)
    assert r["routing_reason"] is None
    assert routing_notice(r) is None


def test_an_engine_with_no_declared_floor_never_warns():
    """Only engines with a measured figure warn — inventing floors for the
    rest would put confident numbers in the UI that nothing backs."""
    r = resolve_routing(("cuda", "cpu"), _gpu(4.0))
    assert r["routing_reason"] is None


def test_mps_is_not_judged_by_a_cuda_measured_floor():
    """On MPS, HostCaps.vram_gb is a heuristic (system RAM / 2) for a UNIFIED
    memory pool. An 8 GB Mac therefore reports 4.0 "VRAM" — comparing that to a
    floor measured on discrete CUDA hardware would warn every small Mac about
    an engine that runs fine there. Different memory model, unmeasured floor."""
    caps = HostCaps(
        family="mps",
        available_families=("mps", "cpu"),
        device_name="Apple Silicon (MPS)",
        vram_gb=4.0,  # an 8 GB Mac
    )
    r = resolve_routing(("cuda", "mps", "cpu"), caps, OmniVoiceBackend.min_vram_gb)
    assert r["routing_status"] == "accelerated"
    assert r["routing_reason"] is None


def test_mps_timeout_message_is_not_a_vram_verdict(monkeypatch):
    caps = HostCaps(
        family="mps", available_families=("mps", "cpu"),
        device_name="Apple Silicon (MPS)", vram_gb=4.0,
    )
    msg = _guidance(monkeypatch, caps)
    assert "Apple Silicon" not in msg


def test_a_failed_vram_probe_does_not_guess():
    """vram_gb == 0 means the probe failed, not that the card has no memory."""
    r = resolve_routing(("cuda", "cpu"), _gpu(0.0), 6.0)
    assert r["routing_reason"] is None


def test_kernel_risk_still_outranks_the_vram_caveat():
    """A card that may not launch kernels at all is the more severe finding."""
    caps = _gpu(4.0, notes=(f"GPU (sm_120) not in this build's archs — {KERNEL_RISK_MARKER}",))
    r = resolve_routing(("cuda", "cpu"), caps, 6.0)
    assert KERNEL_RISK_MARKER in r["routing_reason"]


def test_the_matrix_payload_carries_the_floor():
    from services.tts_backend import list_backends

    entry = next(b for b in list_backends() if b["id"] == "omnivoice")
    assert entry["min_vram_gb"] == OmniVoiceBackend.min_vram_gb
    # Engines without a floor report null, not 0.0 — "unknown", not "none".
    assert all(b.get("min_vram_gb") != 0.0 for b in list_backends())


# ── the after-the-fact message names the actual card ─────────────────────


def _guidance(monkeypatch, caps, min_vram_gb=OmniVoiceBackend.min_vram_gb,
              what="TTS generate"):
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: caps)
    return model_manager._timeout_guidance(what, 300.0, min_vram_gb)


def test_timeout_message_names_the_small_card(monkeypatch):
    msg = _guidance(monkeypatch, _gpu(4.0))
    assert "GTX 1650 Ti" in msg
    assert "4.0 GB" in msg
    # It must not read as transient contention the user can flush away.
    assert "lighter engine" in msg


def test_timeout_message_unchanged_on_a_large_card(monkeypatch):
    msg = _guidance(monkeypatch, _gpu(24.0, "NVIDIA RTX 4090"))
    assert "VRAM-starved" in msg
    assert "RTX 4090" not in msg


def test_a_job_with_no_declared_floor_is_never_diagnosed_as_under_provisioned(
    monkeypatch,
):
    """`_timeout_guidance` serves EVERY job on the GPU pool — reference
    transcribe, stream assemble, watermarking, dub steps, and CPU-only engines
    running on a GPU host. Applying a VRAM verdict without knowing whose job it
    is would confidently misdiagnose most of them, so the caller must opt in by
    passing the engine's measured floor."""
    msg = _guidance(monkeypatch, _gpu(4.0), min_vram_gb=0.0,
                    what="Reference transcribe")
    assert "GTX 1650 Ti" not in msg
    assert "VRAM-starved" in msg  # the pre-existing generic GPU wording


def test_the_generate_call_sites_pass_the_engines_floor():
    """...and the TTS generate dispatches DO opt in — otherwise the branch
    above is unreachable in production.

    Each dispatch names the floor TWICE (#1804): once for the guard, whose
    message diagnoses the timeout after the fact, and once inside the budget the
    job is judged against. Only the first existed, so a 4 GB card could be told
    exactly why it timed out by a watchdog that had given it the fast-hardware
    budget in the first place."""
    import ast
    import inspect

    from api.routers import generation

    src = inspect.getsource(generation)
    tree = ast.parse(src)

    def _kw(call, name):
        return next((k for k in call.keywords if k.arg == name), None)

    def _names_floor(node) -> bool:
        return any(
            isinstance(n, ast.Name) and n.id == "_engine_min_vram_gb"
            for n in ast.walk(node)
        )

    dispatches = [
        call for call in ast.walk(tree)
        if isinstance(call, ast.Call)
        and (kw := _kw(call, "what")) is not None
        and isinstance(kw.value, ast.Constant)
        and kw.value.value == "TTS generate"
    ]
    # Four local dispatches remain: classic native/generic, streamed chunks,
    # and the shared local gateway. The removed fifth site was remote-only.
    assert len(dispatches) >= 4, "call-site scan found suspiciously few dispatches"

    # Per dispatch, not a global tally: counting occurrences lets one call site
    # drop both arguments while another gains an extra and the total still
    # matches (CodeRabbit, #1806).
    for call in dispatches:
        where = f"{generation.__name__}:{call.lineno}"
        guard = _kw(call, "min_vram_gb")
        assert guard is not None and _names_floor(guard.value), (
            f"{where}: the guard needs the engine's floor to name the card"
        )
        budget = _kw(call, "timeout")
        assert budget is not None and _names_floor(budget.value), (
            f"{where}: the BUDGET needs the engine's floor too, or a card that "
            f"renders slower than CPU is judged by the accelerated one (#1804)"
        )


def test_timeout_message_unchanged_on_cpu(monkeypatch):
    """#896: a CPU-only host must never be blamed on VRAM."""
    caps = HostCaps(family="cpu", available_families=("cpu",))
    msg = _guidance(monkeypatch, caps)
    assert "VRAM" not in msg
    assert "compute-bound" in msg


# ── the two reports really are one bug ───────────────────────────────────


def test_the_300_vs_372_second_spread_is_just_text_length():
    """#1226 saw 300s, #1222 saw 372s. If that difference were device-aware
    they'd be separate bugs; it is purely the length-scaled budget."""
    assert model_manager.generate_timeout_s("x" * 100, execution_device="cuda") == 300.0
    assert model_manager.generate_timeout_s("x" * 4080, execution_device="cuda") == 372.0
