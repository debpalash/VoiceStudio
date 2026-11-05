"""Windows shell contracts must not accidentally invoke the WSL launcher."""
from types import SimpleNamespace

import pytest

import test_docker_registry_owner as contract


@pytest.mark.parametrize("directory", ["System32", "Sysnative", "SYSTEM32"])
def test_wsl_launchers_skip_without_starting_a_child(monkeypatch, tmp_path, directory):
    """WSL drops the native runner variables used by the shell contract."""
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-git"))
    monkeypatch.setattr(contract.shutil, "which", lambda name: f"C:/Windows/{directory}/bash.exe")
    monkeypatch.setattr(contract.subprocess, "run", lambda *a, **kw: pytest.fail("WSL launched"))
    with pytest.raises(pytest.skip.Exception, match="Bash required"):
        contract.resolved_env(tmp_path, "dutch2005")


def test_custom_git_bash_on_path_remains_usable(monkeypatch, tmp_path):
    """A native Git Bash outside Program Files must still execute the contract."""
    native = "D:/Developer Tools/Git/bin/bash.exe"
    monkeypatch.setenv("ProgramFiles", str(tmp_path / "no-git"))
    monkeypatch.setattr(contract.shutil, "which", lambda name: native)

    def run(argv, **kwargs):
        assert argv[0] == native
        contract.Path(kwargs["env"]["GITHUB_ENV"]).write_text("IMAGE_NAME=dutch2005/voicestudio\n")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(contract.subprocess, "run", run)
    assert contract.resolved_env(tmp_path, "dutch2005") == {"IMAGE_NAME": "dutch2005/voicestudio"}
