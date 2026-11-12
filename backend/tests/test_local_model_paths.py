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


def test_complete_local_qwen_base_checkpoint(tmp_path):
    model = checkpoint(tmp_path / "model", "mlx-audio")
    edit(model, tts_model_type="base")
    (model / "tokenizer.json").unlink()
    for name in ("tokenizer_config.json", "vocab.json", "merges.txt"):
        (model / name).write_text("fixture")
    assert paths.validate("tts", "mlx-audio", str(model)) == str(model.resolve())
    (model / "merges.txt").unlink()
    with pytest.raises(paths.LocalModelError, match="missing text tokenizer"):
        paths.validate("tts", "mlx-audio", str(model))


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
    {"tts_model_type": "custom_voice", "_name_or_path": "mlx-community/Qwen3-VoiceDesign"},
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
    edit(candidate, tts_model_type="custom_voice")
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


def _fake_engine_cache(monkeypatch, tmp_path):
    from services import model_manager
    from services import tts_backend as tts
    monkeypatch.setattr(paths.prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(paths, "_APPLIED", {})

    class Fake:
        unloaded = 0

        def unload(self):
            Fake.unloaded += 1

    cls = tts.get_backend_class("omnivoice")
    inst = Fake()
    monkeypatch.setitem(tts._ENGINE_INSTANCES, cls, inst)
    shared = []
    monkeypatch.setattr(model_manager, "unload_shared_model", lambda: shared.append(1) or True)
    monkeypatch.setattr(tts, "_retire_engine", lambda i: i.unload())
    return Fake, shared


def test_resaving_same_local_model_keeps_engine_warm(monkeypatch, tmp_path):
    Fake, shared = _fake_engine_cache(monkeypatch, tmp_path)
    model = checkpoint(tmp_path / "model")
    paths.change("tts", "omnivoice", str(model))  # real change: reloads
    assert shared == [1]
    from services import tts_backend as tts
    cls = tts.get_backend_class("omnivoice")
    tts._ENGINE_INSTANCES[cls] = Fake()
    Fake.unloaded = 0
    paths.change("tts", "omnivoice", str(model))  # same value: no-op
    paths.change("tts", "omnivoice", str(model))
    assert shared == [1] and Fake.unloaded == 0
    assert cls in tts._ENGINE_INSTANCES


def test_clearing_unset_local_model_keeps_engine_warm(monkeypatch, tmp_path):
    Fake, shared = _fake_engine_cache(monkeypatch, tmp_path)
    paths.change("tts", "omnivoice", None)
    assert shared == [] and Fake.unloaded == 0


def test_changing_or_clearing_local_model_still_reloads(monkeypatch, tmp_path):
    _, shared = _fake_engine_cache(monkeypatch, tmp_path)
    a = checkpoint(tmp_path / "a")
    b = checkpoint(tmp_path / "b")
    paths.change("tts", "omnivoice", str(a))
    paths.change("tts", "omnivoice", str(b))
    paths.change("tts", "omnivoice", None)
    assert len(shared) == 3


def test_resaving_after_replacing_the_weights_reloads(monkeypatch, tmp_path):
    """Re-saving the same folder used to be how a user picked up swapped weights."""
    import os
    _, shared = _fake_engine_cache(monkeypatch, tmp_path)
    model = checkpoint(tmp_path / "model")
    paths.change("tts", "omnivoice", str(model))
    newer = max(e.stat().st_mtime_ns for e in os.scandir(model)) + 10**9
    replaced = next(f for f in sorted(model.rglob("*")) if f.is_file())
    os.utime(replaced, ns=(newer, newer))
    paths.change("tts", "omnivoice", str(model))
    assert len(shared) == 2


def test_replacing_a_nested_tokenizer_file_reloads(monkeypatch, tmp_path):
    """Nested assets count, and any change counts, not only a newer maximum (#4043 review)."""
    import os
    _, shared = _fake_engine_cache(monkeypatch, tmp_path)
    model = checkpoint(tmp_path / "model")
    nested = model / "audio_tokenizer"
    nested.mkdir(exist_ok=True)
    weights = nested / "model.safetensors"
    weights.write_bytes(b"v1")
    old = 10**18
    os.utime(weights, ns=(old, old))  # newest file in the folder
    paths.change("tts", "omnivoice", str(model))
    weights.write_bytes(b"v2, a different size")
    os.utime(weights, ns=(old - 10**9, old - 10**9))  # older than before: max() would miss it
    paths.change("tts", "omnivoice", str(model))
    assert len(shared) == 2


def test_first_resave_after_restart_keeps_a_loaded_engine_warm(monkeypatch, tmp_path):
    """The loader records the folder it loaded, so an unchanged re-save is a no-op (#4043 review)."""
    _, shared = _fake_engine_cache(monkeypatch, tmp_path)
    model = checkpoint(tmp_path / "model")
    paths.set_path("tts", "omnivoice", str(model))  # saved in an earlier session
    snap = paths.loading_fingerprint(str(model))  # loader snapshots, then loads
    paths.note_loaded("tts", "omnivoice", str(model), snap)  # after a successful load
    paths.change("tts", "omnivoice", str(model))
    assert shared == []


def test_a_huge_folder_tree_is_not_walked_in_full(monkeypatch, tmp_path):
    """Empty subfolders count toward the bound, not only files (#4043 review)."""
    root = tmp_path / "many"
    root.mkdir()
    for i in range(30):
        (root / f"d{i}").mkdir()
    monkeypatch.setattr(paths, "_FINGERPRINT_MAX_FILES", 10)
    a = paths._weights_fingerprint(str(root))
    b = paths._weights_fingerprint(str(root))
    assert a != b  # over the bound: never treated as unchanged


def test_a_load_of_a_folder_no_longer_saved_is_not_recorded(monkeypatch, tmp_path):
    """An overlapping load must not record weights the saved folder no longer names (#4043 review)."""
    _, shared = _fake_engine_cache(monkeypatch, tmp_path)
    a = checkpoint(tmp_path / "a")
    b = checkpoint(tmp_path / "b")
    snap = paths.loading_fingerprint(str(a))  # a load of A starts
    paths.change("tts", "omnivoice", str(b))  # the user switches to B meanwhile
    paths.note_loaded("tts", "omnivoice", str(a), snap)  # A's load finishes late
    paths.change("tts", "omnivoice", str(b))  # re-saving B is unchanged: no extra reload
    assert len(shared) == 1
    assert paths._APPLIED[("tts", "omnivoice")][0] == str(b)


def test_an_unreadable_file_never_looks_unchanged(monkeypatch, tmp_path):
    """A stat error must force a reload, not drop the file from the comparison (#4043 review)."""
    model = checkpoint(tmp_path / "model")
    real_stat = paths.os.stat

    def failing_stat(target, *args, **kwargs):
        if str(target).endswith("model.safetensors"):
            raise PermissionError("locked")
        return real_stat(target, *args, **kwargs)

    monkeypatch.setattr(paths.os, "stat", failing_stat)
    assert paths._weights_fingerprint(str(model)) != paths._weights_fingerprint(str(model))
