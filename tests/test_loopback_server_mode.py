"""Server-mode origin and admin gate contracts (issue #261).

The gate must stay strict on the desktop build (non-loopback → 403, which is the
PR #81 trust boundary). Docker NAT makes the host operator appear non-loopback,
so bare server mode keeps read-only discovery open while every mutation still
requires the long admin API key.
"""
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

def _dependency(name):
    # Resolve at test execution time: other suites intentionally replace
    # ``api.*`` modules in sys.modules while probing cold-start behavior.
    from api import dependencies

    return getattr(dependencies, name)


def is_loopback(*args, **kwargs):
    return _dependency("is_loopback")(*args, **kwargs)


def is_local_host(*args, **kwargs):
    return _dependency("is_local_host")(*args, **kwargs)


def require_admin(*args, **kwargs):
    # ``require_admin`` is the async FastAPI dependency; the gate logic is the
    # sync ``check_admin`` it delegates to.
    return _dependency("check_admin")(*args, **kwargs)


def require_admin_action(*args, **kwargs):
    return _dependency("require_admin_action")(*args, **kwargs)


def require_desktop(*args, **kwargs):
    return _dependency("require_desktop")(*args, **kwargs)


def require_local(*args, **kwargs):
    return _dependency("require_local")(*args, **kwargs)


def require_loopback(*args, **kwargs):
    return _dependency("require_loopback")(*args, **kwargs)


def _req(host):
    """Minimal stand-in for a Starlette Request — the gate only reads client.host."""
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)


@pytest.fixture(autouse=True)
def _clear_loopback_env(monkeypatch):
    # Start each test from the strict desktop default regardless of ambient env.
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_loopback_always_allowed(host):
    require_loopback(_req(host))  # must not raise


def test_non_loopback_rejected_by_default():
    with pytest.raises(HTTPException) as exc:
        require_loopback(_req("172.17.0.1"))  # Docker bridge gateway
    assert exc.value.status_code == 403
    assert "loopback" in str(exc.value.detail).lower()


def test_missing_client_rejected_by_default():
    with pytest.raises(HTTPException):
        require_loopback(_req(None))


@pytest.mark.parametrize("val", ["1", "true", "TRUE", "yes", "on"])
def test_server_mode_allows_non_loopback(monkeypatch, val):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", val)
    require_loopback(_req("172.17.0.1"))  # must not raise
    require_loopback(_req("127.0.0.1"))   # loopback still fine


@pytest.mark.parametrize("val", ["0", "false", "no", "", "off"])
def test_falsey_server_mode_keeps_gate_strict(monkeypatch, val):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", val)
    with pytest.raises(HTTPException):
        require_loopback(_req("10.0.0.5"))


# Trusted local networks (OMNIVOICE_TRUSTED_NETWORKS) — issue #1170.
# A self-hoster can name CIDRs treated as trusted by the CONSUMPTION gates
# (PIN/API-key/WS), so a LAN or reverse proxy is exempted. Admin gates
# (require_admin) stay true-loopback-only — two-tier privilege model.


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_is_loopback_true_for_loopback_only(host):
    assert is_loopback(host) is True


@pytest.mark.parametrize("host", ["192.168.1.50", "10.0.0.1", "8.8.8.8"])
def test_is_loopback_false_for_non_loopback(monkeypatch, host):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "192.168.1.0/24")
    assert is_loopback(host) is False  # trusted-network ≠ loopback


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_is_local_host_loopback_always(monkeypatch, host):
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)
    assert is_local_host(host) is True


def test_is_local_host_trusts_configured_cidr(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "192.168.1.0/24,10.0.0.0/8")
    assert is_local_host("192.168.1.50") is True
    assert is_local_host("10.5.5.5") is True


def test_is_local_host_rejects_outside_configured_cidr(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "192.168.1.0/24")
    assert is_local_host("8.8.8.8") is False
    assert is_local_host("192.168.2.1") is False  # adjacent subnet


@pytest.mark.parametrize("host", ["192.168.1.5", "example.com"])
def test_is_local_host_untrusted_without_config(monkeypatch, host):
    # No trust configured → no behavior change vs. the desktop default.
    monkeypatch.delenv("OMNIVOICE_TRUSTED_NETWORKS", raising=False)
    assert is_local_host(host) is False


def test_is_local_host_ignores_malformed_cidr(monkeypatch):
    # A garbage entry is skipped, not fatal — the gate must never wedge.
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "not-a-cidr,192.168.1.0/24")
    assert is_local_host("192.168.1.5") is True
    assert is_local_host("8.8.8.8") is False


