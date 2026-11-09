"""Bounded child-only native checks; parent never imports Torch or CT2."""

import json
import os
import subprocess
import sys
from pathlib import Path

from .failures import VerificationFailure
from .recipe import expected_versions, load_recipe

PROJECT_ROOT = Path(__file__).resolve().parents[3]


def validate_report(report, recipe):
    """Do not infer readiness from exit zero, CPU fallback, or stale versions."""
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
        isinstance(report.get("ct2_gpu_count"), int) and report["ct2_gpu_count"] > 0,
        "float16" in report.get("ct2_compute_types", []),
        report.get("ct2_binary_sha256") == recipe["ctranslate2"]["binary_sha256"],
    )
    if not all(requirements):
        raise VerificationFailure(
            "invalid_report", "Windows ROCm probe failed exact interpreter/package/HIP/GPU validation"
        )
    return report


def verify_environment(timeout=90):
    """Run native validation without keeping package DLLs loaded in installer."""
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.core.windows_rocm.probe"],
            cwd=PROJECT_ROOT,
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
        category = "crash" if result.returncode else "invalid_report"
        raise VerificationFailure(category, f"Windows ROCm probe returned invalid diagnostics: {details}") from exc
    if result.returncode:
        if (
            result.returncode == 1
            and report.get("ready") is False
            and report.get("scope") == "model-free"
            and isinstance(report.get("error"), str)
        ):
            raise VerificationFailure(report.get("category", "unknown"), report["error"])
        raise VerificationFailure("crash", f"Windows ROCm probe failed (exit {result.returncode}): {details}")
    try:
        return validate_report(report, load_recipe())
    except (ValueError, TypeError, KeyError) as exc:
        raise VerificationFailure("invalid_report", "Windows ROCm probe returned invalid diagnostics") from exc
