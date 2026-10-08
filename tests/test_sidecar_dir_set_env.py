"""Sidecar engine install folders (OMNIVOICE_*_DIR) name a folder whose
interpreter the backend runs, so /system/set-env accepts a new value only
through a one-shot desktop authorization; clearing stays a plain request and
the in-app installer keeps persisting its own folders server-side.
"""
from __future__ import annotations

import json
import os

import pytest
from fastapi.testclient import TestClient

KEY = "OMNIVOICE_INDEXTTS_DIR"


@pytest.fixture
def client(tmp_path, monkeypatch):
    from core import path_authorization, prefs
    from services.sidecar_install import persistent_env_vars

    assert KEY in persistent_env_vars()
    auth_dir = tmp_path / "authorizations"
    auth_dir.mkdir()
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))
    monkeypatch.delenv(KEY, raising=False)
    from main import app

    try:
        yield TestClient(app, client=("127.0.0.1", 50000)), auth_dir
    finally:
        os.environ.pop(KEY, None)
        prefs.delete(f"env.{KEY}")


def _authorize(auth_dir, path, kind="sidecar_dir"):
    token = "b" * 64
    (auth_dir / f"{token}.json").write_text(
        json.dumps({"token": token, "kind": kind, "path": path}), encoding="utf-8",
    )
    return token


def test_raw_folder_values_are_refused_for_every_sidecar_key(client):
    c, _ = client
    from core import prefs
    from services.sidecar_install import persistent_env_vars

    for key in sorted(persistent_env_vars()):
        response = c.post("/system/set-env", json={"key": key, "value": "/tmp/elsewhere"})
        assert response.status_code == 403, key
        assert os.environ.get(key) != "/tmp/elsewhere"
        assert prefs.get(f"env.{key}") != "/tmp/elsewhere"


def test_authorized_folder_is_saved(client, tmp_path):
    c, auth_dir = client
    from core import prefs

    target = str(tmp_path / "index-tts")
    token = _authorize(auth_dir, target)
    response = c.post("/system/set-env", json={"key": KEY, "authorization": token})
    assert response.status_code == 200, response.text
    assert os.environ[KEY] == target
    assert prefs.get(f"env.{KEY}") == target
    # One-shot: the same token cannot be replayed.
    again = c.post("/system/set-env", json={"key": KEY, "authorization": token})
    assert again.status_code == 403


def test_authorization_for_another_setting_is_refused(client, tmp_path):
    c, auth_dir = client
    token = _authorize(auth_dir, str(tmp_path / "x"), kind="models_dir")
    response = c.post("/system/set-env", json={"key": KEY, "authorization": token})
    assert response.status_code == 403
    assert KEY not in os.environ


def test_clearing_still_works(client):
    c, _ = client
    from core import prefs

    os.environ[KEY] = "/kept/by/installer"
    prefs.set_(f"env.{KEY}", "/kept/by/installer")
    response = c.post("/system/set-env", json={"key": KEY, "value": ""})
    assert response.status_code == 200
    assert KEY not in os.environ
    assert prefs.get(f"env.{KEY}") in (None, "")


def test_other_keys_still_accept_plain_values(client):
    c, _ = client
    try:
        response = c.post(
            "/system/set-env", json={"key": "TRANSLATE_MODEL", "value": "some-model"},
        )
        assert response.status_code == 200
    finally:
        os.environ.pop("TRANSLATE_MODEL", None)
        from core import prefs
        prefs.delete("env.TRANSLATE_MODEL")
