"""Provision SDK development files explicitly and prepare read-only header lookup."""

import importlib
from types import SimpleNamespace

import pytest


@pytest.fixture
def runtime():
    return importlib.import_module("core.windows_rocm.runtime")


@pytest.fixture
def sdk_environment(monkeypatch, tmp_path, runtime):
    monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="win32"))
    versions = {"torch": "2.9.1+rocm7.2.1", "rocm-sdk-devel": "7.2.1"}
    monkeypatch.setattr(runtime.metadata, "version", versions.__getitem__)
    monkeypatch.setattr(runtime, "dll_directories", lambda: [])
    monkeypatch.setenv("ROCM_PATH", "")
    monkeypatch.delenv("ROCM_PATH")
    monkeypatch.setenv("MIOPEN_CUSTOM_CACHE_DIR", "")
    monkeypatch.delenv("MIOPEN_CUSTOM_CACHE_DIR")
    root = tmp_path / "SDK with spaces"
    header = root / "include/rocrand/rocrand_xorwow.h"
    header.parent.mkdir(parents=True)
    header.write_text("fixture header", encoding="utf-8")
    monkeypatch.setattr(runtime, "package_root", lambda package: root)
    return monkeypatch, root, versions


def test_native_initialization_finds_expanded_headers_without_unpacking(sdk_environment, runtime):
    monkeypatch, root, versions = sdk_environment
    monkeypatch.setattr(runtime, "import_module", lambda name: pytest.fail("native lookup must not expand SDK"), raising=False)

    runtime.initialize_dll_search()

    assert runtime.os.environ["ROCM_PATH"] == str(root)


@pytest.mark.parametrize("override", ["", "D:/Custom SDK"])
def test_explicit_rocm_path_is_preserved(sdk_environment, override, runtime):
    monkeypatch, root, versions = sdk_environment
    monkeypatch.setenv("ROCM_PATH", override)

    runtime.initialize_dll_search()

    assert runtime.os.environ["ROCM_PATH"] == override


@pytest.mark.parametrize("damage", ["missing-spec", "missing-header", "wrong-sdk", "cuda", "linux"])
def test_unverified_sdk_does_not_change_header_lookup(sdk_environment, damage, runtime):
    monkeypatch, root, versions = sdk_environment
    if damage == "missing-spec":
        monkeypatch.setattr(runtime, "package_root", lambda package: None)
    elif damage == "missing-header":
        (root / "include/rocrand/rocrand_xorwow.h").unlink()
    elif damage == "wrong-sdk":
        versions["rocm-sdk-devel"] = "7.2.2"
    elif damage == "cuda":
        versions["torch"] = "2.9.1+cu128"
    else:
        monkeypatch.setattr(runtime, "sys", SimpleNamespace(platform="linux"))

    runtime.initialize_dll_search()

    assert "ROCM_PATH" not in runtime.os.environ


def test_explicit_sdk_setup_uses_official_api(sdk_environment, runtime):
    monkeypatch, root, versions = sdk_environment
    calls = []
    monkeypatch.setattr(runtime, "import_module", lambda name: calls.append(name) or SimpleNamespace(
        get_devel_root=lambda: root
    ), raising=False)

    runtime.initialize_development_sdk()

    assert calls == ["rocm_sdk._devel"]
    assert runtime.os.environ["ROCM_PATH"] == str(root)


def test_sdk_expansion_failure_does_not_publish_header_lookup(sdk_environment, runtime):
    monkeypatch, root, versions = sdk_environment

    def fail():
        raise PermissionError("SDK expansion blocked")

    monkeypatch.setattr(runtime, "import_module", lambda name: SimpleNamespace(get_devel_root=fail), raising=False)
    with pytest.raises(PermissionError, match="SDK expansion blocked"):
        runtime.initialize_development_sdk()

    assert "ROCM_PATH" not in runtime.os.environ


def test_matched_install_reuse_initializes_sdk_after_verification(monkeypatch):
    from core.windows_rocm import install

    events = []
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", lambda: events.append("verify") or {"ready": True})
    monkeypatch.setattr(install, "initialize_development_sdk", lambda: events.append("sdk"), raising=False)
    monkeypatch.setattr(install, "run_install", lambda command: pytest.fail("healthy install must not reinstall"))

    assert install.provision("fixture-uv") == {"ready": True}
    assert events == ["verify", "sdk"]


def test_verify_only_never_unpacks_sdk(monkeypatch, capsys):
    from core.windows_rocm import install

    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", lambda: {"ready": True})
    monkeypatch.setattr(install, "initialize_development_sdk", lambda: pytest.fail("verify-only must not unpack"), raising=False)

    assert install.main(["--verify-only"]) == 0
    assert '"ready": true' in capsys.readouterr().out


def test_repaired_install_initializes_sdk_after_all_packages(monkeypatch):
    from core.windows_rocm import install
    from core.windows_rocm.failures import VerificationFailure

    events = []

    def verify():
        events.append("verify")
        if len(events) == 1:
            raise VerificationFailure("package_missing", "fixture missing package")
        return {"ready": True}

    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", verify)
    monkeypatch.setattr(install, "download_archive", lambda *arguments: events.append("download"))
    monkeypatch.setattr(install, "checked_wheel", lambda archive, folder, recipe: folder / "fixture.whl")
    monkeypatch.setattr(install, "run_install", lambda command: events.append("packages"))
    monkeypatch.setattr(install, "initialize_development_sdk", lambda: events.append("sdk"))

    assert install.provision("fixture-uv") == {"ready": True}
    assert events == ["verify", "download", "packages", "packages", "sdk", "verify"]


def test_sidecar_install_cli_expands_sdk_before_native_probe(monkeypatch, capsys):
    from core.windows_rocm import voxcpm2

    events = []
    monkeypatch.setattr(voxcpm2, "validate_host", lambda: None)
    monkeypatch.setattr(voxcpm2, "_initialize_dll_search", lambda **options: events.append(options))
    monkeypatch.setattr(voxcpm2, "verify_gpu", lambda: events.append("verify") or {"ready": True})

    assert voxcpm2.main() == 0
    assert events == [{"initialize_sdk": True}, "verify"]
    assert '"ready": true' in capsys.readouterr().out
