"""Real router/auth/capability/prefs tests; only tensor runtime is stubbed.

No main-app lifespan, torch, model download, or inference is exercised here.
"""
import asyncio
import json
import secrets
import sys
import threading
import types
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import local_model_paths as paths
from backend.tests.test_local_model_paths import checkpoint


@pytest.fixture
def api(monkeypatch, tmp_path):
    # tts_backend uses torch only when doing inference; do not install gigabytes
    # to test settings and identity dispatch. Restore this import after the test.
    monkeypatch.setitem(sys.modules, "torch", types.ModuleType("torch"))
    from api.routers import settings
    from core import path_authorization as auth
    from services import model_manager as mm, tts_backend as tts
    monkeypatch.setattr(paths.prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(auth, "_AUTH_DIR", str(tmp_path / "caps"))
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock())
    monkeypatch.setattr(mm, "_model_load_thread_lock", threading.RLock())
    monkeypatch.setattr(mm, "unload_shared_model", Mock())
    monkeypatch.setattr(tts, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts, "_ENGINE_IN_USE", {})
    monkeypatch.setattr(tts, "_active_instance", None)
    monkeypatch.setattr(tts, "_active_instance_id", None)
    monkeypatch.delenv("OMNIVOICE_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    app = FastAPI()
    app.include_router(settings.router)
    client = TestClient(app, client=("127.0.0.1", 12345))

    def capability(path, kind="models_dir"):
        root = tmp_path / "caps"
        root.mkdir(exist_ok=True)
        token = secrets.token_hex(32)
        (root / f"{token}.json").write_text(json.dumps({"token": token, "kind": kind, "path": str(path)}))
        return {"authorization": token}
    return client, capability, mm, tts, app


URL = "/api/settings/engines/tts/omnivoice/local-model"


def test_api_requires_admin_and_native_capability(api, tmp_path):
    client, cap, mm, tts, app = api
    remote = TestClient(app, client=("203.0.113.9", 9999))
    for method in ("get", "delete", "put"):
        kwargs = {"json": {"authorization": "forged"}} if method == "put" else {}
        assert getattr(remote, method)(URL, **kwargs).status_code == 403
    assert client.put(URL, json={"path": str(tmp_path)}).status_code == 422
    assert client.put(URL, json={"authorization": "forged"}).status_code == 403
    assert client.put(URL, json=cap(tmp_path, "ffmpeg")).status_code == 403
    assert mm.unload_shared_model.call_count == 0


@pytest.mark.parametrize("engine", ["omnivoice", "mlx-audio"])
def test_real_lazy_cached_backend_is_not_loaded(api, monkeypatch, engine):
    client, cap, mm, tts, app = api
    monkeypatch.setattr(mm, "model", None)
    cls = tts.OmniVoiceBackend if engine == "omnivoice" else tts.MLXAudioBackend
    monkeypatch.setattr(tts, "get_backend_class", lambda _: cls)
    tts._ENGINE_INSTANCES[cls] = cls()
    result = client.get(URL.replace("omnivoice", engine)).json()
    assert result["loading"]["status"] == "not_loaded"


@pytest.mark.parametrize("engine", ["omnivoice", "mlx-audio"])
@pytest.mark.parametrize("identity,expected", [("current", "loaded"), ("old", "not_loaded"), (None, "unknown")])
def test_loaded_requires_actual_model_and_current_identity(api, monkeypatch, tmp_path, engine, identity, expected):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model", engine)
    monkeypatch.setattr(paths, "get", lambda *_: str(model))
    cls = tts.OmniVoiceBackend if engine == "omnivoice" else tts.MLXAudioBackend
    monkeypatch.setattr(tts, "get_backend_class", lambda _: cls)
    backend = cls()
    actual = str(model) if identity == "current" else identity
    if engine == "omnivoice":
        backend._model = types.SimpleNamespace(_voicestudio_checkpoint=actual)
        monkeypatch.setattr(mm, "model", backend._model)
    else:
        backend._model = object()
        backend._model_id = actual
    tts._ENGINE_INSTANCES[cls] = backend
    assert client.get(URL.replace("omnivoice", engine)).json()["loading"]["status"] == expected


def test_api_validation_one_shot_error_persistence_and_clear(api, tmp_path):
    client, cap, mm, tts, app = api
    bad = cap(tmp_path / "missing")
    assert client.put(URL, json=bad).status_code == 400
    assert client.put(URL, json=bad).status_code == 403
    assert paths.get("tts", "omnivoice") is None
    assert mm.unload_shared_model.call_count == 0
    model = checkpoint(tmp_path / "model")
    token = cap(model)
    assert client.put(URL, json=token).status_code == 200
    assert client.put(URL, json=token).status_code == 403
    assert client.get(URL).json()["path"] == str(model)
    assert json.loads((tmp_path / "prefs.json").read_text())[paths.PREF_KEY]["tts/omnivoice"] == str(model)
    assert mm.resolve_omnivoice_checkpoint() == str(model)
    assert client.delete(URL).status_code == 200
    assert client.get(URL).json()["path"] is None
    assert model.is_dir()  # Reset does not delete the user's checkpoint.


@pytest.mark.parametrize("engine", ["omnivoice", "mlx-audio"])
def test_busy_lease_rejects_put_and_clear_without_writing_or_unloading(api, tmp_path, engine):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model", engine)
    cls = tts.get_backend_class(engine)
    instance = Mock()
    tts._ENGINE_INSTANCES[cls] = instance
    tts._ENGINE_IN_USE[cls] = 1
    url = f"/api/settings/engines/tts/{engine}/local-model"
    assert client.put(url, json=cap(model)).status_code == 409
    assert client.delete(url).status_code == 409
    assert paths.get("tts", engine) is None
    instance.unload.assert_not_called()
    mm.unload_shared_model.assert_not_called()
    tts._ENGINE_IN_USE.clear()
    assert client.put(url, json=cap(model)).status_code == 200
    instance.unload.assert_called_once()
    assert cls not in tts._ENGINE_INSTANCES


def test_generation_and_cold_load_are_busy(api, tmp_path):
    client, cap, mm, tts, app = api
    asyncio.run(mm._model_lock.acquire())
    assert client.delete(URL).status_code == 409
    mm._model_lock.release()
    started, release = threading.Event(), threading.Event()
    def loading():
        with mm._model_load_thread_lock:
            started.set()
            release.wait(5)
    thread = threading.Thread(target=loading)
    thread.start()
    assert started.wait(2)
    try:
        assert client.delete(URL).status_code == 409
    finally:
        release.set()
        thread.join()
    mm.unload_shared_model.assert_not_called()


def test_unrelated_active_engine_is_not_reset(api, tmp_path, monkeypatch):
    client, cap, mm, tts, app = api
    active = Mock()
    monkeypatch.setattr(tts, "_active_instance", active)
    monkeypatch.setattr(tts, "_active_instance_id", "piper")
    assert client.put(URL, json=cap(checkpoint(tmp_path / "model"))).status_code == 200
    active.unload.assert_not_called()


def test_environment_override_is_explicit_and_authoritative(api, tmp_path, monkeypatch):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    assert client.put(URL, json=cap(model)).status_code == 200
    monkeypatch.setenv("OMNIVOICE_MODEL", "operator/checkpoint")
    state = client.get(URL).json()
    assert state["path"] == str(model)
    assert state["environment_override"] == "OMNIVOICE_MODEL"
    assert state["effective_override"] == "operator/checkpoint"
    assert mm.resolve_omnivoice_checkpoint() == "operator/checkpoint"
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "kokoro")
    url = URL.replace("omnivoice", "mlx-audio")
    assert client.get(url).json()["environment_override"] == "OMNIVOICE_MLX_AUDIO_MODEL"
    assert tts.MLXAudioBackend.configured_identity() == tts.MLXAudioBackend.CURATED_MODELS["kokoro"]


