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

    def capability(path, kind="models_dir", scope=None):
        root = tmp_path / "caps"
        root.mkdir(exist_ok=True)
        token = secrets.token_hex(32)
        (root / f"{token}.json").write_text(json.dumps({"token": token, "kind": kind, "path": str(path), **({"scope": scope} if scope else {})}))
        return {"authorization": token}
    return client, capability, mm, tts, app


URL = "/api/settings/engines/tts/omnivoice/local-model"


def sel(path, engine="omnivoice"):
    """The engine-bound selection grant the backend issues after discovery."""
    from core import path_authorization as auth
    path.mkdir(exist_ok=True)
    return {"authorization": auth.issue_local_model_selection(str(path), "tts", engine)}


def test_api_requires_admin_and_native_capability(api, tmp_path):
    client, cap, mm, tts, app = api
    remote = TestClient(app, client=("203.0.113.9", 9999))
    for method in ("get", "delete", "put"):
        kwargs = {"json": {"authorization": "forged"}} if method == "put" else {}
        assert getattr(remote, method)(URL, **kwargs).status_code == 403
    response = client.put(URL, json={"path": str(tmp_path)})
    assert response.status_code == 422
    response = client.put(URL, json={"authorization": "forged"})
    assert response.status_code == 403
    response = client.put(URL, json=cap(tmp_path, "ffmpeg"))
    assert response.status_code == 403
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
    bad = sel(tmp_path / "empty")
    response = client.put(URL, json=bad)
    assert response.status_code == 400
    response = client.put(URL, json=bad)
    assert response.status_code == 403
    assert paths.get("tts", "omnivoice") is None
    assert mm.unload_shared_model.call_count == 0
    model = checkpoint(tmp_path / "model")
    token = sel(model)
    response = client.put(URL, json=token)
    assert response.status_code == 200
    response = client.put(URL, json=token)
    assert response.status_code == 403
    assert client.get(URL).json()["path"] == str(model)
    assert json.loads((tmp_path / "prefs.json").read_text())[paths.PREF_KEY]["tts/omnivoice"] == str(model)
    assert mm.resolve_omnivoice_checkpoint() == str(model)
    response = client.delete(URL)
    assert response.status_code == 200
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
    response = client.put(url, json=sel(model, engine))
    assert response.status_code == 409
    response = client.delete(url)
    assert response.status_code == 409
    assert paths.get("tts", engine) is None
    instance.unload.assert_not_called()
    mm.unload_shared_model.assert_not_called()
    tts._ENGINE_IN_USE.clear()
    response = client.put(url, json=sel(model, engine))
    assert response.status_code == 200
    instance.unload.assert_called_once()
    assert cls not in tts._ENGINE_INSTANCES


def test_generation_and_cold_load_are_busy(api, tmp_path):
    client, cap, mm, tts, app = api
    asyncio.run(mm._model_lock.acquire())
    response = client.delete(URL)
    assert response.status_code == 409
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
        response = client.delete(URL)
        assert response.status_code == 409
    finally:
        release.set()
        thread.join()
    mm.unload_shared_model.assert_not_called()


def test_unrelated_active_engine_is_not_reset(api, tmp_path, monkeypatch):
    client, cap, mm, tts, app = api
    active = Mock()
    monkeypatch.setattr(tts, "_active_instance", active)
    monkeypatch.setattr(tts, "_active_instance_id", "piper")
    response = client.put(URL, json=sel(checkpoint(tmp_path / "model")))
    assert response.status_code == 200
    active.unload.assert_not_called()


def test_environment_override_is_explicit_and_authoritative(api, tmp_path, monkeypatch):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    response = client.put(URL, json=sel(model))
    assert response.status_code == 200
    monkeypatch.setenv("OMNIVOICE_MODEL", "operator/checkpoint")
    state = client.get(URL).json()
    assert state["path"] == str(model)
    override_name = state["environment_override"]
    assert override_name == "OMNIVOICE_MODEL"
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
    response = client.put(url, json=sel(model, "mlx-audio"))
    assert response.status_code == 200
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
    # /engines constructs the backend: a vanished folder must not raise there,
    # or the controls that clear it disappear. Loading still refuses (above).
    assert tts.MLXAudioBackend.configured_identity() == str(model)
    assert tts.MLXAudioBackend().model_identity() == str(model)


