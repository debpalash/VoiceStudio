"""Pure, host-aware routing resolver — maps an engine's declared ``gpu_compat``
against the cached host capabilities to "where will this engine *actually* run
on this machine, and is that a problem the user should hear about?"

No model load, no probe (the caller passes the cached ``HostCaps``), no I/O.
Deterministic and byte-identical for a given ``(gpu_compat, HostCaps)`` across
macOS/Windows/Linux — that cross-OS determinism is the whole point of the
no-silent-fallback contract.

Reason strings are author-controlled English (interpolating only family/device
names) but are **still** scrubbed by the caller (``core.scrub.scrub_text``)
before serialization, because an interpolated ``device_name`` or probe note can
carry a home path.
"""
from __future__ import annotations

import asyncio
from typing import Literal, TypedDict

from core.device_caps import (
    DIRECTML_MARKER,
    KERNEL_RISK_MARKER,
    UNUSABLE_GPU_MARKER,
    HostCaps,
)

RoutingStatus = Literal["accelerated", "cpu_fallback", "cpu_only", "unavailable", "n/a"]


class RoutingResult(TypedDict):
    effective_device: str          # a DeviceFamily value or "cpu"
    routing_status: RoutingStatus  # resolve_routing never emits "n/a" (LLM-only)
    routing_reason: str | None     # raw, pre-scrub


def runtime_compute_profile(engine_or_cls, caps: HostCaps) -> dict:
    """Return one engine's runtime-aware compute contract.

    Native executables may discover providers independently of PyTorch.  They
    override ``runtime_compute_profile``; all existing engines retain the
    exact static routing contract.
    """
    hook = getattr(engine_or_cls, "runtime_compute_profile", None)
    if callable(hook):
        return hook(caps)
    cls = engine_or_cls if isinstance(engine_or_cls, type) else type(engine_or_cls)
    compat = tuple(getattr(cls, "gpu_compat", ("cpu",)))
    floor = float(getattr(cls, "min_vram_gb", 0.0) or 0.0)
    return {
        "gpu_compat": compat,
        "min_vram_gb": floor,
        **resolve_routing(compat, caps, floor),
        "runtime_backend": None,
        "runtime_device_index": None,
        "runtime_device_name": None,
        "runtime_hardware_family": None,
        "runtime_vram_gb": None,
        "runtime_device_verified": None,
    }


async def runtime_compute_profile_async(engine_or_cls, caps: HostCaps) -> dict:
    """Resolve runtime compute metadata without blocking the event loop."""
    return await asyncio.to_thread(runtime_compute_profile, engine_or_cls, caps)


def under_provisioned_vram(
    caps: HostCaps,
    min_vram_gb: float = 0.0,
    *,
    family: str | None = None,
    vram_gb: float | None = None,
) -> bool:
    """Is this host's DEDICATED VRAM below the engine's declared floor?

    The one definition of "under-provisioned", shared by everything that acts
    on the verdict: the routing caveat below, the timeout guidance, and — since
    #1804 — the compute-time budget itself (``model_manager
    .generate_timeout_s``). It was written out inline in each of them, which is
    how the budget came to disagree with the warning printed next to it.

    Dedicated-VRAM families ONLY. CUDA, ROCm, XPU, and a native Vulkan device
    report dedicated memory. On MPS, ``HostCaps.vram_gb`` is a heuristic
    (system RAM / 2, see device_caps) for a UNIFIED memory pool; comparing it
    against a floor measured on discrete CUDA hardware would tell every 8 GB Mac
    its 4 GB "VRAM" is too small for an engine that runs fine there. A VRAM
    figure of 0 means the probe failed — don't guess from it. A floor of 0 means
    the engine declares none, and inventing one is worse than staying quiet.
    """
    if not min_vram_gb or min_vram_gb <= 0:
        return False
    if (family or getattr(caps, "family", None)) not in (
        "cuda", "rocm", "xpu", "vulkan",
    ):
        return False
    raw_vram_gb = getattr(caps, "vram_gb", 0.0) if vram_gb is None else vram_gb
    available_vram_gb = float(raw_vram_gb or 0.0)
    return 0 < available_vram_gb < float(min_vram_gb)