def test_unsupported_engine(api):
    client, *_ = api
    for method in ("get", "delete", "put"):
        kwargs = {"json": {"authorization": "forged"}} if method == "put" else {}
        assert getattr(client, method)(URL.replace("omnivoice", "piper"), **kwargs).status_code == 404


def test_real_mlx_loader_receives_local_identity_and_never_falls_back(api, tmp_path, monkeypatch):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model", "mlx-audio")
    url = URL.replace("omnivoice", "mlx-audio")
    assert client.put(url, json=cap(model)).status_code == 200
    load = Mock(side_effect=RuntimeError("offline fixture loader"))
    # The native library boundary is mocked; execute the real backend method.
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", types.SimpleNamespace(load_model=load))
    monkeypatch.setattr(tts, "_harden_mlx_audio_eos_ids", lambda: None)
    backend = tts.MLXAudioBackend()
    assert backend.model_identity() == str(model)
    with pytest.raises(RuntimeError, match="offline fixture"):
        backend._ensure_loaded()
    load.assert_called_once_with(str(model))
    (model / "config.json").unlink()
    load.reset_mock()
    with pytest.raises(paths.LocalModelError):
        backend._ensure_loaded()
    load.assert_not_called()
    with pytest.raises(paths.LocalModelError):
        tts.MLXAudioBackend.configured_identity()


