"""#64 — the configurable models-dir settings endpoints (validate + persist +
write the durable env that main.py reads at startup).

Single source of truth: the durable per-user env file (``OMNIVOICE_CACHE_DIR``).
``main.py`` reads it at launch; the GET endpoint reads it back. There is no
second store to diverge from.
"""
from __future__ import annotations

import os
import json

import fastapi
import pytest

from core import user_env
from api.routers import settings as s


@pytest.fixture
def env(tmp_path, monkeypatch):
    # Resolve the durable env file via a process-global override so it survives
    # module re-import: some tests/backend/* tests stub `core.*` in sys.modules,
    # which can give the endpoint's `core.user_env` and this test's a *different*
    # module object — a setattr monkeypatch wouldn't reach the endpoint's copy.
    envfile = str(tmp_path / "env")
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", envfile)
    auth_dir = tmp_path / "authorizations"
    auth_dir.mkdir()
    from core import path_authorization
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))
    return envfile


def _body(path, kind="models_dir"):
    from core import path_authorization
    auth_dir = path_authorization._AUTH_DIR
    token = "a" * 64
    with open(os.path.join(auth_dir, f"{token}.json"), "w", encoding="utf-8") as f:
        json.dump({"token": token, "kind": kind, "path": path}, f)
    return s._ModelsDirBody(authorization=token)


def test_set_persists_and_writes_durable_env(env, tmp_path):
    target = str(tmp_path / "models")
    res = s.set_models_dir(_body(target))
    abs_target = os.path.abspath(target)
    assert res["configured"] == abs_target
    assert res["restart_required"] is True
    # main.py reads this on next launch; GET reads it back — single source:
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR") == abs_target
    assert s.get_models_dir()["configured"] == abs_target
    assert os.path.isdir(target)


def test_rejects_unwritable_dir(env, monkeypatch, tmp_path):
    # OS-neutral: force the mkdir to fail rather than relying on Unix-only
    # /dev/null path semantics (cross-platform parity).
    def boom(*a, **k):
        raise OSError("read-only filesystem")

    monkeypatch.setattr(os, "makedirs", boom)
    with pytest.raises(fastapi.HTTPException) as ei:
        s.set_models_dir(_body(str(tmp_path / "ro")))
    assert ei.value.status_code == 400


def test_rejects_path_with_null_byte(env, tmp_path):
    # An embedded NUL would otherwise blow up os.makedirs with a ValueError
    # (→ 500). Validate up front and return a clean 400 instead.
    with pytest.raises(fastapi.HTTPException) as ei:
        s.set_models_dir(_body(str(tmp_path / "mo\x00dels")))
    assert ei.value.status_code == 400


