"""The delegated-caller tier for MCP tool calls (#4128).

The MCP server is mounted on the backend's own app and re-enters it over
``httpx.ASGITransport(app=app, client=("127.0.0.1", 0))``, so every tool call
looks like a trusted local process. A share-PIN LAN guest satisfies
``NetworkAccessMiddleware`` for ``/mcp`` (the PIN is accepted for any
non-public path), so a 6-digit consumption credential used to reach
admin-tier tools: ``clone_voice`` creates persistent profiles and
``generate_speech`` runs unbounded GPU work.

Two halves hold the tier together:

* the MCP server marks a re-entrant request that is NOT on behalf of an admin
  credential (``X-OmniVoice-Delegated: 1``), and
* the admin gate treats that marker as "not loopback" and asks for a real
  credential.

The marker can only ever *reduce* privilege, so forging it by hand gains
nothing. Tests run without importing ``main`` (no torch, no MCP session
manager) and exercise the real gate logic.
"""
import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

CSRF_PROOF = {'origin': 'http://127.0.0.1:3900', 'x-voicestudio-csrf': '1'}


def _dependencies():
    # Resolve at call time: other suites intentionally swap ``api.*`` modules
    # in sys.modules while probing cold-start behaviour (same reason as
    # tests/test_loopback_server_mode.py).
    from api import dependencies

    return dependencies


def _request(host="127.0.0.1", *, headers=None, cookies=None, method="POST"):
    """A real Starlette request, so the credential channels are read verbatim."""
    raw_headers = [
        (str(name).lower().encode("latin-1"), str(value).encode("latin-1"))
        for name, value in (headers or {}).items()
    ]
    if cookies:
        pair = "; ".join(f"{name}={value}" for name, value in cookies.items())
        raw_headers.append((b"cookie", pair.encode("latin-1")))
    app = SimpleNamespace(state=SimpleNamespace(network_share=None))
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/profiles",
            "raw_path": b"/profiles",
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": (host, 4321),
            "server": ("127.0.0.1", 3900),
            "app": app,
            "state": {},
        }
    )


def _admin_marker():
    return {_dependencies().DELEGATED_CALLER_HEADER: "1"}


# ── (a) loopback without the marker: desktop/stdio unchanged ────────────────


def test_loopback_without_the_marker_still_passes_check_admin():
    _dependencies().check_admin(_request("127.0.0.1"))  # must not raise


def test_loopback_without_the_marker_passes_even_with_server_mode_and_a_key(
    monkeypatch,
):
    """A genuine local process keeps its privileged position in server mode."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    _dependencies().check_admin(_request("127.0.0.1"))  # must not raise


# ── (b) loopback WITH the marker and no credential: refused ────────────────


def test_delegated_loopback_without_a_credential_is_refused():
    with pytest.raises(HTTPException) as exc:
        _dependencies().check_admin(_request(headers=_admin_marker()))
    assert exc.value.status_code == 403


def test_delegated_loopback_is_refused_even_with_a_share_pin_present(monkeypatch):
    """The PIN is a consumption credential; it never unlocks admin."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        _dependencies().check_admin(
            _request(headers={**_admin_marker(), "x-omnivoice-pin": "123456"})
        )
    assert exc.value.status_code == 403