@pytest.mark.parametrize("count,partial", [(100, False), (101, True)])
def test_discovery_api_exact_candidate_boundary(api, tmp_path, count, partial):
    client, cap, mm, tts, app = api
    parent = tmp_path / "models"
    parent.mkdir()
    for i in range(count):
        (parent / str(i)).mkdir()
    response = client.post(URL + "/discover", json=cap(parent))
    assert response.status_code == 200
    result = response.json()
    assert result["parent"] == str(parent)
    assert len(result["candidates"]) == 100
    assert result["partial"] is partial
    assert result["limited"] is partial


def test_discovered_candidate_grant_selects_exact_path_once(api, tmp_path):
    client, cap, mm, tts, app = api
    parent = tmp_path / "models"
    parent.mkdir()
    model = checkpoint(parent / "model")
    (parent / "invalid").mkdir()
    result = client.post(URL + "/discover", json=cap(parent)).json()
    valid = next(c for c in result["candidates"] if c["validation"]["status"] == "valid")
    invalid = next(c for c in result["candidates"] if c["validation"]["status"] == "invalid")
    assert "authorization" not in invalid
    token = {"authorization": valid["authorization"]}
    # A renderer-provided path must never override the exact derived target.
    assert client.put(URL, json={**token, "path": str(tmp_path / "unrelated")}).status_code == 200
    assert paths.get("tts", "omnivoice") == str(model)
    assert client.put(URL, json=token).status_code == 403


def test_discovered_grant_is_engine_bound_and_cannot_change_general_models_dir(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    def grant():
        result = client.post(URL + "/discover", json=cap(model)).json()
        return {"authorization": next(c for c in result["candidates"] if c["validation"]["status"] == "valid")["authorization"]}
    token = grant()
    assert client.put(URL.replace("omnivoice", "mlx-audio"), json=token).status_code == 403
    assert client.put(URL, json=token).status_code == 403  # mismatch also consumes
    assert client.post(URL + "/discover", json=grant()).status_code == 403
    from core import path_authorization as auth
    with pytest.raises(auth.PathAuthorizationError):
        auth.consume(grant()["authorization"], "models_dir")
    assert paths.get("tts", "omnivoice") is None


def test_discovered_grant_revalidates_on_selection(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model)).json()["candidates"] if c["validation"]["status"] == "valid")
    (model / "model.safetensors").unlink()
    token = {"authorization": candidate["authorization"]}
    assert client.put(URL, json=token).status_code == 400
    assert client.put(URL, json=token).status_code == 403
    assert paths.get("tts", "omnivoice") is None


