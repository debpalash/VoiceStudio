"""Subprocess env builder for engine launchers (Phase 1 INST-12 + AUTH-04).

Every place that spawns an engine subprocess (sonitranslate, future
CosyVoice / IndexTTS subprocess backends from Phase 2) should call
`build_engine_env()` instead of constructing its own env dict ad-hoc.
That gives us ONE place to inject:

  - HF_TOKEN / YOUR_HF_TOKEN from the 3-source resolver (AUTH-04)
  - TORCH_COMPILE_DISABLE=1 on Windows when the user enabled the
    Performance toggle (INST-12, issue #65)

The function returns a fresh dict (caller may further mutate before
passing to `subprocess.Popen(env=...)`).
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from typing import Optional

logger = logging.getLogger("omnivoice.engine_env")

_TORCH_COMPILE_KEY = "perf.torch_compile_disabled"

# #278: explicit opt-in override — set to 1/true to attempt torch.compile even
# when the GPU's compute capability is not in this PyTorch build's arch list
# (e.g. a brand-new architecture running through PTX forward-compat).
_FORCE_COMPILE_ENV = "OMNIVOICE_FORCE_TORCH_COMPILE"

# #278: set (with a reason) the first time torch.compile — or *running* the
# compiled model — fails at runtime in this process. Once set, every later
# load in the same session goes straight to eager instead of re-tripping the
# same Dynamo/Inductor/Triton failure.
_compile_runtime_failure: Optional[str] = None


def mark_compile_runtime_failure(reason: str) -> None:
    """Record that torch.compile (or compiled execution) failed at runtime.

    Called by ``services.model_manager`` when compilation raises, or when a
    generation through the compiled model dies inside the Dynamo / Inductor /
    Triton stack (#278). Disables compile for the rest of the process — eager
    mode from here on; the next app restart probes again.
    """
    global _compile_runtime_failure
    _compile_runtime_failure = reason or "unknown torch.compile runtime failure"
    logger.warning(
        "torch.compile disabled for this session after a runtime failure: %s",
        _compile_runtime_failure,
    )


def _force_compile_requested() -> bool:
    value = os.environ.get(_FORCE_COMPILE_ENV, "")
    return value.strip().lower() in {"1", "true", "yes", "on"}


# ── FlashInfer opt-in (upstream k2-fsa port) ────────────────────────────────
# Explicit power-user opt-in, CUDA-only: OMNIVOICE_FLASHINFER=1 patches the
# OmniVoice model with flashinfer packed attention (~2x per upstream's
# benchmarks); =graph additionally captures CUDA graphs (best at batch=1).
# Off by default — `flashinfer` is not a shipped dependency, and an
# optimization must never be a point of failure. Session-sticky failure
# latch mirrors torch.compile's (#278).
_FLASHINFER_ENV = "OMNIVOICE_FLASHINFER"
_flashinfer_runtime_failure: Optional[str] = None


def flashinfer_mode() -> str:
    """The user's ``OMNIVOICE_FLASHINFER`` request: 'off' | 'on' | 'graph'.

    Unknown values normalize to 'off' with a log line naming the env var, so
    a typo degrades to the default path instead of half-applying.
    """
    value = os.environ.get(_FLASHINFER_ENV, "").strip().lower()
    if value in {"", "0", "false", "no", "off"}:
        return "off"
    if value in {"1", "true", "yes", "on"}:
        return "on"
    if value == "graph":
        return "graph"
    logger.warning(
        "%s=%r not recognized (valid: 0, 1, graph) — FlashInfer stays off.",
        _FLASHINFER_ENV, value,
    )
    return "off"


def should_flashinfer(device: str) -> str:
    """Resolve the FlashInfer request against this host: 'off' | 'on' | 'graph'.

    Requires all of: the ``OMNIVOICE_FLASHINFER`` opt-in, device == "cuda"
    (flashinfer is CUDA-only), the ``flashinfer`` package importable, and no
    earlier runtime failure this session. Every refusal is logged with the
    reason and the knob's name — the user asked for it, so silence would read
    as "the setting doesn't work".
    """
    mode = flashinfer_mode()
    if mode == "off":
        return "off"
    if device != "cuda":
        logger.warning(
            "%s requested but the compute device is %r — FlashInfer is "
            "CUDA-only, continuing without it.", _FLASHINFER_ENV, device,
        )
        return "off"
    if importlib.util.find_spec("flashinfer") is None:
        logger.warning(
            "%s requested but the `flashinfer` package is not installed — "
            "continuing without it. Install with: uv pip install "
            "flashinfer-python flashinfer-jit-cache "
            "--extra-index-url https://flashinfer.ai/whl/cu128/ "
            "(pick the index matching your CUDA build).", _FLASHINFER_ENV,
        )
        return "off"
    if _flashinfer_runtime_failure is not None:
        logger.info(
            "FlashInfer skipped: failed earlier this session (%s) — using the "
            "standard path.", _flashinfer_runtime_failure,
        )
        return "off"
    return mode


def mark_flashinfer_runtime_failure(reason: str) -> None:
    """Latch a FlashInfer apply/runtime failure for the rest of the process,
    same contract as ``mark_compile_runtime_failure``."""
    global _flashinfer_runtime_failure
    try:
        # Import/kernel errors embed absolute paths (wheels under the user's
        # home) — redact before latching, since the reason is logged here and
        # re-logged on every later skip.
        from core.failure import sanitize

        reason = sanitize(reason)
    except Exception:
        # Fail closed: if the redactor itself breaks, latching the raw text
        # would defeat the redaction. Keep only the exception class (the part
        # before ':' in our "Type: message" reasons) and drop the message.
        reason = (
            f"{(reason or '').split(':', 1)[0][:80]} "
            "(details redacted: sanitizer unavailable)"
        ).strip()
    _flashinfer_runtime_failure = reason or "unknown FlashInfer runtime failure"
    logger.warning(
        "FlashInfer disabled for this session after a runtime failure: %s",
        _flashinfer_runtime_failure,
    )


def _cuda_arch_supported_for_compile() -> "tuple[bool, str]":
    """Check the GPU's architecture against this torch build's arch list.

    A new GPU architecture routinely breaks torch.compile/Triton before
    upstream support lands (issue #278): the eager model runs via PTX
    forward-compat, but Inductor/Triton kernel compilation targets the new arch
    directly and fails mid-generation. Blackwell sm_120 was that case; it no
    longer is on the pinned torch 2.8.0+cu128, where this probe returns
    supported and compile runs correctly. If the device's arch tag is
    absent from this build's arch list we treat compile as unsupported and use
    eager. The comparison is delegated to ``core.device_caps.arch_unsupported``
    so it stays CUDA/ROCm-aware — a ROCm build lists ``gfx…`` names, and the
    old ``sm_`` comparison here disabled compile on every AMD host (#1228).

    Returns ``(supported, reason)``. Fails open — any probe error returns
    ``(True, "")`` so a weird torch build never silently loses the
    optimization (the runtime fallback in model_manager still protects
    generation).
    """
    try:
        import torch

        from core.device_caps import arch_unsupported

        if not torch.cuda.is_available():
            return True, ""
        mismatch = arch_unsupported(torch)
        if mismatch is None:
            return True, ""
        device_arch, arch_list = mismatch
        try:
            device_name = torch.cuda.get_device_name(0)
        except Exception:
            device_name = "GPU"
        return False, (
            f"{device_name} ({device_arch}) is not in this PyTorch build's "
            f"supported arch list ({', '.join(arch_list)})"
        )
    except Exception:
        logger.debug("CUDA arch probe for torch.compile failed; assuming supported", exc_info=True)
        return True, ""


def _torch_lib_path_is_linkable() -> tuple[bool, str]:
    """``(ok, reason)`` — False when inductor's C++ link step is guaranteed to
    fail because the torch library path contains whitespace (#1266).

    Inductor passes the torch lib directory to ``clang++``/``g++`` as an
    unquoted ``-L`` flag. A path with a space splits into two arguments and the
    compile dies with ``no such file or directory: 'Support/...'``. The bug is
    inside PyTorch, so we cannot fix the quoting — but a path we already know
    cannot compile is one we should not spend a compile attempt on.

    It is not hypothetical on any platform: the macOS data dir lives under
    ``~/Library/Application Support/``, and a Windows user profile is routinely
    ``C:/Users/First Last``.

    Never raises — an unreadable torch path means "no reason to skip".
    """
    try:
        import torch

        lib_dir = os.path.join(os.path.dirname(torch.__file__), "lib")
    except Exception:
        return True, ""
    if any(ch.isspace() for ch in lib_dir):
        # The path is genuinely useful for diagnosis, but it contains the user's
        # home directory and this string is logged and lands in pasted bug
        # reports — so it goes through the same redaction every other
        # user-facing failure text uses (home → ~, secrets stripped).
        try:
            from core.failure import sanitize

            shown = sanitize(lib_dir)
        except Exception:
            shown = os.path.basename(lib_dir.rstrip(os.sep)) or "<torch lib>"
        return False, (
            f"the torch library path contains whitespace ({shown!r}); inductor "
            f"passes it to the C++ linker unquoted, so every compile attempt fails"
        )
    return True, ""


def should_torch_compile(device: str) -> bool:
    """Decide whether to apply ``torch.compile`` to an in-process model.

    plan-02 (#65): ``torch.compile(mode="reduce-overhead")`` needs Triton at
    runtime, and Triton has no Windows build — on Windows+CUDA the compile path
    failed and surfaced as a confusing "OOM". Requires all of:
      - device == "cuda" (compile only helps the CUDA path here),
      - Triton importable (``find_spec`` — the cross-platform gate that closes
        #65; no Windows wheel ⇒ skip ⇒ eager),
      - the user has NOT set the ``perf.torch_compile_disabled`` escape hatch,
      - compile has NOT already failed at runtime in this process (#278),
      - the GPU's compute capability is in this torch build's arch list (#278)
        — overridable via ``OMNIVOICE_FORCE_TORCH_COMPILE=1``.

    Returns False (→ eager mode) on any of those, logging the reason at INFO.
    torch.compile is an optimization, never a requirement — generation must
    always work without it.
    """
    if device != "cuda":
        return False
    if importlib.util.find_spec("triton") is None:
        logger.info("torch.compile skipped: Triton unavailable — using eager mode.")
        return False
    try:
        from services import settings_store

        if settings_store.get_text(_TORCH_COMPILE_KEY, "0") == "1":
            logger.info("torch.compile skipped: disabled in Settings (Performance).")
            return False
    except Exception:
        logger.exception("should_torch_compile: settings read failed; proceeding")
    if _compile_runtime_failure is not None:
        logger.info(
            "torch.compile skipped: failed earlier this session (%s) — using eager mode.",
            _compile_runtime_failure,
        )
        return False
    linkable, path_reason = _torch_lib_path_is_linkable()
    if not linkable:
        if _force_compile_requested():
            logger.warning(
                "torch.compile forced via %s=1 despite: %s", _FORCE_COMPILE_ENV, path_reason,
            )
            return True
        logger.info(
            "torch.compile skipped: %s — using eager mode. "
            "(Set %s=1 to attempt compile anyway.)",
            path_reason, _FORCE_COMPILE_ENV,
        )
        return False
    supported, reason = _cuda_arch_supported_for_compile()
    if not supported:
        if _force_compile_requested():
            logger.warning(
                "torch.compile forced via %s=1 despite: %s", _FORCE_COMPILE_ENV, reason,
            )
            return True
        logger.info(
            "torch.compile skipped: %s — using eager mode. "
            "(Set %s=1 to attempt compile anyway.)",
            reason, _FORCE_COMPILE_ENV,
        )
        return False
    return True


def build_engine_env(
    *,
    base_env: Optional[dict] = None,
    inject_hf_token: bool = True,
) -> dict:
    """Build the environment dict to pass to an engine subprocess launcher.

    Args:
        base_env: starting point — defaults to `os.environ.copy()`.
        inject_hf_token: when True (default), resolve the HF token via the
            3-source cascade and inject it as both HF_TOKEN and YOUR_HF_TOKEN
            (the latter is what SoniTranslate's pipeline expects).

    Returns a new dict — never mutates the input.
    """
    env = dict(base_env if base_env is not None else os.environ)

    # AUTH-04: HF token injection from the resolver cascade. We import lazily
    # so the helper is callable in test contexts that don't stand up the
    # full settings_store / DB.
    if inject_hf_token:
        try:
            from services import token_resolver

            resolved = token_resolver.resolve()
            if resolved and resolved.token:
                env["HF_TOKEN"] = resolved.token
                env["YOUR_HF_TOKEN"] = resolved.token
        except Exception:
            logger.exception("build_engine_env: token resolver failed (non-fatal)")

    # INST-12: TORCH_COMPILE_DISABLE on Windows when the user opted in.
    # The flag is a Windows-only escape hatch — torch.compile OOMs the same
    # Triton kernel cache differently on macOS/Linux, so injecting on those
    # platforms would just slow the engine for no gain. (The in-process
    # should_torch_compile() gate handles the automatic Triton-absence case;
    # the subprocess var stays user-driven by design — see test_perf_settings.)
    if sys.platform.startswith("win"):
        try:
            from services import settings_store

            if settings_store.get_text(_TORCH_COMPILE_KEY, "0") == "1":
                env["TORCH_COMPILE_DISABLE"] = "1"
        except Exception:
            logger.exception("build_engine_env: torch_compile_disabled read failed")

    return env
