"""Uncertain native/device failures must never trigger package mutation."""

import importlib
import json
import subprocess

import pytest


def module(name):
    return importlib.import_module(f"core.windows_rocm.{name}")


@pytest.mark.parametrize(
    "result",
    [
        subprocess.CompletedProcess(
            [],
            1,
            json.dumps({"ready": False, "scope": "model-free", "category": category, "error": "same diagnostic"}),
            "",
        )
        for category in ("unsupported_gpu", "device_runtime", "native_runtime", "timeout", "unknown", "other")
    ]
    + [subprocess.CompletedProcess([], -1, "", "native crash"), subprocess.CompletedProcess([], 0, "malformed", "")],
)
def test_uncertain_or_device_failure_never_downloads_or_mutates(monkeypatch, result):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: result)
    monkeypatch.setattr(install, "download_archive", lambda *a: pytest.fail("unexpected download"))
    monkeypatch.setattr(install, "run_install", lambda *a: pytest.fail("unexpected mutation"))
    with pytest.raises(RuntimeError):
        install.provision("uv")


@pytest.mark.parametrize("category", ["package_missing", "package_version", "native_import", "binary_integrity"])
def test_confirmed_package_failures_enter_checked_repair(monkeypatch, category):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    result = subprocess.CompletedProcess(
        [], 1, json.dumps({"ready": False, "scope": "model-free", "category": category, "error": "same diagnostic"}), ""
    )
    monkeypatch.setattr(install.subprocess, "run", lambda *a, **k: result)

    def download(*args):
        raise RuntimeError("checked repair entered")

    monkeypatch.setattr(install, "download_archive", download)
    with pytest.raises(RuntimeError, match="checked repair entered"):
        install.provision("uv")


def test_unclassified_runtime_error_propagates_unchanged(monkeypatch):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    error = RuntimeError("missing DLL unsupported GPU timeout")

    def fail():
        raise error

    monkeypatch.setattr(install, "verify_environment", fail)
    monkeypatch.setattr(install, "download_archive", lambda *a: pytest.fail("unexpected download"))
    with pytest.raises(RuntimeError) as caught:
        install.provision("uv")
    assert caught.value is error


def test_cli_preserves_machine_failure_category(monkeypatch, capsys):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(
        install.subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(
            [],
            1,
            json.dumps({"ready": False, "scope": "model-free", "category": "device_runtime", "error": "driver"}),
            "",
        ),
    )
    assert install.main(["--verify-only"]) == 1
    assert json.loads(capsys.readouterr().out)["category"] == "device_runtime"


@pytest.mark.parametrize("error", [subprocess.TimeoutExpired("probe", 90), OSError("crash")])
def test_probe_cannot_complete_without_downloads(monkeypatch, error):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)

    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(install.subprocess, "run", fail)
    monkeypatch.setattr(install, "download_archive", lambda *a: pytest.fail("unexpected download"))
    with pytest.raises(RuntimeError):
        install.provision("uv")


def test_actual_repair_is_not_ready_if_post_install_verification_fails(monkeypatch):
    install = module("install")
    failure = module("failures").VerificationFailure
    calls = []

    def verify():
        calls.append("verify")
        raise failure("package_missing" if len(calls) == 1 else "device_runtime", "same diagnostic")

    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", verify)
    monkeypatch.setattr(install, "download_archive", lambda *a: calls.append("download"))
    monkeypatch.setattr(install, "checked_wheel", lambda archive, folder, config: folder / "wheel.whl")
    monkeypatch.setattr(install, "run_install", lambda *a: calls.append("install"))
    with pytest.raises(failure) as caught:
        install.provision("uv")
    assert caught.value.category == "device_runtime"
    assert calls == ["verify", "download", "install", "install", "verify"]


@pytest.mark.parametrize(
    "report",
    [
        {"ready": False, "category": "native_import", "error": "missing scope"},
        {"ready": False, "scope": "other", "category": "native_import", "error": "wrong scope"},
        {"ready": False, "scope": "model-free", "category": ["native_import"], "error": "bad category"},
        {"ready": False, "scope": "model-free", "category": "native_import", "error": 1},
    ],
)
def test_malformed_failure_diagnostics_never_authorize_repair(monkeypatch, report):
    install = module("install")
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(
        install.subprocess, "run", lambda *a, **k: subprocess.CompletedProcess([], 1, json.dumps(report), "")
    )
    monkeypatch.setattr(install, "download_archive", lambda *a: pytest.fail("unexpected download"))
    with pytest.raises(RuntimeError):
        install.provision("uv")
