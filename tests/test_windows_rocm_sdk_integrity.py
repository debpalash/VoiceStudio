"""Pinned SDK DLL damage must repair even when the importing wheel is intact."""

import builtins
import importlib
from importlib import metadata
from types import SimpleNamespace

import pytest

SDK_FILES = [
    ("rocm-sdk-core", "_rocm_sdk_core/bin/amdhip64_7.dll"),
    ("rocm-sdk-libraries-custom", "_rocm_sdk_libraries_custom/bin/hipblas.dll"),
]
DIGEST = "sha256=vPKnZk6OrEn0bF4YxJFxLIrVCmInrg7BNIS6HIZTDDI"


def distribution_fixture(tmp_path, paths):
    records = []
    for name in paths:
        record = metadata.PackagePath(name)
        record.hash = metadata.FileHash(DIGEST)
        records.append(record)
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"native binary")
    return SimpleNamespace(files=records, locate_file=lambda file: tmp_path / file)


@pytest.mark.parametrize("importing_package", ["torch", "ctranslate2"])
@pytest.mark.parametrize("sdk,dll", SDK_FILES)
@pytest.mark.parametrize("damage", ["missing", "corrupt", "intact", "absent", "unreadable", "external", "unpinned"])
def test_failed_import_repairs_only_confirmed_pinned_sdk_damage(
    monkeypatch,
    tmp_path,
    importing_package,
    sdk,
    dll,
    damage,
):
    native = importlib.import_module("core.windows_rocm.native")
    install = importlib.import_module("core.windows_rocm.install")
    distributions = {importing_package: distribution_fixture(tmp_path, [importing_package + "/__init__.py"])}
    distributions.update({name: distribution_fixture(tmp_path, [path]) for name, path in SDK_FILES})
    if damage == "missing":
        (tmp_path / dll).unlink()
    elif damage == "corrupt":
        (tmp_path / dll).write_bytes(b"damaged")
    elif damage == "external":
        external = metadata.PackagePath("System32/vcruntime140.dll")
        distributions[sdk].files.append(external)
    elif damage == "unpinned":
        (tmp_path / dll).unlink()
        recipe = native.load_recipe()
        recipe["runtime"]["packages"] = ["rocm==7.2.1"]
        monkeypatch.setattr(native, "load_recipe", lambda: recipe)
    elif damage == "unreadable":

        def unreadable(*args):
            raise PermissionError("denied")

        distributions[sdk].locate_file = lambda file: SimpleNamespace(is_file=lambda: True, open=unreadable)

    inspected = []

    def distribution(name):
        inspected.append(name)
        if name == sdk and damage == "absent":
            raise metadata.PackageNotFoundError(name)
        return distributions[name]

    monkeypatch.setattr(native.metadata, "distribution", distribution)
    original_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == importing_package:
            raise OSError("native loader failure")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", lambda: native.import_native(importing_package))
    downloads = []

    def download(*args):
        downloads.append(args)
        raise RuntimeError("checked repair entered")

    monkeypatch.setattr(install, "download_archive", download)
    monkeypatch.setattr(install, "run_install", lambda *a: pytest.fail("unexpected mutation"))
    with pytest.raises(RuntimeError) as caught:
        install.provision("uv")
    repairable = damage in {"missing", "corrupt"}
    assert len(downloads) == int(repairable)
    if repairable:
        assert str(caught.value) == "checked repair entered"
    else:
        assert caught.value.category == "native_runtime"
    assert set(inspected) <= {importing_package, "rocm-sdk-core", "rocm-sdk-libraries-custom"}
    if damage == "unpinned":
        assert inspected == [importing_package]
