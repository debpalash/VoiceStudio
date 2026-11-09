"""Stable, non-diagnostic metadata for engine-discovery responses."""
from __future__ import annotations

from core.device_caps import KERNEL_RISK_MARKER

_UNAVAILABLE = "Engine unavailable. Check installation and configuration."
_PREVIOUS_FAILURE = "A previous engine check failed."
_ROUTING_BY_STATUS = {
    "cpu_fallback": "GPU acceleration is unavailable; this engine will use CPU.",
    "cpu_only": "This engine runs on CPU on this host.",
    "unavailable": "This engine has no compatible compute device on this host.",
}
_ROUTING_UNAVAILABLE = "Engine routing details are unavailable."
_ACCELERATOR_KERNEL_RISK = (
    "The selected accelerator may not be supported by this PyTorch build."
)
_ACCELERATOR_LOW_VRAM = (
    "The accelerator may not meet this engine's recommended VRAM."
)
_ACCELERATOR_ADVISORY = "The selected accelerator has a compatibility advisory."


def _public_routing_reason(status: object, diagnostic: object) -> str:
    """Map a private routing diagnostic to an accurate stable category."""
    if status == "accelerated":
        private = diagnostic if isinstance(diagnostic, str) else ""
        if KERNEL_RISK_MARKER in private:
            return _ACCELERATOR_KERNEL_RISK
        if " GB VRAM; this engine wants about " in private:
            return _ACCELERATOR_LOW_VRAM
        return _ACCELERATOR_ADVISORY
    return _ROUTING_BY_STATUS.get(status, _ROUTING_UNAVAILABLE)


# Categories for WHY an engine is unavailable. The probe's own sentence cannot
# cross the boundary — it carries exception text, local paths and sometimes
# credentials — but "Engine unavailable. Check installation and configuration."
# told the user nothing at all, and "Last error: A previous engine check
# failed." reads like a crash rather than "you have not installed this yet"
# (#1866). Classifying the private diagnostic into an owned sentence keeps the
# boundary intact and still names the kind of problem and the place to fix it.
_UNAVAILABLE_NOT_INSTALLED = (
    "This engine's package isn't installed yet. Install it from "
    "Settings → Models."
)
# An engine gated behind an in-app license review (Supertonic-3, PocketTTS).
# The Model Catalogue shows its Accept button only when the reason matches
# /license not accepted/i (EngineCompatibilityMatrix.reasonMentionsLicense), so
# this sentence must keep those words: collapsing it into the generic line hid
# the only way to enable those engines.
_UNAVAILABLE_LICENSE = (
    "License not accepted yet. Review and accept it in "
    "Settings → Models to enable this engine."
)
# An engine that cannot run on this machine at all: Apple-Silicon-only MLX,
# PyTorch with no Intel Mac build. "Isn't installed yet" or "check
# installation" sent people after an install that could never work.
_UNAVAILABLE_PLATFORM = (
    "This engine doesn't run on this computer's platform. Its guide lists "
    "the platforms it supports."
)
# Apple Silicon whose PyTorch cannot use the GPU (MPS): the platform is
# right, the installation is not. MLX-Audio / MLX-Whisper need MPS (#390).
_UNAVAILABLE_NO_MPS = (
    "This engine needs Apple's GPU (MPS), and this installation's PyTorch "
    "can't use it. Updating macOS or reinstalling VoiceStudio usually "
    "restores it."
)
_UNAVAILABLE_NEEDS_CONFIG = (
    "This engine needs to be configured before it can run. Open "
    "Settings → Models to finish setting it up."
)
_UNAVAILABLE_FILE_MISSING = (
    "A file this engine needs is missing or unreadable. Reinstall it from "
    "Settings → Models."
)

