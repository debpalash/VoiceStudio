"""Installer command boundaries: healthy reuse, repair order and no native locks."""

import importlib
import subprocess
import sys
from pathlib import Path

import pytest


@pytest.fixture
def installer(monkeypatch):
    module = importlib.import_module("core.windows_rocm.install")
    monkeypatch.setattr(module, "validate_target", lambda: None)
    return module


def test_healthy_runtime_reuses_without_package_or_network_mutation(installer, monkeypatch):
    monkeypatch.setattr(installer, "verify_environment", lambda: {"ready": True})
    monkeypatch.setattr(installer, "download_archive", lambda *args: pytest.fail("unexpected download"))
    monkeypatch.setattr(installer, "run_install", lambda *args: pytest.fail("unexpected install"))
    assert installer.provision("uv-custom") == {"ready": True}


def test_repair_installs_official_stack_then_verified_ct2_and_rechecks(installer, monkeypatch, tmp_path):
    events = []

    def verify():
        events.append("verify")
        if len(events) == 1:
            raise importlib.import_module("core.windows_rocm.failures").VerificationFailure(
                "package_missing", "missing"
            )
        return {"ready": True}

    def checked(archive, folder, config):
        events.append("checksum")
        return folder / "ctranslate2-4.8.2-cp312-cp312-win_amd64.whl"

    monkeypatch.setattr(installer, "verify_environment", verify)
    monkeypatch.setattr(installer, "download_archive", lambda *a: events.append("download"))
    monkeypatch.setattr(installer, "checked_wheel", checked)
    requirements = []

    def run(command):
        events.append(command)
        if "-r" in command:  # read while the temporary directory still exists
            requirements.extend(Path(command[command.index("-r") + 1]).read_text(encoding="utf-8").splitlines())

    monkeypatch.setattr(installer, "run_install", run)
    assert installer.provision("uv-custom")["ready"]
    assert events[:3] == ["verify", "download", "checksum"]
    stack, ct2 = events[3:5]
    assert stack[:6] == ["uv-custom", "--no-config", "pip", "install", "--python", sys.executable]
    assert "--reinstall-package" in stack and "--constraints" in stack and "--constraints" in ct2
    assert len(requirements) == 7
    assert all(line.startswith("https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/")
               and " --hash=sha256:" in line for line in requirements)
    assert ct2[-1].endswith("ctranslate2-4.8.2-cp312-cp312-win_amd64.whl")
    assert events[-1] == "verify"


def test_corrupt_download_prevents_any_package_mutation(installer, monkeypatch):
    def fail(*args):
        raise RuntimeError("SHA-256")

    def broken():
        raise importlib.import_module("core.windows_rocm.failures").VerificationFailure(
            "binary_integrity", "damaged DLL"
        )

    monkeypatch.setattr(installer, "verify_environment", broken)
    monkeypatch.setattr(installer, "download_archive", lambda *a: None)
    monkeypatch.setattr(installer, "checked_wheel", fail)
    monkeypatch.setattr(installer, "run_install", lambda *a: pytest.fail("unexpected install"))
    with pytest.raises(RuntimeError, match="SHA-256"):
        installer.provision("uv-custom")


def test_installer_removes_ambient_cuda_constraints(installer, monkeypatch):
    monkeypatch.setenv("UV_CONSTRAINT", "cuda.txt")
    monkeypatch.setenv("UV_BUILD_CONSTRAINT", "cuda-build.txt")
    monkeypatch.setenv("UV_OVERRIDE", "cuda-override.txt")
    monkeypatch.setenv("PIP_CONSTRAINT", "pip-cuda.txt")

    def run(command, **kwargs):
        assert kwargs["check"] is True and kwargs["timeout"] == 1800
        leaked_constraints = {"UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "PIP_CONSTRAINT"} & kwargs[
            "env"
        ].keys()
        assert leaked_constraints == set()
        raise subprocess.TimeoutExpired(command, 1800)

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(RuntimeError, match="installation failed"):
        installer.run_install(["uv-custom", "pip", "install"])


def test_verify_only_never_provisions(installer, monkeypatch, capsys):
    monkeypatch.setattr(installer, "verify_environment", lambda: {"ready": True})
    monkeypatch.setattr(installer, "provision", lambda *a: pytest.fail("unexpected provision"))
    assert installer.main(["--verify-only"]) == 0
    assert '"ready": true' in capsys.readouterr().out