@pytest.mark.parametrize("count,partial", [(100, False), (101, True)])
def test_discovery_api_exact_candidate_boundary(api, tmp_path, count, partial):
    client, cap, mm, tts, app = api
    parent = tmp_path / "models"
    parent.mkdir()
    for i in range(count):
        (parent / str(i)).mkdir()
    response = client.post(URL + "/discover", json=cap(parent, scope="local-model-discover:omnivoice"))
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
    result = client.post(URL + "/discover", json=cap(parent, scope="local-model-discover:omnivoice")).json()
    valid = next(c for c in result["candidates"] if c["validation"]["status"] == "valid")
    invalid = next(c for c in result["candidates"] if c["validation"]["status"] == "invalid")
    assert "authorization" not in invalid
    token = {"authorization": valid["authorization"]}
    # A renderer-provided path must never override the exact derived target.
    response = client.put(URL, json={**token, "path": str(tmp_path / "unrelated")})
    assert response.status_code == 200
    assert paths.get("tts", "omnivoice") == str(model)
    response = client.put(URL, json=token)
    assert response.status_code == 403


def test_discovered_grant_is_engine_bound_and_cannot_change_general_models_dir(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    def grant():
        result = client.post(URL + "/discover", json=cap(model, scope="local-model-discover:omnivoice")).json()
        return {"authorization": next(c for c in result["candidates"] if c["validation"]["status"] == "valid")["authorization"]}
    token = grant()
    response = client.put(URL.replace("omnivoice", "mlx-audio"), json=token)
    assert response.status_code == 403
    response = client.put(URL, json=token)  # mismatch also consumes
    assert response.status_code == 403
    response = client.post(URL + "/discover", json=grant())
    assert response.status_code == 403
    from core import path_authorization as auth
    with pytest.raises(auth.PathAuthorizationError):
        auth.consume(grant()["authorization"], "models_dir")
    assert paths.get("tts", "omnivoice") is None


def test_discovered_grant_revalidates_on_selection(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model, scope="local-model-discover:omnivoice")).json()["candidates"] if c["validation"]["status"] == "valid")
    (model / "model.safetensors").unlink()
    token = {"authorization": candidate["authorization"]}
    response = client.put(URL, json=token)
    assert response.status_code == 400
    response = client.put(URL, json=token)
    assert response.status_code == 403
    assert paths.get("tts", "omnivoice") is None


def test_read_only_picker_grant_cannot_change_global_dir_or_select_a_model(api, tmp_path):
    """Review of #3702: the discovery-scoped grant must stay discovery-only."""
    from core import path_authorization as auth
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    scoped = "local-model-discover:omnivoice"
    with pytest.raises(auth.PathAuthorizationError):
        auth.consume(cap(tmp_path, scope=scoped)["authorization"], "models_dir")
    response = client.put("/api/settings/storage/models-dir", json=cap(tmp_path, scope=scoped))
    assert response.status_code == 403
    response = client.put(URL, json=cap(model, scope=scoped))
    assert response.status_code == 403
    # An unscoped writable-picker grant cannot select a model or run discovery either.
    response = client.put(URL, json=cap(model))
    assert response.status_code == 403
    response = client.post(URL + "/discover", json=cap(tmp_path))
    assert response.status_code == 403
    # Discovery is bound to its engine.
    response = client.post(URL.replace("omnivoice", "mlx-audio") + "/discover", json=cap(tmp_path, scope=scoped))
    assert response.status_code == 403
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
    # Any outbound connection is a download. Loopback stays open: on Windows
    # asyncio builds each event loop's self-pipe with socket.socketpair(), a
    # real 127.0.0.1 TCP connect (#3865 Windows CI).
    real_connect = socket.socket.connect
    outbound: list[object] = []

    def no_outbound(sock, address, *args, **kwargs):
        host = address[0] if isinstance(address, tuple) else address
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(sock, address, *args, **kwargs)
        outbound.append(address)
        raise AssertionError("discovery must not download")

    monkeypatch.setattr(socket.socket, "connect", no_outbound)
    token = cap(parent, scope="local-model-discover:omnivoice")
    response = client.post(URL + "/discover", json=token)
    assert response.status_code == 200
    assert response.json()["candidates"][0]["validation"]["status"] == "valid"
    response = client.post(URL + "/discover", json=token)
    assert response.status_code == 403
    response = client.post(URL + "/discover", json={"path": str(parent)})
    assert response.status_code == 422
    remote = TestClient(app, client=("203.0.113.9", 9999))
    response = remote.post(URL + "/discover", json=cap(parent, scope="local-model-discover:omnivoice"))
    assert response.status_code == 403
    forbidden.assert_not_called()
    assert outbound == []
    mm.unload_shared_model.assert_not_called()
    assert tts._ENGINE_INSTANCES == {}
    assert before == {str(p.relative_to(parent)): p.read_bytes() for p in parent.rglob("*") if p.is_file()}
    assert not (tmp_path / "prefs.json").exists()