# The same two cases for an engine the app cannot install for you. "Install it
# from Model Catalogue" sent people to a page with no Install button
# for that engine — most of the catalogue — which reads as the app being
# broken. The row's own guide link (``docs_url``) is the real next step.
_UNAVAILABLE_NOT_INSTALLED_MANUAL = (
    "This engine isn't installed yet, and it has no one-click install. "
    "Its guide lists the install steps."
)
_UNAVAILABLE_FILE_MISSING_MANUAL = (
    "A file this engine needs is missing or unreadable. Its guide lists the "
    "install steps."
)
_MANUAL_INSTALL_VARIANT = {
    _UNAVAILABLE_NOT_INSTALLED: _UNAVAILABLE_NOT_INSTALLED_MANUAL,
    _UNAVAILABLE_FILE_MISSING: _UNAVAILABLE_FILE_MISSING_MANUAL,
}

# Matched against the lowered probe text. Ordered most specific first: a
# missing file often also says "not installed", and the file case has the more
# useful remedy of the two.
_UNAVAILABLE_SIGNATURES = (
    # First: its probe text also says "Open Model Catalogue", and the
    # license is the one gap only the user can close.
    (_UNAVAILABLE_LICENSE, ("license not accepted",)),
    # Before the install and file checks: a platform reason often also says
    # "unavailable" or names a missing wheel, and no install can fix it. Not
    # "apple silicon only": mlx-audio says that on an M-series Mac too, when
    # the package is merely missing and installing does help.
    (_UNAVAILABLE_PLATFORM, (
        "requires apple silicon", "not supported on this platform",
        "unavailable on intel macs", "no macos x86_64 wheel",
        "no windows install", "not supported on windows",
    )),
    (_UNAVAILABLE_NO_MPS, ("torch mps unavailable",)),
    (_UNAVAILABLE_FILE_MISSING, (
        "file is missing", "file is empty", "file is unreadable",
        "script missing", "binary", "not found at",
    )),
    (_UNAVAILABLE_NEEDS_CONFIG, (
        "environment variable", "configure a server endpoint", "api key",
        "unconfigured", "set the", "base url",
    )),
    (_UNAVAILABLE_NOT_INSTALLED, (
        "not installed", "package missing", "not available", "no module named",
        "import ", "unavailable:", "failed to load",
    )),
)


def _public_unavailable_reason(diagnostic: object) -> str:
    """Map a private availability probe to an accurate stable category."""
    private = diagnostic.lower() if isinstance(diagnostic, str) else ""
    for public, markers in _UNAVAILABLE_SIGNATURES:
        if any(marker in private for marker in markers):
            return public
    return _UNAVAILABLE


def public_backends(entries: list[dict]) -> list[dict]:
    """Copy registry entries while replacing service diagnostics.

    Availability probes may contain exception text, local paths, tracebacks, or
    credentials. Registry-authored fields are not probe output and remain
    intact: ``install_hint``, ``setup_snippet`` and ``docs_url`` are all
    VoiceStudio-owned constants keyed on the engine id, so an unavailable row
    still has something actionable to show and somewhere to send the user
    (#1866) even though ``reason``/``last_error`` are replaced here.
    """
    safe: list[dict] = []
    for entry in entries:
        item = dict(entry)
        if item.get("reason") is not None:
            reason = _public_unavailable_reason(item["reason"])
            # Only a row that explicitly says it has NO one-click install gets
            # the manual wording. Rows without the field (ASR, LLM,
            # translation — some of which have installers of their own) keep
            # the line that points at Model Catalogue.
            if item.get("one_click_install") is False:
                reason = _MANUAL_INSTALL_VARIANT.get(reason, reason)
            item["reason"] = reason
        if item.get("last_error") is not None:
            item["last_error"] = _PREVIOUS_FAILURE
        if item.get("routing_reason") is not None:
            item["routing_reason"] = _public_routing_reason(
                item.get("routing_status"), item["routing_reason"]
            )
        evidence = item.get("execution_evidence")
        if isinstance(evidence, dict) and evidence.get("cpu_fallback_reason") is not None:
            evidence = dict(evidence)
            evidence["cpu_fallback_reason"] = _public_routing_reason(
                "cpu_fallback", evidence["cpu_fallback_reason"]
            )
            item["execution_evidence"] = evidence
        safe.append(item)
    return safe


def public_unavailability(detail: object) -> str | None:
    return None if detail is None else _UNAVAILABLE
