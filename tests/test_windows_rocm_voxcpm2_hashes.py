"""Hash enforcement for the explicitly selected private Windows HIP sidecar."""

import hashlib
import importlib.util
import json
import os
from pathlib import Path
from unittest.mock import Mock
from urllib.parse import urlsplit

import pytest


ROOT = Path(__file__).resolve().parents[1]
RECIPE_PATH = ROOT / "scripts/windows-rocm-recipe.json"
ARTIFACTS = [
    (section, position)
    for section, count in (("runtime", 4), ("torch", 3))
    for position in range(count)
]


@pytest.fixture
def standalone(monkeypatch):
    specification = importlib.util.spec_from_file_location(
        "_standalone_voxcpm2_hash_tests", ROOT / "backend/core/windows_rocm/voxcpm2.py"
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    assert not module.__package__
    return module


def write_recipe(module, recipe, monkeypatch, tmp_path):
    recipe_path = tmp_path / "windows-rocm-recipe.json"
    recipe_path.write_text(json.dumps(recipe), encoding="utf-8")
    monkeypatch.setattr(module, "RECIPE_PATH", recipe_path)


@pytest.mark.parametrize("section,position", ARTIFACTS)
def test_standalone_pins_every_amd_artifact_exactly(standalone, section, position):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    arguments = standalone.install_arguments()
    filename = recipe[section]["wheel_files"][position]
    digest = recipe[section]["wheel_sha256"][filename]

    assert arguments[:2] == ["voxcpm==2.0.3", "torchcodec==0.8.1"]
    assert len(arguments) == 9
    assert arguments.count(f"{recipe['torch']['find_links']}{filename}#sha256={digest}") == 1
    assert standalone.load_recipe() == recipe
    assert standalone.MARKER_REVISION == "windows-rocm-voxcpm2-v1"


@pytest.mark.parametrize("section,position", ARTIFACTS)
@pytest.mark.parametrize(
    "digest",
    [None, True, int("1" * 64), [], {}, "", "a" * 63, "a" * 65, "g" * 64, "A" * 64, "a" * 64 + "\n"],
    ids=["null", "boolean", "integer", "list", "mapping", "empty", "short", "long", "nonhex", "uppercase", "newline"],
)
def test_standalone_rejects_invalid_sha256_for_every_artifact(
    standalone, monkeypatch, tmp_path, section, position, digest
):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    filename = recipe[section]["wheel_files"][position]
    recipe[section]["wheel_sha256"][filename] = digest
    write_recipe(standalone, recipe, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.load_recipe()
    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.install_arguments()


@pytest.mark.parametrize("section", ["runtime", "torch"])
@pytest.mark.parametrize("hashes", [None, [], "a" * 64, 123, True, {}])
def test_standalone_rejects_missing_or_nonmapping_hashes(
    standalone, monkeypatch, tmp_path, section, hashes
):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    recipe[section]["wheel_sha256"] = hashes
    write_recipe(standalone, recipe, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.load_recipe()


@pytest.mark.parametrize("section", ["runtime", "torch"])
def test_standalone_rejects_absent_hash_map(standalone, monkeypatch, tmp_path, section):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    del recipe[section]["wheel_sha256"]
    write_recipe(standalone, recipe, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.install_arguments()


@pytest.mark.parametrize("section,position", ARTIFACTS)
def test_standalone_rejects_hash_for_different_filename(
    standalone, monkeypatch, tmp_path, section, position
):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    filename = recipe[section]["wheel_files"][position]
    recipe[section]["wheel_sha256"]["different-" + filename] = recipe[section]["wheel_sha256"].pop(filename)
    write_recipe(standalone, recipe, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.install_arguments()


@pytest.mark.parametrize("section", ["runtime", "torch"])
@pytest.mark.parametrize("filenames", [None, "artifact.whl", [], [123], [[]]])
def test_standalone_rejects_invalid_artifact_list(
    standalone, monkeypatch, tmp_path, section, filenames
):
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    recipe[section]["wheel_files"] = filenames
    write_recipe(standalone, recipe, monkeypatch, tmp_path)

    with pytest.raises(RuntimeError, match="invalid_report"):
        standalone.install_arguments()


@pytest.fixture
def installer(monkeypatch, tmp_path):
    from core.windows_rocm import voxcpm2
    from services import sidecar_install

    monkeypatch.setattr(sidecar_install, "DATA_DIR", tmp_path / "data")
    monkeypatch.delenv("OMNIVOICE_ENGINES_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_VOXCPM2_DIR", raising=False)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setenv("UV_NO_VERIFY_HASHES", "1")
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("UV_PYTHON_INSTALL_DIR", str(tmp_path / "uv-python"))
    monkeypatch.setattr(sidecar_install.sys, "platform", "win32")
    monkeypatch.setattr(voxcpm2.sysconfig, "get_platform", lambda: "win-amd64")
    monkeypatch.setattr(sidecar_install, "_host_family", lambda: "rocm")
    monkeypatch.setattr(sidecar_install, "_locate_uv", lambda: "mock-uv")
    monkeypatch.setattr(sidecar_install, "_run_logged", Mock(return_value=0))
    return sidecar_install


def test_opted_in_install_ignores_config_and_protects_child_hash_checking(installer, monkeypatch):
    ambient = {
        "UV_CONSTRAINT": "main-constraints.txt",
        "UV_BUILD_CONSTRAINT": "main-build-constraints.txt",
        "UV_OVERRIDE": "main-overrides.txt",
        "PIP_CONSTRAINT": "main-pip-constraints.txt",
        "UV_NO_VERIFY_HASHES": "1",
    }
    harmless = {
        "HTTPS_PROXY": "http://proxy.invalid:8080",
        "UV_INDEX_URL": "https://mirror.invalid/simple",
        "UV_CACHE_DIR": os.environ["UV_CACHE_DIR"],
        "UV_PYTHON_INSTALL_DIR": os.environ["UV_PYTHON_INSTALL_DIR"],
        "CUSTOM_SIDECAR_SETTING": "preserved",
    }
    for variable, value in (ambient | harmless).items():
        monkeypatch.setenv(variable, value)
    spec = installer.get_spec("voxcpm2")

    installer._step_install_deps(spec, installer._new_job(spec.engine_id))

    installer._run_logged.assert_called_once()
    arguments = installer._run_logged.call_args.args[1]
    environment = installer._run_logged.call_args.kwargs["env"]
    assert arguments[:4] == ["mock-uv", "--no-config", "pip", "install"]
    assert arguments[arguments.index("--python") + 1] == str(
        installer.managed_checkout(spec) / ".venv/Scripts/python.exe"
    )
    assert all(variable not in environment for variable in ambient)
    assert {variable: os.environ[variable] for variable in ambient} == ambient
    assert {variable: environment[variable] for variable in harmless} == harmless
    assert environment["UV_NO_CONFIG"] == "1"
    assert "--no-verify-hashes" not in arguments
    assert "--constraints" not in arguments
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    urls = [argument for argument in arguments if argument.startswith(recipe["torch"]["find_links"])]
    assert urls == [
        f"{recipe['torch']['find_links']}{filename}#sha256={recipe[section]['wheel_sha256'][filename]}"
        for section in ("runtime", "torch") for filename in recipe[section]["wheel_files"]
    ]


@pytest.mark.parametrize(
    "engine,platform,variant",
    [("voxcpm2", "win32", ""), ("voxcpm2", "linux", "rocm"),
     ("voxcpm2", "darwin", "rocm"), ("pockettts", "win32", "rocm")],
)
def test_other_installs_keep_ambient_environment_and_command(installer, monkeypatch, engine, platform, variant):
    monkeypatch.setattr(installer.sys, "platform", platform)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setenv("UV_CONSTRAINT", "user-constraints.txt")
    spec = installer.get_spec(engine)

    installer._step_install_deps(spec, installer._new_job(spec.engine_id))

    arguments = installer._run_logged.call_args.args[1]
    environment = installer._run_logged.call_args.kwargs["env"]
    assert arguments[:3] == ["mock-uv", "pip", "install"]
    assert environment["UV_NO_VERIFY_HASHES"] == "1"
    assert environment["UV_CONSTRAINT"] == "user-constraints.txt"
    assert "--no-config" not in arguments
    assert not any("#sha256=" in argument for argument in arguments)


@pytest.mark.parametrize("mismatch", [False, True], ids=["matching-content", "mismatched-content"])
def test_install_forwards_sha256_to_verifier_and_propagates_mismatch(
    installer, monkeypatch, tmp_path, mismatch
):
    from core.windows_rocm import voxcpm2

    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    contents = {}
    for section in ("runtime", "torch"):
        for filename in recipe[section]["wheel_files"]:
            payload = filename.encode("utf-8")
            recipe[section]["wheel_sha256"][filename] = hashlib.sha256(payload).hexdigest()
            contents[recipe["torch"]["find_links"] + filename] = payload
    write_recipe(voxcpm2, recipe, monkeypatch, tmp_path)
    if mismatch:
        contents[next(iter(contents))] = b"altered artifact bytes"
    verified = []

    def verify_hashes(job, arguments, **options):
        assert "UV_NO_VERIFY_HASHES" not in options["env"]
        for argument in arguments:
            location = urlsplit(argument)
            if location.scheme != "https":
                continue
            assert location.fragment.startswith("sha256=")
            payload = contents[location._replace(fragment="").geturl()]
            if hashlib.sha256(payload).hexdigest() != location.fragment.removeprefix("sha256="):
                return 1
            verified.append(argument)
        return 0

    installer._run_logged.side_effect = verify_hashes
    spec = installer.get_spec("voxcpm2")
    job = installer._new_job(spec.engine_id)
    if mismatch:
        with pytest.raises(installer._StepError, match="uv pip install failed"):
            installer._step_install_deps(spec, job)
    else:
        installer._step_install_deps(spec, job)
        assert len(verified) == 7
    assert not (installer.managed_checkout(spec) / installer._INSTALL_COMPLETE_MARKER).exists()