def test_delegated_loopback_with_a_wrong_key_is_refused(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        _dependencies().check_admin(
            _request(headers={**_admin_marker(), "authorization": "Bearer nope"})
        )
    assert exc.value.status_code == 403


@pytest.mark.parametrize("marker_value", ["1", "true", "yes", "on"])
def test_the_marker_is_read_case_insensitively(marker_value):
    with pytest.raises(HTTPException):
        _dependencies().check_admin(
            _request(headers={_dependencies().DELEGATED_CALLER_HEADER: marker_value})
        )


def test_an_absent_or_empty_marker_keeps_loopback_privilege():
    _dependencies().check_admin(
        _request(headers={_dependencies().DELEGATED_CALLER_HEADER: ""})
    )  # must not raise


def test_a_forged_marker_cannot_upgrade_a_non_loopback_caller(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    with pytest.raises(HTTPException) as exc:
        _dependencies().check_admin(
            _request("10.0.0.5", headers=_admin_marker())
        )
    assert exc.value.status_code == 403


# ── (c) the same request with a real admin credential passes ───────────────


def test_delegated_loopback_passes_with_the_bearer_admin_key(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    _dependencies().check_admin(
        _request(headers={**_admin_marker(), "authorization": "Bearer s3cret"})
    )  # must not raise


def test_delegated_loopback_passes_with_the_legacy_master_cookie(monkeypatch):
    """``ov_key`` is a real master-key channel, so it satisfies the gate."""
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    _dependencies().check_admin(
        _request(headers={**_admin_marker(), **CSRF_PROOF}, cookies={"ov_key": "s3cret"})
    )  # must not raise


def test_a_forged_credential_header_does_not_make_a_caller_privileged(monkeypatch):
    """A header the backend does not honour must not skip the marker.

    If this returned True, the bridge would send no marker, the backend would
    see its forged loopback peer as the local operator, and a PIN guest could
    reach admin-tier tools by inventing a header.
    """
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    assert _caller_is_privileged({"x-omnivoice-api-key": "s3cret"}) is False


def test_delegated_loopback_passes_with_a_valid_admin_session_cookie(monkeypatch):
    from services.admin_sessions import admin_session_store

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    admin_session_store.clear()
    try:
        issued = admin_session_store.issue("s3cret")
        _dependencies().check_admin(
            _request(headers={**_admin_marker(), **CSRF_PROOF}, cookies={"ov_session": issued.token})
        )  # must not raise
    finally:
        admin_session_store.clear()


def test_a_random_session_cookie_does_not_satisfy_the_delegated_gate(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    with pytest.raises(HTTPException) as exc:
        _dependencies().check_admin(
            _request(headers=_admin_marker(), cookies={"ov_session": "ovs_not-a-token"})
        )
    assert exc.value.status_code == 403


# ── (d) require_admin/require_admin_action inherit the gate ────────────────


def test_require_admin_delegates_to_check_admin(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_SERVER_MODE", raising=False)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(_dependencies().require_admin(_request(headers=_admin_marker())))
    assert exc.value.status_code == 403


# ── MCP side: which callers count as privileged ────────────────────────────


def _caller_is_privileged(headers):
    from mcp_server import mcp_caller_is_privileged

    return mcp_caller_is_privileged(_request(headers=headers))


def test_caller_without_an_http_request_is_privileged_stdio():
    from mcp_server import mcp_caller_is_privileged

    assert mcp_caller_is_privileged(None) is True


def test_caller_with_a_bearer_admin_key_is_privileged(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    assert _caller_is_privileged({"authorization": "Bearer s3cret"}) is True


def test_caller_with_the_legacy_master_cookie_is_privileged(monkeypatch):
    """``ov_key`` is the legacy master-key cookie the backend still honours."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    assert _caller_is_privileged({"cookie": "ov_key=s3cret", **CSRF_PROOF}) is True


def test_caller_with_a_valid_admin_session_cookie_is_privileged(monkeypatch):
    from services.admin_sessions import admin_session_store

    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    admin_session_store.clear()
    try:
        issued = admin_session_store.issue("s3cret")
        assert (
            _caller_is_privileged({"cookie": f"ov_session={issued.token}", **CSRF_PROOF}) is True
        )
    finally:
        admin_session_store.clear()


def test_caller_with_a_share_pin_is_not_privileged(monkeypatch):
    """The bug: a 6-digit PIN must not make re-entrant calls admin-tier."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    assert _caller_is_privileged({"x-omnivoice-pin": "123456"}) is False


def test_caller_with_a_wrong_key_is_not_privileged(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    assert _caller_is_privileged({"authorization": "Bearer nope"}) is False


def test_caller_with_no_credential_at_all_is_not_privileged():
    assert _caller_is_privileged({}) is False


def test_caller_with_no_credential_is_not_privileged_even_without_an_api_key():
    """No key configured means nothing can prove admin — fail closed."""
    assert _caller_is_privileged({"authorization": "Bearer anything"}) is False


def test_the_request_accessor_reads_from_the_server_it_is_given():
    """The accessor must use its argument, not a module global.

    It runs outside ``create_mcp_server``, where the FastMCP instance exists
    only as a local name. Looking it up globally raises, the accessor swallows
    that and returns None, and every HTTP caller is then treated as a local
    stdio client: the delegated marker is never sent and the PIN guest keeps
    the loopback tier. This test fails if the binding regresses.
    """
    from mcp_server import _current_http_request

    sentinel = object()

    class _Server:
        def get_context(self):
            return SimpleNamespace(request_context=SimpleNamespace(request=sentinel))

    assert _current_http_request(_Server()) is sentinel


def test_the_request_accessor_takes_the_server_as_its_only_parameter():
    import inspect

    from mcp_server import _current_http_request

    assert list(inspect.signature(_current_http_request).parameters) == ["server"]


def test_an_unavailable_request_context_is_never_the_local_case():
    from mcp_server import _current_http_request, _delegated_request_headers, mcp_caller_is_privileged

    class _StdioServer:
        def get_context(self):
            raise LookupError("no request context")

    caller = _current_http_request(_StdioServer())
    assert caller is not None
    assert mcp_caller_is_privileged(caller) is False
    assert _delegated_request_headers(caller) == _admin_marker()


# ── MCP side: the re-entrant request carries the marker only when needed ───


def _headers_seen_by_the_backend(monkeypatch, *, privileged):
    """Drive one real tool call and capture the re-entrant request's headers."""
    monkeypatch.setenv("OMNIVOICE_API_KEY", "s3cret")
    from fastapi import FastAPI

    from mcp_server import create_mcp_server

    seen = {}
    app = FastAPI()

    @app.get("/health")
    async def health(request: Request):
        seen.update({name.lower(): value for name, value in request.headers.items()})
        return {"ready": True}

    server = create_mcp_server(app=app)
    request = _request('192.0.2.4', headers=(
        {'Authorization': 'Bearer s3cret'} if privileged else {'X-OmniVoice-Pin': '123456'}
    ))
    monkeypatch.setattr(server, 'get_context', lambda: SimpleNamespace(
        request_context=SimpleNamespace(request=request)
    ))
    asyncio.run(server.call_tool("check_health", {}))
    return seen


def test_reentrant_request_carries_the_marker_for_an_unprivileged_caller(monkeypatch):
    seen = _headers_seen_by_the_backend(monkeypatch, privileged=False)
    assert seen.get(_dependencies().DELEGATED_CALLER_HEADER.lower()) == "1"
    assert "authorization" not in seen


def test_reentrant_request_for_a_privileged_caller_keeps_its_standalone_ability(
    monkeypatch,
):
    """A privileged caller needs no marker, so the backend sees plain loopback."""
    seen = _headers_seen_by_the_backend(monkeypatch, privileged=True)
    assert _dependencies().DELEGATED_CALLER_HEADER.lower() not in seen
