from __future__ import annotations

import json
from pathlib import Path

import pytest
from services import local_model_paths as paths


def checkpoint(root: Path, engine="omnivoice") -> Path:
    root.mkdir()
    config = {"model_type": "omnivoice", "llm_config": {"model_type": "qwen2"}}
    if engine == "mlx-audio":
        config = {"model_type": "qwen3_tts", "tts_model_type": "voice_design",
                  "quantization": {"bits": 4, "group_size": 64}}
    (root / "config.json").write_text(json.dumps(config))
    (root / "model.safetensors").write_bytes(b"test fixture, not real tensors")
    (root / "tokenizer.json").write_text("{}")
    sub = root / ("audio_tokenizer" if engine == "omnivoice" else "speech_tokenizer")
    sub.mkdir()
    (sub / "config.json").write_text("{}")
    (sub / "model.safetensors").write_bytes(b"fixture")
    (sub / "preprocessor_config.json").write_text("{}")
    return root


def edit(root, **values):
    p = root / "config.json"
    p.write_text(json.dumps({**json.loads(p.read_text()), **values}))


@pytest.mark.parametrize("engine", ["omnivoice", "mlx-audio"])
def test_complete_local_checkpoint(engine, tmp_path):
    model = checkpoint(tmp_path / "model", engine)
    assert paths.validate("tts", engine, str(model)) == str(model.resolve())


@pytest.mark.parametrize("changes", [
    {"model_type": "not_omnivoice"}, {"model_type": "other", "_name_or_path": "voicestudio"},
])
def test_brand_substrings_are_not_architecture_validation(tmp_path, changes):
    model = checkpoint(tmp_path / "model")
    edit(model, **changes)
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(model))


@pytest.mark.parametrize("changes", [
    {"quantization": None, "_name_or_path": "mlx-community/Qwen3-VoiceDesign"},
    {"tts_model_type": "base", "_name_or_path": "mlx-community/Qwen3-VoiceDesign"},
    {"model_type": "not_a_supported_model", "_name_or_path": "mlx"},
])
def test_mlx_provenance_string_cannot_disguise_native_or_wrong_model(tmp_path, changes):
    model = checkpoint(tmp_path / "model", "mlx-audio")
    edit(model, **changes)
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "mlx-audio", str(model))


def test_shard_index_is_checked_instead_of_any_nested_weight(tmp_path):
    model = checkpoint(tmp_path / "model")
    (model / "model.safetensors").unlink()
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "missing.safetensors"}}))
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(model))


def test_sharded_checkpoint_rejects_empty_shard(tmp_path):
    model = checkpoint(tmp_path / "model")
    (model / "model.safetensors").unlink()
    (model / "model.safetensors.index.json").write_text(json.dumps({"weight_map": {"x": "shard.safetensors"}}))
    (model / "shard.safetensors").write_bytes(b"")
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(model))


@pytest.mark.parametrize("missing", ["tokenizer.json", "audio_tokenizer/model.safetensors"])
def test_incomplete_assets_cannot_trigger_tokenizer_download(tmp_path, missing):
    model = checkpoint(tmp_path / "model")
    (model / missing).unlink()
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(model))


def test_missing_invalid_and_unsupported(tmp_path):
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(tmp_path / "absent"))
    model = checkpoint(tmp_path / "model")
    (model / "config.json").write_text("not json")
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", str(model))
    with pytest.raises(paths.LocalModelError):
        paths.validate("asr", "faster-whisper", str(model))


def test_state_reports_file_validation_and_identity_without_loading(monkeypatch, tmp_path):
    model = checkpoint(tmp_path / "model")
    monkeypatch.setattr(paths, "get", lambda family, engine: str(model))
    state = paths.state("tts", "omnivoice")
    assert state["path"] == str(model)
    assert state["model_type"] == "omnivoice"
    assert state["validation"]["status"] == "valid"
    assert state["loading"]["status"] == "not_loaded"


def test_discovery_is_bounded_to_direct_children_and_never_selects(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    checkpoint(parent / "valid")
    (parent / "nested").mkdir()
    checkpoint(parent / "nested" / "hidden")
    (parent / "not-a-model.txt").write_text("ignore")
    candidates = paths.discover("tts", "omnivoice", str(parent))["candidates"]
    assert [item["name"] for item in candidates] == ["nested", "valid"]
    assert candidates[1]["validation"]["status"] == "valid"
    assert candidates[0]["validation"]["status"] == "invalid"
    assert paths.get("tts", "omnivoice") is None


def test_discovery_reports_actionable_incompatibility(tmp_path):
    parent = tmp_path / "parent"
    parent.mkdir()
    candidate = checkpoint(parent / "native", "mlx-audio")
    edit(candidate, tts_model_type="base")
    result = paths.discover("tts", "mlx-audio", str(parent))["candidates"][0]
    assert result["validation"]["code"] == "incompatible_architecture"
    assert "VoiceDesign" in result["validation"]["message"]


def test_persist_and_clear_survive_real_preferences_reload(monkeypatch, tmp_path):
    monkeypatch.setattr(paths.prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    model = checkpoint(tmp_path / "model")
    paths.set_path("tts", "omnivoice", str(model))
    assert json.loads((tmp_path / "prefs.json").read_text())[paths.PREF_KEY]["tts/omnivoice"] == str(model)
    assert paths.get("tts", "omnivoice") == str(model)
    paths.clear("tts", "omnivoice")
    assert paths.get("tts", "omnivoice") is None
