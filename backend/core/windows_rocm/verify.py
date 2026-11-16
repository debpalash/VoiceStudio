"""Bounded child-only native checks; parent never imports Torch or CT2."""

import json
import logging
import os
import subprocess
import sys
from pathlib import Path

from .failures import VerificationFailure
from .runtime import DRIVER_HIP_ENV
from .recipe import ct2_supported, expected_versions, load_recipe

logger = logging.getLogger(__name__)
PROJECT_ROOT = Path(__file__).resolve().parents[3]


def validate_report(report, recipe):
    """Do not infer readiness from exit zero, CPU fallback, or stale versions."""
    verified_ct2 = ct2_supported(recipe)
    requirements = (
        report.get("ready") is True,
        report.get("scope") == "model-free",
        os.path.normcase(os.path.abspath(report.get("python", "")))
        == os.path.normcase(os.path.abspath(sys.executable)),
        report.get("architecture") == recipe["python"]["platform"],
        report.get("abi") == recipe["python"]["abi"],
        report.get("packages") == expected_versions(recipe),
        str(report.get("hip", "")).startswith(recipe["torch"]["hip_prefix"]),
        report.get("torch_gpu") is True,
        report.get("gpu_architecture") in recipe["gpu_architectures"],
        report.get("kernel_result") == 4.0,
        # A device without a verified HIP CTranslate2 (gfx1010) must report
        # that (and none) rather than leave the CT2 fields to chance. A recipe
        # that does pin one keeps the strict CT2 requirements unchanged; the
        # field itself is optional there so a 7.2.1-era probe stays valid.
        report.get("ct2_supported", verified_ct2) is verified_ct2,
        (isinstance(report.get("ct2_gpu_count"), int) and report["ct2_gpu_count"] > 0
         and "float16" in report.get("ct2_compute_types", [])
         and report.get("ct2_binary_sha256") == recipe["ctranslate2"]["binary_sha256"]
         and report.get("ct2_kernel_result") == [1.25, -2.5, 0.0, 8.0])
        if verified_ct2 else
        (report.get("ct2_gpu_count") == 0 and report.get("ct2_compute_types") == []
         and report.get("ct2_binary_sha256") is None and report.get("ct2_kernel_result") is None),
    )
    if not all(requirements):
        raise VerificationFailure(
            "invalid_report", "Windows ROCm probe failed exact interpreter/package/HIP/GPU validation"
        )
    return report


def _native_failure_message(returncode, details):
    """Keep the exit evidence ahead of the bounded native diagnostic tail."""
    status = returncode & 0xFFFFFFFF
    summary = f"Windows ROCm probe failed (exit {returncode}, status 0x{status:08X}): "
    if status == 0xC0000005:
        summary += (
            "native access violation; faulting module and cause are unknown."
        )
    else:
        summary += "probe process failure; cause is unknown."
    prefix = summary + " Diagnostic: "
    return prefix + details[-(2000 - len(prefix)):]


def verify_environment(timeout=90):
    """Run native validation without keeping package DLLs loaded in installer.

    The probe prefers the driver's newer HIP runtime (see ``runtime``). If the
    probe process dies natively with that preload active, retry once with the
    wheel's bundled runtime and log why, so a driver-side problem cannot
    strand an install that the bundled runtime verifies.
    """
    try:
        return _verify_once(timeout, os.environ)
    except VerificationFailure as exc:
        if exc.category != "crash" or os.environ.get(DRIVER_HIP_ENV, "").strip().lower() in ("0", "false", "off", "no"):
            raise
        logger.warning("Windows ROCm probe crashed; retrying with the bundled HIP runtime: %s", exc)
        env = {**os.environ, DRIVER_HIP_ENV: "0"}
        return _verify_once(timeout, env)


def _verify_once(timeout, env):
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.core.windows_rocm.probe"],
            cwd=PROJECT_ROOT,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired as exc:
        raise VerificationFailure("timeout", f"Windows ROCm probe could not complete: {exc}") from exc
    except OSError as exc:
        raise VerificationFailure("crash", f"Windows ROCm probe could not complete: {exc}") from exc
    details = (result.stderr or result.stdout or "no diagnostic").strip()[-2000:]
    try:
        report = json.loads(result.stdout.strip().splitlines()[-1])
        if not isinstance(report, dict):
            raise TypeError("Expected probe object")
    except (ValueError, IndexError, TypeError) as exc:
        if result.returncode:
            raise VerificationFailure("crash", _native_failure_message(result.returncode, details)) from exc
        raise VerificationFailure("invalid_report", f"Windows ROCm probe returned invalid diagnostics: {details}") from exc
    if result.returncode:
        if (
            result.returncode == 1
            and report.get("ready") is False
            and report.get("scope") == "model-free"
            and isinstance(report.get("error"), str)
        ):
            raise VerificationFailure(report.get("category", "unknown"), report["error"])
        raise VerificationFailure("crash", _native_failure_message(result.returncode, details))
    try:
        return validate_report(report, load_recipe())
    except (ValueError, TypeError, KeyError) as exc:
        raise VerificationFailure("invalid_report", "Windows ROCm probe returned invalid diagnostics") from exc