def test_missing_persisted_omnivoice_does_not_resolve_default(api, tmp_path):
    client, cap, mm, tts, app = api
    model = checkpoint(tmp_path / "model")
    response = client.put(URL, json=sel(model))
    assert response.status_code == 200
    (model / "config.json").unlink()
    assert mm.resolve_omnivoice_checkpoint() == str(model)
    assert client.get(URL).json()["validation"]["status"] != "valid"
    with pytest.raises(paths.LocalModelError):
        paths.validate("tts", "omnivoice", mm.resolve_omnivoice_checkpoint())


@pytest.mark.parametrize("replacement", ["symlink", "directory"])
def test_discovered_grant_rejects_replaced_root(api, tmp_path, replacement):
    client, cap, mm, *_ = api
    model = checkpoint(tmp_path / "model")
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model, scope="local-model-discover:omnivoice")).json()["candidates"] if c["validation"]["status"] == "valid")
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
    token = cap(model, scope="local-model-discover:omnivoice") if discovered else sel(model)
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
    candidate = next(c for c in client.post(URL + "/discover", json=cap(model, scope="local-model-discover:omnivoice")).json()["candidates"] if c["validation"]["status"] == "valid")
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


@pytest.mark.parametrize("override", ["env", "pref"])
def test_existing_absolute_mlx_models_skip_gui_format_validation(api, monkeypatch, tmp_path, override):
    client, cap, mm, tts, app = api
    model_path = str(tmp_path / "kokoro")
    if override == "env":
        monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", model_path)
    backend = tts.MLXAudioBackend()
    backend._model_id = model_path
    validator = Mock(side_effect=paths.LocalModelError("not VoiceDesign"))
    monkeypatch.setattr(paths, "validate", validator)
    monkeypatch.setattr(paths, "get", lambda *_: None)
    monkeypatch.setattr(tts, "_harden_mlx_audio_eos_ids", lambda: None)
    loader = Mock(return_value=object())
    module = types.ModuleType("mlx_audio.tts.utils")
    module.load_model = loader
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.utils", module)
    backend._ensure_loaded()
    loader.assert_called_once_with(model_path)
    validator.assert_not_called()


def test_missing_saved_omnivoice_still_resolves_for_inventory(api, monkeypatch, tmp_path):
    client, cap, mm, tts, app = api
    missing = str(tmp_path / "gone")
    monkeypatch.setattr(paths, "get", lambda *_: missing)
    assert mm.resolve_omnivoice_checkpoint() == missing
    assert client.get(URL).json()["validation"]["status"] != "valid"


def test_local_model_change_runs_off_event_loop(api, monkeypatch):
    client, cap, mm, tts, app = api
    from api.routers import settings
    async def scenario():
        loop_thread = threading.get_ident()
        def change(*_):
            assert threading.get_ident() != loop_thread
            assert mm._model_load_thread_lock._is_owned()
            assert mm._model_lock.locked()
            return {"ok": True}
        monkeypatch.setattr(paths, "change", change)
        assert await settings._change_local_model("tts", "omnivoice", None) == {"ok": True}
        assert not mm._model_lock.locked()
    asyncio.run(scenario())


def test_local_model_change_keeps_loop_responsive_and_lock_until_cancelled_worker_finishes(api, monkeypatch):
    client, cap, mm, tts, app = api
    from api.routers import settings
    started = threading.Event()
    release = threading.Event()
    def change(*_):
        started.set()
        assert release.wait(5), "test worker was not released"
        return {"ok": True}
    monkeypatch.setattr(paths, "change", change)
    async def scenario():
        task = asyncio.create_task(settings._change_local_model("tts", "omnivoice", None))
        try:
            assert await asyncio.wait_for(asyncio.to_thread(started.wait, 2), 3)
            await asyncio.sleep(0)
            assert mm._model_lock.locked()
            task.cancel()
            await asyncio.sleep(0)
            assert not task.done()
            assert mm._model_lock.locked()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not mm._model_lock.locked()
    asyncio.run(scenario())
