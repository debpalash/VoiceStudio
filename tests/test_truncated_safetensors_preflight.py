"""A cut-off .safetensors must be caught before mmap, not by an access violation.

transformers memory-maps weights; reading past EOF of a truncated file is a
Windows 0xC0000005 that kills the backend with no Python traceback
(#3015, #2960, #2957, #2382, #2250).
"""
from __future__ import annotations

import json
import struct

import pytest

from services import hf_cache_repair as repair
from services import model_manager


def _st(path, payload_bytes=64, keep=None):
    header = json.dumps({"w": {"dtype": "U8", "shape": [payload_bytes],
                               "data_offsets": [0, payload_bytes]}}).encode()
    data = struct.pack("<Q", len(header)) + header + b"\0" * payload_bytes
    path.write_bytes(data if keep is None else data[:keep])
    return path


def test_complete_file_is_not_truncated(tmp_path):
    assert not repair.is_truncated_safetensors(str(_st(tmp_path / "a.safetensors")))


@pytest.mark.parametrize("keep", [0, 5, 20, 100])
def test_cut_off_file_is_truncated(tmp_path, keep):
    assert repair.is_truncated_safetensors(str(_st(tmp_path / "a.safetensors", keep=keep)))


def test_find_truncated_ignores_healthy(tmp_path):
    _st(tmp_path / "ok.safetensors")
    bad = _st(tmp_path / "bad.safetensors", keep=90)
    assert repair.find_truncated_weights(str(tmp_path)) == [str(bad)]


def test_preflight_refuses_truncated_local_checkpoint(tmp_path):
    _st(tmp_path / "model.safetensors", keep=90)
    with pytest.raises(RuntimeError, match="incomplete"):
        model_manager._preflight_truncated_weights(str(tmp_path))


def test_preflight_passes_healthy_local_checkpoint(tmp_path):
    _st(tmp_path / "model.safetensors")
    model_manager._preflight_truncated_weights(str(tmp_path))


def test_repair_removes_truncated_blob_and_link(tmp_path, monkeypatch):
    from services import hf_revisions
    monkeypatch.delenv("HF_HUB_OFFLINE", raising=False)
    monkeypatch.delenv("TRANSFORMERS_OFFLINE", raising=False)
    monkeypatch.setitem(hf_revisions.CURATED_REVISIONS, "test/checkpoint", "a" * 40)
    repo = tmp_path / "models--test--checkpoint"
    (repo / "blobs").mkdir(parents=True)
    snap = repo / "snapshots" / "abc"
    snap.mkdir(parents=True)
    blob = _st(repo / "blobs" / "b1", keep=90)
    try:
        (snap / "model.safetensors").symlink_to(blob)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")
    calls = []
    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "snapshot_download", lambda **kw: calls.append(kw))
    out = repair.repair_repo_cache("test/checkpoint", cache_dir=str(tmp_path))
    assert out["found"] == 1 and out["restored"] and calls
    assert not blob.exists() and not (snap / "model.safetensors").is_symlink()
