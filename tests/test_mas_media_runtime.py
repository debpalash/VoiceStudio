import hashlib
import importlib.util
import json
from pathlib import Path

import pytest


def stager():
    spec = importlib.util.spec_from_file_location("mas_media_runtime", Path(__file__).resolve().parents[1] / "scripts/stage_mas_media.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_staged_media_preserves_source_bytes_provenance_and_pending_clearance(tmp_path):
    source = tmp_path / "verified acquisition"
    source.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (source / name).write_bytes(name.encode())
    target = tmp_path / "app resources/media-tools"
    archive = {"url": "https://example.invalid/pinned.zip", "sha256": "a" * 64, "bytes": 12}
    stager().stage_media(source, target, archive)
    manifest = json.loads((target / "provenance.json").read_text())
    assert manifest["archive"] == archive
    assert manifest["redistributionApproved"] is False
    for item in manifest["files"]:
        assert (source / item["file"]).read_bytes() == (target / item["file"]).read_bytes()
        assert item["sourceSha256"] == item["sha256"] == hashlib.sha256((target / item["file"]).read_bytes()).hexdigest()
    with pytest.raises(FileExistsError):
        stager().stage_media(source, target, archive)


def test_media_staging_does_not_follow_unverified_symlink_inputs(tmp_path, symlink_or_skip):
    symlink_or_skip(tmp_path / "ffmpeg", "ffprobe")
    (tmp_path / "ffprobe").write_bytes(b"tool")
    with pytest.raises(ValueError):
        stager().stage_media(tmp_path, tmp_path / "output", {})
    assert not (tmp_path / "output").exists()