def test_server_mode_remote_without_api_key_cannot_create_models_dir(env, monkeypatch, tmp_path):
    """GHAS #440/#441: a published bare Docker port is not filesystem auth."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    target = tmp_path / "must-not-exist"
    app = fastapi.FastAPI()
    app.include_router(s.router)
    response = TestClient(app, client=("172.17.0.1", 50000)).put(
        "/api/settings/storage/models-dir",
        json={"path": str(target)},
    )
    assert response.status_code == 403
    assert not target.exists()


def test_server_mode_admin_key_cannot_supply_raw_models_path(env, monkeypatch, tmp_path):
    """An admin key is not a native path authorization."""
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    target = tmp_path / "must-not-exist"
    app = fastapi.FastAPI()
    app.include_router(s.router)
    response = TestClient(app, client=("172.17.0.1", 50000)).put(
        "/api/settings/storage/models-dir",
        headers={"authorization": "Bearer s3cret"},
        json={"path": str(target)},
    )
    assert response.status_code == 422
    assert not target.exists()


def test_loopback_raw_path_is_not_a_models_directory_authorization(env, tmp_path):
    from fastapi.testclient import TestClient

    target = tmp_path / "must-not-exist"
    app = fastapi.FastAPI()
    app.include_router(s.router)
    response = TestClient(app, client=("127.0.0.1", 50000)).put(
        "/api/settings/storage/models-dir", json={"path": str(target)}
    )
    assert response.status_code == 422
    assert not target.exists()


def test_models_directory_authorization_is_one_shot(env, tmp_path):
    body = _body(str(tmp_path / "models"))
    assert s.set_models_dir(body)["configured"]
    with pytest.raises(fastapi.HTTPException) as exc:
        s.set_models_dir(body)
    assert exc.value.status_code == 403


def test_clear_reverts_to_default(env):
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/old")
    res = s.set_models_dir(_body(""))
    assert res["configured"] is None
    assert res["restart_required"] is True
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR") is None
    assert s.get_models_dir()["configured"] is None


def test_get_shape(env):
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/configured")
    res = s.get_models_dir()
    assert res["configured"] == "/configured"
    assert "effective" in res and "default" in res


def test_path_with_spaces_survives_the_full_persistence_chain(env, tmp_path, monkeypatch):
    """#1186 class: the wizard/Settings dirs regularly contain spaces
    ('D:\\Program Data\\OmniVoice\\Model Cache'). The durable env file stores
    the value as a dotenv-compatible line and main.py re-reads it through
    python-dotenv, so a writer/parser quoting regression would truncate at the
    first space and silently redirect every model download while Settings
    still shows the chosen folder. Pin the whole chain byte-for-byte:
    endpoint → env file → load_into_environ → os.environ → GET."""
    target = str(tmp_path / "Program Data" / "OmniVoice" / "Model Cache")
    res = s.set_models_dir(_body(target))
    abs_target = os.path.abspath(target)
    assert res["configured"] == abs_target
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR") == abs_target

    # A launcher-injected (stale) value must lose to the durable file (#480),
    # and the loaded value must be byte-identical — spaces intact.
    monkeypatch.setenv("OMNIVOICE_CACHE_DIR", "/stale/launcher/value")
    assert user_env.load_into_environ() is True
    assert os.environ["OMNIVOICE_CACHE_DIR"] == abs_target
    assert s.get_models_dir()["configured"] == abs_target


@pytest.mark.parametrize("folder", ["Books #1", "Model's #1", r"Models\fonts #1"])
def test_models_directory_comment_characters_survive_route_and_startup(env, tmp_path, monkeypatch, folder):
    from fastapi.testclient import TestClient
    from api.routers.settings import router
    from core import user_env

    target = str(tmp_path / folder)
    body = _body(target)
    app = fastapi.FastAPI()
    app.include_router(router)
    with TestClient(app, client=("127.0.0.1", 50000)) as client:
        response = client.put("/api/settings/storage/models-dir", json={"authorization": body.authorization})
        assert response.status_code == 200
        assert response.json()["configured"] == target
        assert client.get("/api/settings/storage/models-dir").json()["configured"] == target
        monkeypatch.setenv("OMNIVOICE_CACHE_DIR", "/stale/launcher/value")
        assert user_env.load_into_environ() is True
        assert os.environ["OMNIVOICE_CACHE_DIR"] == target
        assert client.get("/api/settings/storage/models-dir").json()["configured"] == target
    assert not os.path.exists(target.split(" #", 1)[0])


def test_windows_drive_paths_with_spaces_round_trip_verbatim(env):
    """#1186 class, non-system-drive half: 'D:\\…' paths with spaces (and '&')
    must survive persist → read-back → the exact dotenv parse that
    load_into_environ feeds os.environ from, with backslashes, the drive
    prefix, and interior spaces untouched. (Path *usability* dropping is
    platform-dependent and covered elsewhere; this pins the string layer that
    is identical on every OS.)"""
    import dotenv

    cache = r"D:\Program Data\OmniVoice\Model Cache"
    data = r"D:\Program Data\OmniVoice\Voice data & projects"
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", cache)
    user_env.set_user_env("OMNIVOICE_DATA_DIR", data)
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR") == cache
    assert user_env.get_user_env("OMNIVOICE_DATA_DIR") == data
    parsed = dotenv.dotenv_values(env)
    assert parsed["OMNIVOICE_CACHE_DIR"] == cache
    assert parsed["OMNIVOICE_DATA_DIR"] == data


def test_default_is_xdg_aware(env, monkeypatch, tmp_path):
    # huggingface_hub's default cache root honors XDG_CACHE_HOME on Linux.
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "xdg"))
    for var in ("HF_HUB_CACHE", "HUGGINGFACE_HUB_CACHE", "HF_HOME"):
        monkeypatch.delenv(var, raising=False)
    default = s.get_models_dir()["default"]
    assert default == str(tmp_path / "xdg" / "huggingface")
