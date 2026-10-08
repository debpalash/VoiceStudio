"""Exact-origin policy for ambient browser administrator credentials."""

from __future__ import annotations

import importlib
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from core.csrf import cookie_csrf_allowed, origin_allowed


@pytest.fixture(autouse=True)
def _resolve_active_csrf_module():
    """Bind the active app module after any sys.modules test isolation."""
    module = importlib.import_module("core.csrf")
    globals()["cookie_csrf_allowed"] = module.cookie_csrf_allowed
    globals()["origin_allowed"] = module.origin_allowed


@pytest.fixture(autouse=True)
def _clean_origin_environment(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    monkeypatch.delenv("VOICESTUDIO_UI_PORT", raising=False)


def _connection(
    *,
    origin: str | None = "https://voice.test",
    destination: str = "https://voice.test",
    method: str = "POST",
    marker: str | None = "1",
    fetch_site: str | None = None,
):
    target = destination.split("://", 1)
    headers: dict[str, str] = {}
    if origin is not None:
        headers["origin"] = origin
    if marker is not None:
        headers["x-voicestudio-csrf"] = marker
    if fetch_site is not None:
        headers["sec-fetch-site"] = fetch_site
    return SimpleNamespace(
        headers=headers,
        method=method,
        url=SimpleNamespace(scheme=target[0], netloc=target[1]),
        scope={"type": "http", "scheme": target[0], "method": method},
    )


@pytest.mark.parametrize(
    "origin",
    [
        None,
        "",
        "null",
        "*",
        "ftp://voice.test",
        "https://evil.test",
        "http://voice.test",
        "https://voice.test:444",
        "https://sub.voice.test",
        "https://voice.test.evil.test",
        "https://user@voice.test",
        "https://voice.test/path",
        "https://voice.test?query=1",
        "https://voice.test#fragment",
        "https://voice.test, https://voice.test",
        "https://voice.test:not-a-port",
        "not an origin",
    ],
)
def test_destination_origin_rejects_missing_malformed_and_lookalike_values(origin):
    assert origin_allowed(_connection(origin=origin)) is False


@pytest.mark.parametrize(
    ("origin", "destination"),
    [
        ("https://voice.test", "https://voice.test"),
        ("HTTPS://VOICE.TEST", "https://voice.test:443"),
        ("http://voice.test", "http://voice.test:80"),
        ("https://voice.test:7443", "https://voice.test:7443"),
    ],
)
def test_destination_origin_uses_normalized_exact_tuple(origin, destination):
    assert origin_allowed(_connection(origin=origin, destination=destination)) is True


def test_explicit_allowed_origin_is_exact_and_port_bound(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ALLOWED_ORIGINS", "https://ui.test:7443")

    assert origin_allowed(_connection(origin="https://ui.test:7443")) is True
    assert origin_allowed(_connection(origin="https://ui.test")) is False
    assert origin_allowed(_connection(origin="https://ui.test.evil")) is False


def test_default_desktop_origin_is_allowed_and_retired_tauri_origins_are_not():
    assert origin_allowed(_connection(origin="app://voicestudio")) is True
    # Final Tauri installs talk to the backend they bundle, never this one.
    assert origin_allowed(_connection(origin="tauri://localhost")) is False
    assert origin_allowed(_connection(origin="http://tauri.localhost")) is False


@pytest.mark.parametrize("name", ["OMNIVOICE_UI_PORT", "VOICESTUDIO_UI_PORT"])
def test_ui_port_accepts_the_canonical_name_and_its_alias(monkeypatch, name):
    monkeypatch.delenv("OMNIVOICE_UI_PORT", raising=False)
    monkeypatch.delenv("VOICESTUDIO_UI_PORT", raising=False)
    monkeypatch.delenv("OMNIVOICE_ALLOWED_ORIGINS", raising=False)
    monkeypatch.setenv(name, "4100")

    assert origin_allowed(_connection(origin="http://localhost:4100")) is True
    assert origin_allowed(_connection(origin="http://localhost:3901")) is False


def test_canonical_ui_port_wins_over_the_alias(monkeypatch):
    ui_port = importlib.import_module("core.csrf").ui_port

    monkeypatch.setenv("OMNIVOICE_UI_PORT", "4100")
    monkeypatch.setenv("VOICESTUDIO_UI_PORT", "4200")
    assert ui_port() == 4100


def test_invalid_ui_port_falls_back_to_the_default_allowlist(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_UI_PORT", "not-a-port")
    monkeypatch.delenv("OMNIVOICE_ALLOWED_ORIGINS", raising=False)

    assert origin_allowed(_connection(origin="http://localhost:3901")) is True


def test_destination_origin_falls_back_to_asgi_scope_and_host_header():
    connection = _connection(origin="https://voice.test")
    del connection.url
    connection.headers["host"] = "voice.test"
    connection.scope["scheme"] = "https"

    assert origin_allowed(connection) is True


def test_forwarded_proto_upgrades_destination_scheme_behind_tls_proxy():
    # Tailscale Serve (docs/remote-gpu.md) and any TLS-terminating proxy:
    # the browser presents an https Origin while the backend hop is http.
    connection = _connection(origin="https://gpu.test", destination="http://gpu.test")
    connection.headers["x-forwarded-proto"] = "https"

    assert origin_allowed(connection) is True


def test_forwarded_proto_uses_first_value_of_comma_separated_chain():
    connection = _connection(origin="https://gpu.test", destination="http://gpu.test")
    connection.headers["x-forwarded-proto"] = "https, http"

    assert origin_allowed(connection) is True


def test_forwarded_proto_upgrades_scope_fallback_path_too():
    connection = _connection(origin="https://gpu.test", destination="http://gpu.test")
    del connection.url
    connection.headers["host"] = "gpu.test"
    connection.headers["x-forwarded-proto"] = "https"

    assert origin_allowed(connection) is True


def test_spoofed_forwarded_proto_does_not_admit_cross_origin():
    connection = _connection(origin="https://evil.test", destination="http://voice.test")
    connection.headers["x-forwarded-proto"] = "https"

    assert origin_allowed(connection) is False


def test_forwarded_proto_never_downgrades_a_genuine_tls_destination():
    # A forged "http" on a real https hop must not make an http Origin match.
    connection = _connection(origin="http://voice.test", destination="https://voice.test")
    connection.headers["x-forwarded-proto"] = "http"

    assert origin_allowed(connection) is False


@pytest.mark.parametrize("junk", ["ftp", "", "  ", "HTTPS://x", "null"])
def test_unrecognized_forwarded_proto_values_are_ignored(junk):
    accepted = _connection(origin="http://voice.test", destination="http://voice.test")
    accepted.headers["x-forwarded-proto"] = junk
    rejected = _connection(origin="https://voice.test", destination="http://voice.test")
    rejected.headers["x-forwarded-proto"] = junk

    assert origin_allowed(accepted) is True
    assert origin_allowed(rejected) is False


@pytest.mark.parametrize(
    ("marker", "origin"),
    [(None, "https://voice.test"), ("", "https://voice.test"), ("0", "https://voice.test"), ("1", None)],
)
def test_cookie_mutation_requires_marker_and_exact_origin(marker, origin):
    assert cookie_csrf_allowed(_connection(marker=marker, origin=origin)) is False


def test_cookie_mutation_accepts_exact_origin_and_marker():
    assert cookie_csrf_allowed(_connection()) is True


@pytest.mark.parametrize("fetch_site", [None, "", "cross-site", "same-site", "none"])
def test_side_effectful_get_requires_browser_same_origin_signal(fetch_site):
    connection = _connection(method="GET", fetch_site=fetch_site)

    assert cookie_csrf_allowed(connection, side_effectful_get=True) is False


def test_side_effectful_get_accepts_all_three_browser_proofs():
    connection = _connection(method="GET", fetch_site="same-origin")

    assert cookie_csrf_allowed(connection, side_effectful_get=True) is True

def test_electron_origin_is_exact_and_respects_explicit_allowlists(monkeypatch):
    assert origin_allowed(_connection(origin="app://voicestudio")) is True
    for origin in ("app://evil", "app://voicestudio.evil", "app://voicestudio:3900", "app://user@voicestudio", "app://voicestudio/path"):
        assert origin_allowed(_connection(origin=origin)) is False
    monkeypatch.setenv("OMNIVOICE_ALLOWED_ORIGINS", "https://ui.test")
    assert origin_allowed(_connection(origin="app://voicestudio")) is False
