"""Opt-in call control must not grant remote access to settings or other admin APIs."""
from dataclasses import replace
from importlib import import_module

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from core.auth import AuthPrincipal, CredentialTransport, PrincipalKind, ADMIN_CAPABILITIES, CONSUME_CAPABILITIES


@pytest.fixture
def paired_calls(monkeypatch):
    router = import_module("api.routers.calls")
    dependencies = import_module("api.dependencies")
    config = router.config
    monkeypatch.setattr(dependencies, "_server_mode", lambda: False)
    state = {"settings": config.CallSettings(), "kind": PrincipalKind.ADMIN_SESSION}
    monkeypatch.setattr(config, "load_call_settings", lambda: state["settings"])
    monkeypatch.setattr(config, "save_call_settings", lambda value: state.update(settings=value))
    monkeypatch.setattr(router.calls, "readiness", lambda: [{"id": "voice", "ok": True, "detail": ""}])
    monkeypatch.setattr(router.calls, "list_records", lambda limit: [])
    app = FastAPI()

    @app.middleware("http")
    async def principal(request, call_next):
        request.state.auth_principal = AuthPrincipal(state["kind"], ADMIN_CAPABILITIES if state["kind"] in {PrincipalKind.API_KEY, PrincipalKind.ADMIN_SESSION} else CONSUME_CAPABILITIES, transport=CredentialTransport.HEADER)
        return await call_next(request)

    app.include_router(router.router)
    return TestClient(app, client=("192.0.2.22", 5000)), state, config


def test_paired_call_control_defaults_off(paired_calls):
    client, _, _ = paired_calls
    assert client.get("/calls/readiness").status_code == 403
    assert client.get("/calls").status_code == 403


@pytest.mark.parametrize("kind", [PrincipalKind.ADMIN_SESSION, PrincipalKind.API_KEY])
def test_operator_opt_in_grants_only_call_control(paired_calls, kind):
    client, state, config = paired_calls
    state.update(settings=replace(config.CallSettings(), allow_remote_control=True), kind=kind)
    assert client.get("/calls/readiness").status_code == 200
    assert client.get("/calls/options").json()["disclosure"]
    assert client.get("/calls").json() == {"calls": []}
    assert client.get("/calls/settings").status_code == 403
    assert client.put("/calls/settings", json={"allow_remote_control": True}).status_code == 403


@pytest.mark.parametrize("kind", [PrincipalKind.PIN, PrincipalKind.TRUSTED_NETWORK, PrincipalKind.ANONYMOUS])
def test_consumption_credentials_cannot_control_calls_even_after_opt_in(paired_calls, kind):
    client, state, config = paired_calls
    state.update(settings=replace(config.CallSettings(), allow_remote_control=True), kind=kind)
    assert client.get("/calls").status_code == 403
    assert client.post("/calls", json={"to": "+15550001111", "brief": "hello", "profile_id": "voice"}).status_code == 403


def test_permission_round_trip_and_missing_field_preserves_choice(monkeypatch):
    router = import_module("api.routers.calls")
    settings_store = import_module("services.settings_store")
    import services
    monkeypatch.setattr(services, "settings_store", settings_store)
    values = {}
    monkeypatch.setattr(settings_store, "get_text", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(settings_store, "set_text", lambda key, value: values.__setitem__(key, value))
    assert router.config.load_call_settings().allow_remote_control is False
    router.put_settings(router._SettingsBody(allow_remote_control=True))
    assert router.config.load_call_settings().allow_remote_control is True
    assert router.put_settings(router._SettingsBody(inbound_brief="Hello"))["allow_remote_control"] is True
