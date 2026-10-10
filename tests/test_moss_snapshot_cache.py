"""MOSS snapshot resolution reuses legacy caches and completes partial installs."""
import importlib.util
import json
from pathlib import Path
from unittest.mock import Mock, call

import pytest


@pytest.fixture
def sidecar():
    """Import the standalone runner without loading the parent engine package."""
    path = Path(__file__).resolve().parents[1] / "backend/engines/moss_tts_v15/main.py"
    spec = importlib.util.spec_from_file_location("moss_cache_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _loading_files(path, *, codec=False):
    """Model the pinned repositories' loading metadata and relative imports."""
    if codec:
        modules = {
            "configuration_moss_audio_tokenizer.py": "",
            "modeling_moss_audio_tokenizer.py": "from .configuration_moss_audio_tokenizer import Config\n",
        }
        configs = {"config.json": {"auto_map": {
            "AutoConfig": "configuration_moss_audio_tokenizer.Config",
            "AutoModel": "modeling_moss_audio_tokenizer.Model",
        }}}
    else:
        modules = {
            "configuration_moss_tts.py": "",
            "modeling_moss_tts.py": "from .configuration_moss_tts import Config\nfrom .inference_utils import sample\n",
            "processing_moss_tts.py": "from .configuration_moss_tts import Config\nfrom .tts_robust_normalizer_single_script import normalize\n",
            "inference_utils.py": "",
            "tts_robust_normalizer_single_script.py": "",
        }
        configs = {
            "config.json": {"auto_map": {
                "AutoConfig": "configuration_moss_tts.Config",
                "AutoModel": "modeling_moss_tts.Model",
            }},
            "processor_config.json": {"auto_map": {
                "AutoProcessor": "processing_moss_tts.Processor",
            }},
            "tokenizer_config.json": {"tokenizer_class": "Qwen2Tokenizer"},
            "tokenizer.json": {},
        }
        (path / "chat_template.jinja").write_text("{{ messages }}", encoding="utf-8")
    for name, content in modules.items():
        (path / name).write_text(content, encoding="utf-8")
    for name, content in configs.items():
        (path / name).write_text(json.dumps(content), encoding="utf-8")


def _sharded_snapshot(path, *, codec=False):
    """Create a complete snapshot with two small fixture weight shards."""
    path.mkdir(parents=True, exist_ok=True)
    _loading_files(path, codec=codec)
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
        _sharded_snapshot(path, codec=repo == sidecar._CODEC_REPO)
        assert Path(sidecar._snapshot_path(repo, revision)) == path
    network.assert_not_called()


@pytest.mark.parametrize("state", ["missing", "config_only", "partial", "invalid_index"])
def test_incomplete_cache_still_downloads_the_pinned_snapshot(sidecar, monkeypatch, tmp_path, state):
    """Missing or invalid weight files resume at the same immutable revision."""
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


@pytest.mark.parametrize("codec,name", [
    (False, "config.json"),
    (False, "processor_config.json"),
    (False, "tokenizer_config.json"),
    (False, "tokenizer.json"),
    (False, "chat_template.jinja"),
    (False, "configuration_moss_tts.py"),
    (False, "modeling_moss_tts.py"),
    (False, "processing_moss_tts.py"),
    (False, "inference_utils.py"),
    (False, "tts_robust_normalizer_single_script.py"),
    (True, "configuration_moss_audio_tokenizer.py"),
    (True, "modeling_moss_audio_tokenizer.py"),
])
def test_complete_weights_with_missing_loading_file_resume_download(
    sidecar, monkeypatch, tmp_path, codec, name,
):
    """Complete weights must not hide missing tokenizer, processor or code files."""
    import huggingface_hub

    cached = tmp_path / "cached"
    _sharded_snapshot(cached, codec=codec)
    (cached / name).unlink()
    repo, revision = (
        (sidecar._CODEC_REPO, sidecar._CODEC_REVISION) if codec
        else (sidecar._DEFAULT_REPO, sidecar._DEFAULT_REVISION)
    )
    download = Mock(side_effect=[str(cached), str(tmp_path / "downloaded")])
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)

    assert sidecar._snapshot_path(repo, revision) == str(tmp_path / "downloaded")
    assert download.call_args_list == [
        call(repo, revision=revision, local_files_only=True), call(repo, revision=revision),
    ]


def test_missing_transitive_code_dependency_resumes_download(sidecar, monkeypatch, tmp_path):
    """Follow relative imports beyond the modules directly named in auto_map."""
    import huggingface_hub

    cached = tmp_path / "cached"
    _sharded_snapshot(cached)
    (cached / "inference_utils.py").write_text("from .sampler import sample\n", encoding="utf-8")
    download = Mock(side_effect=[str(cached), str(tmp_path / "downloaded")])
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)

    assert sidecar._snapshot_path(sidecar._DEFAULT_REPO, sidecar._DEFAULT_REVISION) == str(tmp_path / "downloaded")
    assert download.call_count == 2


@pytest.mark.parametrize("alternative", ["slow_tokenizer", "inline_template", "custom_processor"])
def test_complete_alternative_loading_assets_stay_offline(sidecar, monkeypatch, tmp_path, alternative):
    """Accept valid tokenizer fallbacks and audited processors with renamed modules."""
    import huggingface_hub

    _sharded_snapshot(tmp_path)
    repo, revision = sidecar._DEFAULT_REPO, sidecar._DEFAULT_REVISION
    if alternative == "slow_tokenizer":
        (tmp_path / "tokenizer.json").unlink()
        (tmp_path / "vocab.json").write_text("{}", encoding="utf-8")
        (tmp_path / "merges.txt").write_text("#version: 0.2\n", encoding="utf-8")
    elif alternative == "inline_template":
        (tmp_path / "chat_template.jinja").unlink()
        (tmp_path / "tokenizer_config.json").write_text(
            json.dumps({"chat_template": "{{ messages }}"}), encoding="utf-8",
        )
    else:
        repo, revision = "someone/audited-moss", "b" * 40
        (tmp_path / "processing_moss_tts.py").rename(tmp_path / "custom_processor.py")
        (tmp_path / "processor_config.json").write_text(
            json.dumps({"auto_map": {"AutoProcessor": "custom_processor.Processor"}}), encoding="utf-8",
        )
    download = Mock(return_value=str(tmp_path))
    monkeypatch.setattr(huggingface_hub, "snapshot_download", download)

    assert sidecar._snapshot_path(repo, revision) == str(tmp_path)
    download.assert_called_once_with(repo, revision=revision, local_files_only=True)


@pytest.mark.parametrize("name", ["model.safetensors", "pytorch_model.bin"])
def test_unsharded_cached_weights_are_recognized(sidecar, tmp_path, name):
    """Both Transformers single-file weight formats remain reusable."""
    (tmp_path / "config.json").write_text("{}", encoding="utf-8")
    (tmp_path / name).write_bytes(b"fixture")
    assert sidecar._snapshot_has_weights(str(tmp_path))