def low_vram_caveat(
    caps: HostCaps,
    min_vram_gb: float = 0.0,
    *,
    family: str | None = None,
    vram_gb: float | None = None,
) -> str | None:
    """User-facing advisory for a known under-provisioned dedicated GPU."""
    if not under_provisioned_vram(
        caps, min_vram_gb, family=family, vram_gb=vram_gb,
    ):
        return None
    device = caps.device_name or (family or caps.family).upper()
    available_vram_gb = caps.vram_gb if vram_gb is None else vram_gb
    return (
        f"{device} has {available_vram_gb:.1f} GB VRAM; this engine wants about "
        f"{min_vram_gb:.0f} GB. It will run, but expect slow generations "
        f"that may time out. Unload other models before generating, keep "
        f"the text short, or pick a lighter engine."
    )


def _caveat(caps: HostCaps, min_vram_gb: float = 0.0) -> str | None:
    """A caveat string for an otherwise-accelerated host, or None.

    Two kinds, kernel risk first (it's the more severe):

    * a driver/arch mismatch that may fail at kernel launch;
    * (#1226/#1222) a GPU that will run, but has less VRAM than the engine
      declares it needs. Two users on 4 GB cards ran the ``omnivoice`` engine
      and only learned their hardware was under-provisioned AFTER waiting out
      the full compute budget and being told the job "was too heavy". Routing
      showed a clean green "accelerated" throughout, because family membership
      was the only thing checked. Advisory, not blocking — the driver can page
      to system RAM, and short inputs fit where long ones don't.

    Advisory probe notes (multi-GPU, VRAM-query-failed, DirectML) never
    qualify. A VRAM figure of 0 means the probe failed; don't guess from it.
    """
    for note in caps.notes:
        if KERNEL_RISK_MARKER in note:
            return f"{caps.family.upper()} selected, but: {note}"
    return low_vram_caveat(caps, min_vram_gb)


def resolve_routing(
    gpu_compat: tuple[str, ...],
    caps: HostCaps,
    min_vram_gb: float = 0.0,
) -> RoutingResult:
    """Resolve the effective device + status for an engine on this host.

    Rules are evaluated in order; the first match wins (see spec §2).
    ``min_vram_gb`` is the engine's declared VRAM floor (``TTSBackend
    .min_vram_gb``); 0 disables the under-provisioned-GPU caveat. Optional so
    every existing caller keeps its exact behaviour."""
    targets = tuple(gpu_compat or ())
    fam = caps.family

    # 1. Empty compat — reserved for LLM (which never calls this). Defensive.
    if not targets:
        return {
            "effective_device": "cpu",
            "routing_status": "cpu_only",
            "routing_reason": "engine declares no compute targets",
        }

    # 2. Host accelerator is one the engine supports → accelerated.
    if fam != "cpu" and fam in targets:
        return {
            "effective_device": fam,
            "routing_status": "accelerated",
            "routing_reason": _caveat(caps, min_vram_gb),
        }

    # 3. CPU-native engine (declares ONLY cpu) has nothing to fall back FROM,
    #    so on ANY accelerator host it is benign cpu_only (neutral), never a
    #    warn-tone "CPU fallback". This must precede the fallback rule below —
    #    a ("cpu",) engine matches `"cpu" in targets` too, and would otherwise
    #    be mis-classed cpu_fallback on a GPU/MPS host. (A cpu host reaches
    #    rule 5 unchanged, keeping its DirectML note.) Engines that *could*
    #    accelerate elsewhere (e.g. ("cuda", "cpu")) are untouched.
    if fam != "cpu" and targets == ("cpu",):
        return {
            "effective_device": "cpu",
            "routing_status": "cpu_only",
            "routing_reason": None,
        }

    # 4. Host has an accelerator the engine lacks, but engine supports cpu
    #    → the no-silent-fallback signal.
    if fam != "cpu" and "cpu" in targets:
        if fam == "rocm" and "cuda" in targets and "rocm" not in targets:
            reason = "declares CUDA only; ROCm not in its compat set"
        else:
            reason = f"engine has no {fam.upper()} path; running on CPU"
        return {
            "effective_device": "cpu",
            "routing_status": "cpu_fallback",
            "routing_reason": reason,
        }

    # 5. Genuine CPU-only host (or DirectML, which the probe reports as cpu)
    #    and engine supports cpu → benign; must not warn or block.
    if fam == "cpu" and "cpu" in targets:
        # A GPU the OS can see but this torch build cannot drive (a Radeon on
        # the NVIDIA-CUDA wheel): an engine that WOULD use a GPU elsewhere is
        # a genuine fallback, not a CPU-native engine on a GPU-less machine.
        if targets != ("cpu",):
            for note in caps.notes:
                if UNUSABLE_GPU_MARKER in note:
                    return {
                        "effective_device": "cpu",
                        "routing_status": "cpu_fallback",
                        "routing_reason": note,
                    }
        reason = None
        for note in caps.notes:
            if DIRECTML_MARKER in note:
                reason = (
                    "DirectML GPU present; engine routes via torch CPU path "
                    "(DirectML acceleration not wired into routing)"
                )
                break
        return {
            "effective_device": "cpu",
            "routing_status": "cpu_only",
            "routing_reason": reason,
        }

    # 6. Engine needs an accelerator this host lacks and has no cpu path.
    first = targets[0]
    return {
        "effective_device": first,
        "routing_status": "unavailable",
        "routing_reason": f"requires {', '.join(targets)}; this host has {fam}",
    }


