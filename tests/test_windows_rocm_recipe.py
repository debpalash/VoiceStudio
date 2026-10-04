"""Keep the native Windows installer contract reviewable across releases."""

import importlib.util
import json
from pathlib import Path
import re
from urllib.parse import urlparse

import pytest


ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATH = ROOT / "scripts" / "windows-rocm-recipe.json"


def load_setup():
    spec = importlib.util.spec_from_file_location("recipe_setup", ROOT / "scripts" / "setup.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_recipe_has_explicit_reviewed_artifacts():
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    assert recipe["schema_version"] == 1
    assert recipe["python"] == {"version": "3.12", "platform": "win-amd64"}
    torch = recipe["torch"]
    assert urlparse(torch["find_links"]).scheme == "https"
    assert urlparse(torch["find_links"]).netloc == "repo.radeon.com"
    versions = dict(pin.split("==") for pin in torch["packages"])
    assert set(versions) == {"torch", "torchaudio", "torchvision", "rocm[libraries]"}
    assert len(torch["packages"]) == len(versions)
    assert versions["torch"] == versions["torchaudio"]
    for package in ("torch", "torchaudio", "torchvision"):
        assert versions[package].endswith("+rocm" + versions["rocm[libraries]"])
    assert torch["find_links"].endswith(f'/rocm-rel-{versions["rocm[libraries]"]}/')
    native = recipe["ctranslate2"]
    assert urlparse(native["archive_url"]).scheme == "https"
    assert urlparse(native["archive_url"]).netloc == "github.com"
    assert f'/OpenNMT/CTranslate2/releases/download/v{native["version"]}/' in native["archive_url"]
    python_tag = "cp" + recipe["python"]["version"].replace(".", "")
    wheel_tag = recipe["python"]["platform"].replace("-", "_")
    assert native["wheel_member"] == (
        f'temp-windows/ctranslate2-{native["version"]}-{python_tag}-{python_tag}-{wheel_tag}.whl'
    )
    assert native["required_dlls"] == ["hipblas.dll", "amdhip64_7.dll"]
    for field in ("archive_sha256", "wheel_sha256"):
        assert re.fullmatch(r"[0-9a-f]{64}", native[field])


@pytest.mark.parametrize("working_directory", ["elsewhere", "folder with spaces", "za\u017c\u00f3\u0142\u0107"])
def test_setup_loads_sibling_recipe_independently_of_cwd(monkeypatch, tmp_path, working_directory):
    directory = tmp_path / working_directory
    directory.mkdir()
    monkeypatch.chdir(directory)
    setup = load_setup()
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    assert setup.WINDOWS_ROCM_RECIPE == recipe
    assert setup.WINDOWS_ROCM_FIND_LINKS == recipe["torch"]["find_links"]
    assert setup.WINDOWS_ROCM_TORCH_PINS == tuple(recipe["torch"]["packages"])
    native = recipe["ctranslate2"]
    assert setup.WINDOWS_ROCM_CT2_VERSION == native["version"]
    assert setup.WINDOWS_ROCM_CT2_URL == native["archive_url"]
    assert setup.WINDOWS_ROCM_CT2_ARCHIVE_SHA256 == native["archive_sha256"].upper()
    assert setup.WINDOWS_ROCM_CT2_WHEEL_MEMBER == native["wheel_member"]
    assert setup.WINDOWS_ROCM_CT2_WHEEL_SHA256 == native["wheel_sha256"].upper()


def test_probe_uses_the_recipe_version_and_dll_contract(monkeypatch):
    setup = load_setup()
    commands = []
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_VERSION", "9.8.7")
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_REQUIRED_DLLS", ("future_hip.dll",))
    monkeypatch.setattr(setup.subprocess, "run", lambda command, **kwargs: commands.append(command))
    setup._probe_windows_rocm_ctranslate2()
    probe = commands[0][2]
    assert "ctranslate2.__version__ != '9.8.7'" in probe
    assert "future_hip.dll" in probe
    assert "4.8.2" not in probe
    compile(probe, "<recipe-ct2-probe>", "exec")


def test_recipe_changes_drive_source_installation_and_python_guard(monkeypatch):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    recipe["python"]["version"] = "3.13"
    recipe["torch"]["find_links"] = "https://mirror.invalid/reviewed/"
    recipe["torch"]["packages"] = ["torch==9.0.0", "torchaudio==9.0.0"]
    recipe["ctranslate2"]["version"] = "9.8.7"
    read_text = Path.read_text

    def read_recipe(path, *args, **kwargs):
        if path == RECIPE_PATH:
            return json.dumps(recipe)
        return read_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "read_text", read_recipe)
    setup = load_setup()
    monkeypatch.setattr(setup.sysconfig, "get_platform", lambda: "win-amd64")
    assert setup._rocm_python_supported("win32", (3, 13))
    assert not setup._rocm_python_supported("win32", (3, 12))
    assert setup._rocm_python_supported("linux", (3, 11))
    assert setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "rocm"}, "win32") == recipe["torch"]["find_links"]
    command = setup.rocm_torch_reinstall_cmd(recipe["torch"]["find_links"], platform="win32")
    assert "torch==9.0.0" in command
    assert "torchaudio==9.0.0" in command
    assert not any("2.9.1" in argument for argument in command)
    assert setup.WINDOWS_ROCM_CT2_VERSION == "9.8.7"
