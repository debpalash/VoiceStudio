"""MOSS snapshot resolution reuses legacy caches and completes partial installs."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, call

import pytest


@pytest.fixture
def sidecar():
    path = Path(__file__).resolve().parents[1] / "backend/engines/moss_tts_v15/main.py"
    spec = importlib.util.spec_from_file_location("moss_cache_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _sharded_snapshot(path):
    path.mkdir(parents=True, exist_ok=True)
    (path / "config.json").write_text("{}", encoding="utf-8")
    shards = ["model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors"]
    (path / "model.safetensors.index.json").write_text(
        json.dumps({"weight_map": dict(zip(["a", "b"], shards))}), encoding="utf-8",
    )
    for shard in shards:
        (path / shard).write_bytes(b"fixture")
    return shards


def test_legacy_cached_model_and_codec_load_without_hub_requests(sidecar, monkeypatch, tmp_path):
    """Exercise the real Hub cache resolver without a cached repository tree."""
    from huggingface_hub import HfApi, constants

    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(tmp_path))
    network = Mock(side_effect=AssertionError("cached synthesis must stay offline"))
    monkeypatch.setattr(HfApi, "list_repo_tree", network)
    for repo, revision in (
        (sidecar._DEFAULT_REPO, sidecar._DEFAULT_REVISION),
        (sidecar._CODEC_REPO, sidecar._CODEC_REVISION),
    ):
        path = tmp_path / ("models--" + repo.replace("/", "--")) / "snapshots" / revision
        _sharded_snapshot(path)
        assert Path(sidecar._snapshot_path(repo, revision)) == path
    network.assert_not_called()


@pytest.mark.parametrize("state", ["missing", "config_only", "partial", "invalid_index"])
def test_incomplete_cache_still_downloads_the_pinned_snapshot(sidecar, monkeypatch, tmp_path, state):
    import huggingface_hub
    from huggingface_hub.errors import LocalEntryNotFoundError

    cached = tmp_path / "cached"
    shards = _sharded_snapshot(cached)
    if state == "partial":
        (cached / shards[-1]).unlink()
    elif state == "config_only":
        for shard in shards:
            (cached / shard).unlink()
        (cached / "model.safetensors.index.json").unlink()
    elif state == "invalid_index":
        (cached / "model.safetensors.index.json").write_text("{", encoding="utf-8")
    first = LocalEntryNotFoundError("uncached") if state == "missing" else str(cached)
    download = Mock(side_effect=[first, str(tmp_path / "downloaded")])
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)
    repo, revision = sidecar._DEFAULT_REPO, sidecar._DEFAULT_REVISION

    assert sidecar._snapshot_path(repo, revision) == str(tmp_path / "downloaded")
    assert download.call_args_list == [
        call(repo, revision=revision, local_files_only=True), call(repo, revision=revision),
    ]


@pytest.mark.parametrize("name", ["model.safetensors", "pytorch_model.bin"])
def test_unsharded_cached_weights_are_recognized(sidecar, tmp_path, name):
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / name).write_bytes(b"fixture")
    assert sidecar._snapshot_has_weights(str(tmp_path))
