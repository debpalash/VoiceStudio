"""Ordinary import-time failures require package evidence; control flow must escape."""

import asyncio
import base64
import hashlib
import importlib
import sys
from importlib import metadata
from types import SimpleNamespace

import pytest

SOURCES = [
    b"def broken(:\n",  # A damaged Python source must exercise a real SyntaxError.
    b"raise ValueError('import failed')\n",
    b"raise AttributeError('import failed')\n",
    b"raise TypeError('import failed')\n",
]


@pytest.mark.parametrize("package", ["torch", "torchaudio", "torchvision", "ctranslate2"])
@pytest.mark.parametrize("source", SOURCES)
@pytest.mark.parametrize("evidence", ["corrupt", "intact", "unavailable"])
def test_ordinary_import_failure_requires_positive_record_evidence(
    monkeypatch,
    tmp_path,
    package,
    source,
    evidence,
):
    native = importlib.import_module("core.windows_rocm.native")
    install = importlib.import_module("core.windows_rocm.install")
    path = tmp_path / package / "__init__.py"
    path.parent.mkdir()
    path.write_bytes(source)
    # Corrupt fixtures retain the original, valid wheel's RECORD digest.
    recorded_source = b"pass\n" if evidence == "corrupt" else source
    digest = base64.urlsafe_b64encode(hashlib.sha256(recorded_source).digest()).decode().rstrip("=")
    recorded = metadata.PackagePath(package + "/__init__.py")
    recorded.hash = metadata.FileHash("sha256=" + digest)

    def distribution(name):
        if name != package:
            raise metadata.PackageNotFoundError(name)
        return SimpleNamespace(
            files=None if evidence == "unavailable" else [recorded], locate_file=lambda file: tmp_path / file
        )

    monkeypatch.setattr(native.metadata, "distribution", distribution)
    monkeypatch.delitem(sys.modules, package, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", lambda: native.import_native(package))
    downloads = []

    def download(*args):
        downloads.append(args)
        raise RuntimeError("checked repair entered")

    monkeypatch.setattr(install, "download_archive", download)
    monkeypatch.setattr(install, "run_install", lambda *a: pytest.fail("unexpected mutation"))
    with pytest.raises(RuntimeError) as caught:
        install.provision("uv")
    assert len(downloads) == int(evidence == "corrupt")
    if evidence == "corrupt":
        assert str(caught.value) == "checked repair entered"
    else:
        assert caught.value.category == "native_runtime"


@pytest.mark.parametrize(
    "source,error",
    [
        (b"raise KeyboardInterrupt()\n", KeyboardInterrupt),
        (b"raise SystemExit(2)\n", SystemExit),
        (b"raise GeneratorExit()\n", GeneratorExit),
        (b"import asyncio; raise asyncio.CancelledError()\n", asyncio.CancelledError),
    ],
)
def test_import_control_flow_escapes_without_integrity_or_download(monkeypatch, tmp_path, source, error):
    native = importlib.import_module("core.windows_rocm.native")
    install = importlib.import_module("core.windows_rocm.install")
    package = "torchaudio"
    path = tmp_path / package / "__init__.py"
    path.parent.mkdir()
    path.write_bytes(source)
    monkeypatch.delitem(sys.modules, package, raising=False)
    monkeypatch.syspath_prepend(str(tmp_path))
    monkeypatch.setattr(native, "native_package_damage", lambda *a: pytest.fail("unexpected integrity check"))
    monkeypatch.setattr(install, "validate_target", lambda: None)
    monkeypatch.setattr(install, "verify_environment", lambda: native.import_native(package))
    monkeypatch.setattr(install, "download_archive", lambda *a: pytest.fail("unexpected download"))
    monkeypatch.setattr(install, "run_install", lambda *a: pytest.fail("unexpected mutation"))
    with pytest.raises(error):
        install.provision("uv")
