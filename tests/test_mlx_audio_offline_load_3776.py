"""#3776: an mlx-audio load must resolve the installed snapshot, never the Hub.

Handed a repo id, mlx-audio's ``load_model`` ran an online ``snapshot_download``
inside the single MPS GPU-pool worker; the worker parked on a lock and every
later TTS request queued behind it (the #2700 class). The native loader is
mocked; the Hugging Face cache lookup is real and offline.
"""
import importlib
import sys
import types
from pathlib import Path

import pytest

KOKORO = "mlx-community/Kokoro-82M-bf16"
SHA = "a" * 40


@pytest.fixture
def env(monkeypatch, tmp_path):
    cache = tmp_path / "hub"
    monkeypatch.setenv("HF_HUB_CACHE", str(cache))
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    tts = importlib.import_module("services.tts_backend")
    paths = importlib.import_module("services.local_model_paths")
    monkeypatch.setattr(paths, "get", lambda *_: None)
    monkeypatch.setattr(tts, "_harden_mlx_audio_eos_ids", lambda: None)
    espeak = types.ModuleType("utils.espeak_data")
    espeak.ensure_espeak_data_path = lambda: None
    monkeypatch.setitem(sys.modules, "utils.espeak_data", espeak)

    calls = []
    loaded = types.SimpleNamespace(repo_id=None)

    def load_model(target, **kwargs):
        calls.append((target, kwargs))
        return loaded

    utils = types.ModuleType("mlx_audio.tts.utils")
    utils.load_model = load_model
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", utils)
    base = types.ModuleType("mlx_audio.utils")
    base.get_model_name_parts = lambda name: name.lower().split("/")[-1].split("-")
    monkeypatch.setitem(sys.modules, "mlx_audio.utils", base)

    backend = tts.MLXAudioBackend.__new__(tts.MLXAudioBackend)
    backend._model = None
    backend._model_id = KOKORO
    return backend, cache, calls, loaded


def _install(cache: Path, repo: str) -> Path:
    repo_dir = cache / ("models--" + repo.replace("/", "--"))
    snapshot = repo_dir / "snapshots" / SHA
    (snapshot / "voices").mkdir(parents=True)
    (snapshot / "config.json").write_text("{}", encoding="utf-8")
    (snapshot / "voices" / "pf_dora.safetensors").write_bytes(b"")
    (repo_dir / "voicestudio-revision").write_text(SHA + "\n", encoding="ascii")
    return snapshot


def test_installed_model_loads_from_the_local_snapshot(env):
    backend, cache, calls, loaded = env
    snapshot = _install(cache, KOKORO)

    backend._ensure_loaded()

    (target, kwargs), = calls
    assert isinstance(target, Path) and target.resolve() == snapshot.resolve()
    # Model-type detection still keys on the repo name, as before.
    assert kwargs == {"model_name_parts": ["kokoro", "82m", "bf16"]}
    # Kokoro's voices come from the installed repo, not prince-canuma/Kokoro-82M.
    assert backend._model is loaded and loaded.repo_id == KOKORO


def test_missing_model_fails_fast_without_reaching_the_loader(env):
    backend, _cache, calls, _loaded = env

    with pytest.raises(RuntimeError, match="Model Catalogue"):
        backend._ensure_loaded()

    assert calls == []
    assert backend._model is None


def test_local_folder_is_handed_through_unchanged(env, tmp_path):
    backend, _cache, calls, loaded = env
    folder = tmp_path / "my-kokoro"
    folder.mkdir()
    backend._model_id = str(folder)

    backend._ensure_loaded()

    assert calls == [(str(folder), {})]
    assert loaded.repo_id is None
