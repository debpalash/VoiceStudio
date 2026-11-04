"""Malformed JSON capability fields cannot certify native GPU readiness."""

import importlib
import json
import subprocess
import sys

import pytest


def _native_report():
    recipe_module = importlib.import_module("core.windows_rocm.recipe")
    recipe = recipe_module.load_recipe()
    return {
        "ready": True,
        "scope": "model-free",
        "python": sys.executable,
        "architecture": "win-amd64",
        "abi": "cp312",
        "packages": recipe_module.expected_versions(recipe),
        "hip": "7.2.53211",
        "torch_gpu": True,
        "gpu_architecture": "gfx1201",
        "kernel_result": 4.0,
        "ct2_gpu_count": 1,
        "ct2_compute_types": ["float16"],
        "ct2_binary_sha256": recipe["ctranslate2"]["binary_sha256"],
    }


@pytest.mark.parametrize("field,value", [
    ("ct2_gpu_count", True),
    ("ct2_compute_types", "float16"),
    ("ct2_compute_types", {"float16": False}),
    ("ct2_compute_types", ["float16", 1]),
    ("ct2_compute_types", ["float16", True]),
])
def test_malformed_native_capabilities_fail_closed(monkeypatch, field, value):
    verifier = importlib.import_module("core.windows_rocm.verify")
    report = _native_report()
    report[field] = value
    monkeypatch.setattr(
        verifier.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(report), ""),
    )
    with pytest.raises(verifier.VerificationFailure) as failure:
        verifier.verify_environment()
    assert failure.value.category == "invalid_report"
    assert not failure.value.repairable


@pytest.mark.parametrize("count,compute_types", [
    (1, ["float16"]),
    (2, ["float16", "float32"]),
])
def test_well_formed_capabilities_retain_ready_state(monkeypatch, count, compute_types):
    verifier = importlib.import_module("core.windows_rocm.verify")
    report = _native_report()
    report.update(ct2_gpu_count=count, ct2_compute_types=compute_types)
    monkeypatch.setattr(
        verifier.subprocess, "run",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(report), ""),
    )
    assert verifier.verify_environment() == report
