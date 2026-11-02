"""Host inference selects the pinned archive matching the runner architecture."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("machine,key", [("arm64", "macos_arm64_shared"), ("x86_64", "macos_x64_shared")])
def test_macos_sherpa_uses_the_matching_verified_archive(tmp_path, monkeypatch, machine, key):
    spec = importlib.util.spec_from_file_location("ios_host_builder", ROOT / "mobile/native/ios/build.py")
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    monkeypatch.setattr(builder.platform, "machine", lambda: machine)
    monkeypatch.setattr(builder, "BUILD", tmp_path)
    requested = []

    def download(url, target, checksum):
        requested.append((url, checksum))
        return target

    def extract(archive, output):
        library = output / "sherpa/lib/libsherpa-onnx-c-api.dylib"
        library.parent.mkdir(parents=True)
        library.touch()

    monkeypatch.setattr(builder, "download", download)
    monkeypatch.setattr(builder, "extract_tar", extract)
    monkeypatch.setattr(builder, "source", lambda **kwargs: (tmp_path / "source", []))
    monkeypatch.setattr(builder, "prepare_headers", lambda *args: None)
    library = builder.macos_sherpa()
    pin = builder.PINS["sherpa-onnx"][key]
    assert requested == [(pin["url"], pin["sha256"])]
    assert (tmp_path / "macos/sherpa-path.txt").read_text() == library