def test_require_loopback_rejects_trusted_network(monkeypatch):
    # Admin gate stays true-loopback-only: a trusted CIDR exempts consumption
    # (PIN/API-key/WS) but NOT admin routes like /system/set-env (RCE-class).
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "172.16.0.0/12")
    with pytest.raises(HTTPException) as exc:
        require_loopback(_req("172.20.0.9"))
    assert exc.value.status_code == 403


def test_require_loopback_still_rejects_untrusted_non_loopback(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "172.16.0.0/12")
    with pytest.raises(HTTPException) as exc:
        require_loopback(_req("8.8.8.8"))
    assert exc.value.status_code == 403


def test_require_local_allows_trusted_network(monkeypatch):
    # Consumption-tier: a trusted-network client IS exempted (unlike require_loopback).
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "172.16.0.0/12")
    require_local(_req("172.20.0.9"))  # must not raise


def test_require_local_rejects_untrusted_non_loopback(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "172.16.0.0/12")
    with pytest.raises(HTTPException) as exc:
        require_local(_req("8.8.8.8"))
    assert exc.value.status_code == 403


def _req_full(host, *, headers=None, query=None, cookies=None, pin=None, method="GET"):
    """Richer stub carrying the channels the admin-credential check reads:
    headers, query params, cookies, and app.state.network_share.pin."""
    ns = SimpleNamespace(pin=pin) if pin is not None else None
    app = SimpleNamespace(state=SimpleNamespace(network_share=ns))
    return SimpleNamespace(
        client=SimpleNamespace(host=host) if host else None,
        headers=headers or {},
        query_params=query or {},
        cookies=cookies or {},
        app=app,
        method=method,
    )


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_legacy_loopback_guard_fails_closed_for_server_mode_mutations(
    monkeypatch, method
):
    """A stale route guard must not reopen writes in a bare Docker server.

    ``require_admin`` is the explicit dependency for privileged routers, but
    this fallback closes the whole bug class: a future mutation that
    accidentally keeps ``require_loopback`` still requires the long API key.
    """
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        require_loopback(_req_full("172.17.0.1", method=method))

    assert exc.value.status_code == 403


