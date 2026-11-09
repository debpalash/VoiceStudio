"""Installer integrity and ABI boundaries; no installed native packages needed."""
import hashlib
import importlib
import json
import zipfile

import pytest


def module(name):
    return importlib.import_module(f"core.windows_rocm.{name}")


def test_shared_installer_exists_without_importing_native_packages():
    import sys

    before = set(sys.modules)
    assert importlib.util.find_spec("core.windows_rocm") is not None
    module("install")
    assert not ({"torch", "torchaudio", "torchvision", "ctranslate2"} & (set(sys.modules) - before))


@pytest.mark.parametrize(
    "platform,version,architecture",
    [
        ("win32", (3, 12), "win-arm64"),
        ("darwin", (3, 12), "win-amd64"),
        ("win32", (3, 12), "win32"),
        ("win32", (3, 13), "win-amd64"),
    ],
    ids=("arm-wheel", "mac-host", "x86-wheel", "newer-python"),
)
def test_recipe_rejects_an_unsupported_runtime(platform, version, architecture):
    with pytest.raises(RuntimeError, match="x64 Python 3.12"):
        module("recipe").validate_target(platform, version, architecture)


def test_recipe_identity_invalidates_on_any_byte_change(tmp_path):
    recipe = module("recipe")
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(recipe.load_recipe()))
    initial = recipe.recipe_identity(path)
    path.write_text(path.read_text(encoding="utf-8") + "\n")
    assert recipe.recipe_identity(path) != initial
    recipe.validate_target("win32", (3, 12), "win-amd64")


def archive_fixture(tmp_path, entries=None):
    member = "nested/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl"
    archive = tmp_path / "archive.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        for name, data in entries or [(member, b"wheel")]:
            bundle.writestr(name, data)
    config = {
        "archive_sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "wheel_member": member,
        "wheel_sha256": hashlib.sha256(b"wheel").hexdigest(),
    }
    return archive, config


def test_only_named_checked_wheel_is_written(tmp_path):
    archive, config = archive_fixture(tmp_path, [
        ("../../outside", b"evil"),
        ("nested/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl", b"wheel"),
    ])
    destination = tmp_path / "output"
    destination.mkdir()
    wheel = module("artifacts").checked_wheel(archive, destination, config)
    assert wheel.read_bytes() == b"wheel"
    assert list(destination.iterdir()) == [wheel]


def test_partial_wheel_is_removed_when_output_write_fails(tmp_path, monkeypatch):
    archive, config = archive_fixture(tmp_path)
    destination = tmp_path / "output"
    destination.mkdir()
    expected_output = destination / config["wheel_member"].rsplit("/", 1)[-1]
    artifacts = module("artifacts")
    original_open = artifacts.Path.open
    failure = OSError("simulated storage failure")

    class FailingWriter:
        def __init__(self, stream):
            self.stream = stream

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.stream.close()

        def write(self, data):
            self.stream.write(data[:2])
            raise failure

    def open_path(path, *args, **kwargs):
        stream = original_open(path, *args, **kwargs)
        if path == expected_output and args and args[0] == "wb":
            return FailingWriter(stream)
        return stream

    monkeypatch.setattr(artifacts.Path, "open", open_path)
    with pytest.raises(OSError) as caught:
        artifacts.checked_wheel(archive, destination, config)

    assert caught.value is failure
    assert list(destination.iterdir()) == []


@pytest.mark.parametrize("field", ["archive_sha256", "wheel_sha256"])
def test_tampered_artifacts_never_return_installable_wheel(tmp_path, field):
    archive, config = archive_fixture(tmp_path)
    config[field] = "0" * 64
    with pytest.raises(RuntimeError, match="SHA-256"):
        module("artifacts").checked_wheel(archive, tmp_path, config)


def test_duplicate_member_refused(tmp_path):
    member = "nested/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl"
    with pytest.warns(UserWarning):
        archive, config = archive_fixture(tmp_path, [(member, b"wheel"), (member, b"wheel")])
    with pytest.raises(RuntimeError, match="exactly one"):
        module("artifacts").checked_wheel(archive, tmp_path, config)


# ── Supply-chain pinning (maintainer security review of #3045) ───────────────

def test_every_amd_artifact_is_hash_pinned():
    recipe = module("recipe").load_recipe()
    for section in ("torch", "runtime"):
        assert set(recipe[section]["wheel_files"]) == set(recipe[section]["wheel_sha256"])


@pytest.mark.parametrize("bad", [None, "", "abc", "Z" * 64])
def test_recipe_without_a_valid_hash_is_refused(tmp_path, bad):
    recipe = module("recipe")
    data = recipe.load_recipe()
    name = data["torch"]["wheel_files"][0]
    if bad is None:
        del data["torch"]["wheel_sha256"][name]
    else:
        data["torch"]["wheel_sha256"][name] = bad
    path = tmp_path / "recipe.json"
    path.write_text(json.dumps(data))
    with pytest.raises(RuntimeError, match="sha256"):
        recipe.load_recipe(path)


def test_requirements_carry_a_hash_for_every_url():
    install = module("install")
    recipe = module("recipe").load_recipe()
    lines = install.hashed_requirements(recipe).splitlines()
    assert len(lines) == sum(len(recipe[s]["wheel_files"]) for s in ("torch", "runtime"))
    for line in lines:
        url, flag = line.split(" --hash=sha256:")
        assert url.startswith("https://repo.radeon.com/") and len(flag) == 64


class _Dist:
    def __init__(self, name, version):
        self.metadata = {"Name": name}
        self.version = version


def test_constraints_pin_everything_but_the_overlay():
    install = module("install")
    text = install.constraints_text([
        _Dist("numpy", "2.2.6"), _Dist("PyYAML", "6.0.2"), _Dist("torch", "2.8.0"),
        _Dist("torchaudio", "2.8.0"), _Dist("rocm-sdk-core", "7.2.1"), _Dist("rocm", "7.2.1"),
        _Dist("ctranslate2", "4.6.0"), _Dist("triton-windows", "3.4.0"),
    ])
    assert text.splitlines() == ["numpy==2.2.6", "PyYAML==6.0.2"]


def test_install_command_applies_constraints_and_never_disables_hash_checks(tmp_path, monkeypatch):
    install = module("install")
    command = install.install_command("uv", ["-r", "req.txt"], ["torch"], tmp_path / "c.txt",
                                      build_isolation_off=("rocm",))
    assert command[command.index("--constraints") + 1] == str(tmp_path / "c.txt")
    assert "--no-verify-hashes" not in command
    assert command[command.index("--no-build-isolation-package") + 1] == "rocm"
    seen = {}
    monkeypatch.setenv("UV_NO_VERIFY_HASHES", "1")
    monkeypatch.setattr(install.subprocess, "run", lambda cmd, **kw: seen.update(kw))
    install.run_install(command)
    assert "UV_NO_VERIFY_HASHES" not in seen["env"]
