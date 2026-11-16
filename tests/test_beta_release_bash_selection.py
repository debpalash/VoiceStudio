"""Release shell contracts must find native Bash behind a WSL launcher."""

import os
import subprocess
from pathlib import Path
from typing import Any
from types import SimpleNamespace

import pytest

import test_beta_release_channel as contract


@pytest.mark.parametrize("directory", ["System32", "Sysnative", "SYSTEM32"])
def test_custom_native_bash_after_wsl_is_used(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, directory: str
) -> None:
    launcher = f"C:/Windows/{directory}/bash.exe"
    native = "D:/Custom Git/bin/bash.exe"
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-git"))
    monkeypatch.setenv("PATH", os.pathsep.join(["wsl-bin", "custom-git-bin"]))
    monkeypatch.setattr(
        contract.shutil,
        "which",
        lambda name, path=None: native if path == "custom-git-bin" else launcher,
    )
    calls: list[list[str]] = []

    def run(argv: list[str], **kwargs: Any) -> SimpleNamespace:
        calls.append(argv)
        assert argv[0] == native
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    try:
        contract.test_publish_proves_where_latest_points(tmp_path, "stable", "v0.5.8", True)
    except pytest.skip.Exception:
        pytest.fail("Native Bash is available after the WSL launcher")
    assert len(calls) == 1


def test_wsl_only_path_skips_without_starting_a_child(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-git"))
    monkeypatch.setattr(contract.shutil, "which", lambda name, path=None: "C:/Windows/System32/bash.exe")
    monkeypatch.setattr(subprocess, "run", lambda *a, **kw: pytest.fail("WSL launched"))
    with pytest.raises(pytest.skip.Exception, match="Native Bash required"):
        contract.test_publish_proves_where_latest_points(tmp_path, "stable", "v0.5.8", True)