def test_discovery_is_read_only_and_capability_scoped(api, tmp_path, monkeypatch):
    import socket
    import subprocess
    client, cap, mm, tts, app = api
    parent = tmp_path / "models"
    parent.mkdir()
    model = checkpoint(parent / "model")
    (model / "model.py").write_text("raise AssertionError('must not execute checkpoint code')")
    before = {str(p.relative_to(parent)): p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    forbidden = Mock(side_effect=AssertionError("discovery must not write, execute, load, or download"))
    monkeypatch.setattr(paths.prefs, "set_", forbidden)
    monkeypatch.setattr(paths.prefs, "update_mapping", forbidden)
    monkeypatch.setattr(mm, "get_model", forbidden)
    monkeypatch.setattr(tts.OmniVoiceBackend, "_ensure_loaded", forbidden)
    monkeypatch.setattr(tts.MLXAudioBackend, "_ensure_loaded", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(socket.socket, "connect", forbidden)
    token = cap(parent)
    response = client.post(URL + "/discover", json=token)
    assert response.status_code == 200
    assert response.json()["candidates"][0]["validation"]["status"] == "valid"
    assert client.post(URL + "/discover", json=token).status_code == 403
    assert client.post(URL + "/discover", json={"path": str(parent)}).status_code == 422
    remote = TestClient(app, client=("203.0.113.9", 9999))
    assert remote.post(URL + "/discover", json=cap(parent)).status_code == 403
    forbidden.assert_not_called()
    mm.unload_shared_model.assert_not_called()
    assert tts._ENGINE_INSTANCES == {}
    assert before == {str(p.relative_to(parent)): p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    assert not (tmp_path / "prefs.json").exists()


def test_missing_persisted_omnivoice_does_not_resolve_default(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    assert client.put(URL, json=cap(model)).status_code == 200
    (model / "config.json").unlink()
    with pytest.raises(paths.LocalModelError):
        mm.resolve_omnivoice_checkpoint()


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_discovered_grant_rejects_replaced_root(api, tmp_path, replacement):
    client, cap, mm, *_ = api
    model = checkpoint(tmp_path / "model")
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model)).json()["candidates"] if c["validation"]["status"] == "valid")
    model.rename(tmp_path / "original")
    if replacement == "symlink":
        model.symlink_to(checkpoint(tmp_path / "other"), target_is_directory=True)
    else:
        checkpoint(model)
    response = client.put(URL, json={"authorization": candidate["authorization"]})
    assert response.status_code == 403
    assert paths.get("tts", "omnivoice") is None
    mm.unload_shared_model.assert_not_called()


@pytest.mark.parametrize("discovered", [False, True])
@pytest.mark.parametrize("asset", ["config.json", "model.safetensors", "tokenizer.json", "audio_tokenizer", "audio_tokenizer/model.safetensors", "audio_tokenizer/preprocessor_config.json"])
def test_linked_checkpoint_assets_cannot_escape(api, tmp_path, asset, discovered):
    client, cap, mm, *_ = api
    model = checkpoint(tmp_path / "model")
    token = cap(model)
    if discovered:
        candidate = next(c for c in client.post(URL + "/discover", json=token).json()["candidates"] if c["validation"]["status"] == "valid")
        token = {"authorization": candidate["authorization"]}
    target = model / asset
    outside = tmp_path / "outside"
    target.rename(outside)
    target.symlink_to(outside, target_is_directory=outside.is_dir())
    response = client.put(URL, json=token)
    assert response.status_code == 400
    assert "symlink" in response.json()["detail"].lower()
    assert "copy" in response.json()["detail"].lower()
    assert paths.get("tts", "omnivoice") is None


def test_discovered_identity_is_rechecked_immediately_before_persistence(api, tmp_path, monkeypatch):
    client, cap, mm, *_ = api
    model = checkpoint(tmp_path / "model")
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model)).json()["candidates"] if c["validation"]["status"] == "valid")
    validate = paths.validate

    def swap_after_validation(*args, **kwargs):
        result = validate(*args, **kwargs)
        model.rename(tmp_path / "original")
        checkpoint(model)
        return result

    monkeypatch.setattr(paths, "validate", swap_after_validation)
    response = client.put(URL, json={"authorization": candidate["authorization"]})
    assert response.status_code == 403
    assert paths.get("tts", "omnivoice") is None
    mm.unload_shared_model.assert_not_called()