def routing_notice(result: RoutingResult) -> tuple[str, str | None] | None:
    """`(status, reason)` when a synth-time notice SHOULD be surfaced to the
    user, else `None`. Surfaced for `cpu_fallback` (always) and for
    `accelerated` ONLY when it carries a driver/arch caveat reason — everything
    else (`cpu_only`, clean `accelerated`, `n/a`) is benign and stays silent."""
    st = result["routing_status"]
    if st == "cpu_fallback" or (st == "accelerated" and result["routing_reason"]):
        return (st, result["routing_reason"])
    return None


def header_safe_reason(reason: str | None) -> str | None:
    """A routing reason made safe for an HTTP header value: scrubbed, then
    ASCII-sanitized (headers are latin-1; a non-ASCII device name would 500 the
    response otherwise), **control characters stripped** (a CR/LF could split
    the header / inject a new one), and length-capped at 256. Returns None for
    an empty reason. No regex — `.encode`/membership only (CodeQL-clean)."""
    if not reason:
        return None
    from core.scrub import scrub_text
    ascii_only = scrub_text(reason).encode("ascii", "ignore").decode("ascii")
    # Drop ASCII control chars (0x00-0x1F + DEL 0x7F) — incl. CR/LF, so the
    # value can never break out of its header line.
    cleaned = "".join(c for c in ascii_only if 0x20 <= ord(c) < 0x7F)
    return cleaned[:256] or None


def routing_fields(
    gpu_compat: tuple[str, ...],
    caps: HostCaps,
    min_vram_gb: float = 0.0,
) -> dict:
    """The three serialization-ready routing keys for a ``list_backends`` entry.

    Resolves routing and applies the redaction contract: ``routing_reason`` is
    scrubbed via ``core.scrub.scrub_text`` only when truthy, so a ``None`` reason
    serializes as JSON ``null`` (NOT ``""`` — ``scrub_text(None)`` would coerce
    to ``""``). Used by tts/asr ``list_backends`` so the scrub rule lives in one
    place. (LLM emits its own literal ``network``/``n/a``/``null`` fields and
    does NOT call this.)
    """
    from core.scrub import scrub_text

    r = resolve_routing(tuple(gpu_compat or ()), caps, min_vram_gb)
    reason = r["routing_reason"]
    return {
        "effective_device": r["effective_device"],
        "routing_status": r["routing_status"],
        "routing_reason": scrub_text(reason) if reason else None,
    }


__all__ = [
    "RoutingStatus", "RoutingResult", "resolve_routing", "routing_fields",
    "routing_notice", "header_safe_reason", "low_vram_caveat",
    "runtime_compute_profile", "runtime_compute_profile_async",
    "under_provisioned_vram",
]
