"""Native loader failures require wheel evidence before package repair."""

import builtins
import importlib
from importlib import metadata
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("loader_error", [OSError("missing VC runtime"), RuntimeError("driver initialization")])
@pytest.mark.parametrize("damage", ["intact", "missing", "corrupt"])
def test_native_import_requires_confirmed_wheel_damage(monkeypatch, tmp_path, loader_error, damage):
    native = importlib.import_module("core.windows_rocm.native")
    path = tmp_path / "native.pyd"
    path.write_bytes(b"native binary" if damage == "intact" else b"damaged")
    recorded = metadata.PackagePath("torchaudio/native.pyd")
    recorded.hash = metadata.FileHash("sha256=vPKnZk6OrEn0bF4YxJFxLIrVCmInrg7BNIS6HIZTDDI")
    distribution = SimpleNamespace(files=[recorded], locate_file=lambda file: path)
    monkeypatch.setattr(native.metadata, "distribution", lambda name: distribution)
    if damage == "missing":
        path.unlink()
    original_import = builtins.__import__

    def importing(name, *args, **kwargs):
        if name == "torchaudio":
            raise loader_error
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", importing)
    with pytest.raises(RuntimeError) as caught:
        native.import_native("torchaudio")
    assert caught.value.category == ("native_runtime" if damage == "intact" else "native_import")