def test_legacy_loopback_guard_allows_authenticated_server_mode_mutation(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    require_loopback(
        _req_full(
            "172.17.0.1",
            method="POST",
            headers={"authorization": "Bearer s3cret"},
        )
    )


def test_server_mode_side_effectful_get_requires_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        require_admin_action(_req_full("172.17.0.1", method="GET"))

    assert exc.value.status_code == 403


def test_server_mode_side_effectful_get_accepts_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    require_admin_action(
        _req_full(
            "172.17.0.1",
            method="GET",
            headers={"authorization": "Bearer s3cret"},
        )
    )


def test_side_effectful_get_rejects_remote_api_key_outside_server_mode(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    with pytest.raises(HTTPException) as exc:
        require_admin_action(
            _req_full(
                "10.0.0.5",
                method="GET",
                headers={"authorization": "Bearer s3cret"},
            )
        )

    assert exc.value.status_code == 403


# Mode-distinct admin-gate detail: the 403 message must state what would
# ACTUALLY satisfy the gate. The bundled UI routes any 403 whose detail
# mentions "admin api key" to the API-key login form (frontend client.ts;
# the literal contract is locked by tests/test_auth_gate_detail_lockstep.py).
# Server mode accepts the key, so naming it is right. Desktop mode rejects
# every non-loopback client regardless of credentials — the checks above only
# run under server mode — so it must keep the plain loopback detail: naming
# the key there invites a login form that can never succeed (a desktop
# LAN-share guest would lose the whole consumption UI to it, #1213).


def test_require_admin_desktop_detail_is_plain_loopback(monkeypatch):
    """Desktop build: no presented key can satisfy the gate."""
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")  # a valid key can't help here

    with pytest.raises(HTTPException) as exc:
        require_admin(
            _req_full("10.0.0.5", headers={"authorization": "Bearer s3cret"})
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "loopback origin required"


def test_require_admin_server_mode_detail_names_the_key(monkeypatch):
    """Server mode with an API key configured: the 403 names the key."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    with pytest.raises(HTTPException) as exc:
        require_admin(_req_full("172.17.0.1"))  # credential configured, none presented

    assert exc.value.status_code == 403
    assert exc.value.detail == "loopback origin or admin API key required"


def test_require_admin_pin_only_server_mode_detail_is_plain_loopback(monkeypatch):
    """Server mode with ONLY a share PIN (Greptile P1, PR #1569): the PIN
    closes read-only bootstrap but no API key exists to present, so naming
    the key would send the browser to a login form that can never succeed.
    Only loopback can use admin here — the plain detail says so, and the
    SPA leaves it a plain error instead of gating the whole UI."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        require_admin(_req_full("172.17.0.1", pin="424242"))  # PIN ≠ admin credential

    assert exc.value.status_code == 403
    assert exc.value.detail == "loopback origin required"


def test_require_admin_action_desktop_detail_is_plain_loopback(monkeypatch):
    """Desktop build, side-effectful GET: plain loopback detail."""
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    with pytest.raises(HTTPException) as exc:
        require_admin_action(
            _req_full(
                "10.0.0.5",
                method="GET",
                headers={"authorization": "Bearer s3cret"},
            )
        )

    assert exc.value.status_code == 403
    assert exc.value.detail == "loopback origin required"


def test_require_admin_action_server_mode_detail_names_the_key(monkeypatch):
    """Server mode + key configured, side-effectful GET: names the key."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    with pytest.raises(HTTPException) as exc:
        require_admin_action(_req_full("172.17.0.1", method="GET"))

    assert exc.value.status_code == 403
    assert exc.value.detail == "loopback origin or admin API key required"


def test_side_effectful_get_rejects_pin_and_trusted_network(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/8")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)

    with pytest.raises(HTTPException) as exc:
        require_admin_action(
            _req_full(
                "10.1.2.3",
                method="GET",
                pin="123456",
                headers={"x-omnivoice-pin": "123456"},
            )
        )

    assert exc.value.status_code == 403


# Server mode + trusted network + credential — issue #1213.
# Regression for the two-tier collapse: with OMNIVOICE_SERVER_MODE=1 the
# loopback origin is unenforceable, so admin can't require true loopback. But a
# configured API key must still gate admin — a trusted-network client that
# presents NO key must NOT reach /system/* or /api/settings/* just because
# is_local_host exempts it from the consumption middleware.


def test_server_mode_trusted_network_no_credential_reaches_admin(monkeypatch):
    # No credential configured → admin stays open in server mode (the #261
    # Docker flow: operator reaches /system/* off the bridge gateway).
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/8")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    require_loopback(_req_full("10.1.2.3"))  # must not raise


def test_server_mode_trusted_network_blocked_when_api_key_set(monkeypatch):
    # THE FIX: API key set to lock the backend + trusted CIDR for consumption.
    # A trusted-network client with NO key must be 403'd on the admin surface —
    # trusted-network membership is a consumption exemption, never admin.
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/8")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        require_loopback(_req_full("10.1.2.3"))
    assert exc.value.status_code == 403
    # ...but consumption stays exempt for that same trusted client.
    require_local(_req_full("10.1.2.3"))  # must not raise


def test_server_mode_admin_allowed_with_api_key_header(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    require_loopback(
        _req_full("172.17.0.1", headers={"authorization": "Bearer s3cret"})
    )  # must not raise


def test_server_mode_admin_allowed_with_api_key_cookie_or_query(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    require_loopback(_req_full("172.17.0.1", cookies={"ov_key": "s3cret"}))
    require_loopback(_req_full("172.17.0.1", query={"api_key": "s3cret"}))


def test_server_mode_admin_rejects_wrong_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        require_loopback(_req_full("172.17.0.1", headers={"authorization": "Bearer nope"}))
    assert exc.value.status_code == 403


def test_server_mode_pin_only_keeps_admin_loopback_only(monkeypatch):
    # CodeRabbit #1213: the 6-digit share PIN is a CONSUMPTION credential and is
    # brute-forceable (10^6, no lockout), so it must NEVER gate the RCE-class
    # admin surface. Once a PIN is configured, bare read-only discovery closes;
    # presenting that PIN still cannot authorize either a read or a write.
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "10.0.0.0/8")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    # No PIN presented → discovery denied.
    with pytest.raises(HTTPException):
        require_loopback(_req_full("10.1.2.3", pin="1234"))
    # Correct PIN presented → STILL denied (the PIN never gates admin).
    with pytest.raises(HTTPException):
        require_loopback(
            _req_full(
                "10.1.2.3",
                pin="1234",
                headers={"x-omnivoice-pin": "1234"},
            )
        )
    # Mutations are denied for the same reason.
    with pytest.raises(HTTPException):
        require_loopback(
            _req_full(
                "10.1.2.3",
                pin="1234",
                method="POST",
                headers={"x-omnivoice-pin": "1234"},
            )
        )
    # Loopback admin still needs no credential (the local operator path)…
    require_loopback(_req_full("127.0.0.1", pin="1234"))
    # …and the trusted client keeps its consumption exemption.
    require_local(_req_full("10.1.2.3"))


def test_server_mode_loopback_admin_never_needs_credential(monkeypatch):
    # The local operator on the Docker host (loopback) reaches admin with no
    # credential even when one is configured — the desktop shell path.
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    require_loopback(_req_full("127.0.0.1"))  # must not raise


# GHAS #506/#440/#441: bare Docker retains read-only discovery for bootstrap.
# RCE/filesystem-capable routers use the method-aware admin gate, while the
# legacy loopback guard independently fails closed on accidental mutations.


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
def test_server_mode_admin_mutation_requires_api_key_when_unconfigured(monkeypatch, method):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_admin(_req_full("172.17.0.1", method=method))
    assert exc.value.status_code == 403


def test_server_mode_admin_read_keeps_bare_docker_bootstrap(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    require_admin(_req_full("172.17.0.1", method="GET"))


def test_server_mode_admin_read_rejects_pin_only_deployment(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    with pytest.raises(HTTPException) as exc:
        require_admin(_req_full("172.17.0.1", method="GET", pin="123456"))
    assert exc.value.status_code == 403


def test_server_mode_admin_mutation_allows_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    require_admin(_req_full(
        "172.17.0.1",
        method="POST",
        headers={"authorization": "Bearer s3cret"},
    ))


def test_whitespace_only_api_key_cannot_authorize_admin_mutation(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "   ")

    for credential in (
        {"query": {"api_key": "   "}},
        {"cookies": {"ov_key": "   "}},
    ):
        with pytest.raises(HTTPException) as exc:
            require_admin(
                _req_full("172.17.0.1", method="POST", **credential)
            )
        assert exc.value.status_code == 403

    monkeypatch.setenv("OMNIVOICE_API_KEY", "  s3cret  ")
    require_admin(
        _req_full("172.17.0.1", method="POST", query={"api_key": " s3cret "})
    )


def test_whitespace_query_does_not_shadow_admin_key_cookie(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")

    request = _req_full(
        "172.17.0.1",
        method="POST",
        query={"api_key": "   "},
        cookies={"ov_key": "s3cret"},
        headers={
            "origin": "http://voice.test",
            "x-voicestudio-csrf": "1",
        },
    )
    request.url = SimpleNamespace(scheme="http", netloc="voice.test")
    require_admin(request)


def test_server_mode_desktop_capability_rejects_remote_api_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        require_desktop(_req_full(
            "172.17.0.1",
            method="POST",
            headers={"authorization": "Bearer s3cret"},
        ))
    assert exc.value.status_code == 403


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_loopback_admin_never_needs_api_key(monkeypatch, method):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    require_admin(_req_full("127.0.0.1", method=method))


def test_is_local_host_unwraps_ipv4_mapped_ipv6(monkeypatch):
    # Dual-stack proxies (Caddy, Node.js) pass ::ffff:192.168.1.5 — should
    # match an IPv4 CIDR after unwrapping the mapped address.
    monkeypatch.setenv("OMNIVOICE_TRUSTED_NETWORKS", "192.168.1.0/24")
    assert is_local_host("::ffff:192.168.1.5") is True
    assert is_local_host("::ffff:8.8.8.8") is False


def test_side_effectful_get_cookie_session_requires_same_origin_csrf(monkeypatch, request):
    from services.admin_sessions import admin_session_store

    admin_session_store.clear()
    request.addfinalizer(admin_session_store.clear)
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    session = admin_session_store.issue("s3cret")

    missing = _req_full(
        "172.17.0.1",
        method="GET",
        cookies={"ov_session": session.token},
    )
    missing.url = SimpleNamespace(scheme="http", netloc="voice.test")
    with pytest.raises(HTTPException) as exc:
        require_admin_action(missing)
    assert exc.value.status_code == 403

    allowed = _req_full(
        "172.17.0.1",
        method="GET",
        cookies={"ov_session": session.token},
        headers={
            "origin": "http://voice.test",
            "x-voicestudio-csrf": "1",
            "sec-fetch-site": "same-origin",
        },
    )
    allowed.url = SimpleNamespace(scheme="http", netloc="voice.test")
    require_admin_action(allowed)
