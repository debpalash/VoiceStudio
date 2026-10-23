# tests/test_tailscale_service.py
import json
from unittest.mock import patch, MagicMock
from services import tailscale as ts


def test_status_absent_cli_is_graceful():
    with patch("services.tailscale.shutil.which", return_value=None):
        s = ts.status()
    assert s["installed"] is False and s["running"] is False


def test_status_parses_json():
    payload = {"BackendState": "Running", "Self": {"DNSName": "box.tail1234.ts.net.", "TailscaleIPs": ["100.64.0.1"]}}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(payload))):
        s = ts.status()
    assert s["installed"] and s["running"]
    assert s["magic_dns_name"] == "box.tail1234.ts.net"
    assert s["tailnet_ips"] == ["100.64.0.1"]


_RUNNING = {"BackendState": "Running", "Self": {"DNSName": "box.ts.net.", "TailscaleIPs": ["100.64.0.1"]}}


def _runner(status_payload, https_ok=True, http_ok=True, serve_config=None, calls=None):
    """subprocess.run side_effect: serves the status JSON for `status --json`,
    the serve config for `serve status --json`, and ok/fail for the serve
    subcommands based on the --http/--https flag. Records every call."""
    def run(args, **kw):
        if calls is not None:
            calls.append(list(args))
        if args[1:] == ["serve", "status", "--json"]:
            return MagicMock(returncode=0, stdout=json.dumps(serve_config or {}))
        if args[1:] == ["status", "--json"]:
            return MagicMock(returncode=0, stdout=json.dumps(status_payload))
        if any(a.startswith("--https=") for a in args):
            return MagicMock(returncode=0 if https_ok else 1, stdout="",
                             stderr="" if https_ok else "error enabling https feature: error 404 Not Found")
        if any(a.startswith("--http=") for a in args):
            return MagicMock(returncode=0 if http_ok else 1, stdout="",
                             stderr="" if http_ok else "serve failed")
        return MagicMock(returncode=0, stdout="", stderr="")
    return run


def _serve_config(*shares):
    """A `tailscale serve status --json` payload: shares are (port, scheme, {path: proxy})."""
    tcp, web = {}, {}
    for port, scheme, handlers in shares:
        tcp[str(port)] = {scheme.upper(): True}
        web[f"box.ts.net:{port}"] = {"Handlers": {p: {"Proxy": t} for p, t in handlers.items()}}
    return {"TCP": tcp, "Web": web}


def _serve_commands(calls):
    return [c[1:] for c in calls if c[1] == "serve" and c[2:] != ["status", "--json"]]


def test_status_includes_cert_domains():
    payload = {**_RUNNING, "CertDomains": ["box.ts.net"]}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(payload))):
        s = ts.status()
    assert s["cert_domains"] == ["box.ts.net"]


def test_serve_enable_not_running_is_clear_error():
    payload = {"BackendState": "Stopped", "Self": {}}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", return_value=MagicMock(returncode=0, stdout=json.dumps(payload))):
        r = ts.serve_enable(3900)
    assert r["ok"] is False and "tailscale up" in r["error"]


def test_serve_enable_uses_http_when_no_certs():
    # CertDomains absent (the common case) -> HTTP serve, no failed https attempt.
    payload = {**_RUNNING, "CertDomains": None}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=_runner(payload)):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["scheme"] == "http"
    assert r["url"] == "http://box.ts.net:10000"
    assert "note" in r


def test_serve_enable_uses_https_when_certs_present():
    payload = {**_RUNNING, "CertDomains": ["box.ts.net"]}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=_runner(payload, https_ok=True)):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["scheme"] == "https"
    assert r["url"] == "https://box.ts.net:10000"


def test_serve_enable_falls_back_to_http_when_https_fails():
    payload = {**_RUNNING, "CertDomains": ["box.ts.net"]}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=_runner(payload, https_ok=False, http_ok=True)):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["scheme"] == "http"


def test_serve_enable_proxies_configured_backend_port(monkeypatch):
    """When OMNIVOICE_PORT is set and serve_enable() is called with no explicit
    port, the proxy target must use the configured backend port (not 3900)."""
    monkeypatch.setenv("OMNIVOICE_PORT", "4000")
    payload = {**_RUNNING, "CertDomains": None}
    captured = {}

    def run(args, **kw):
        if args[1:] == ["serve", "status", "--json"]:
            return MagicMock(returncode=0, stdout="{}")
        if "status" in args and "--json" in args:
            return MagicMock(returncode=0, stdout=json.dumps(payload))
        if any(a.startswith("--http=") for a in args):
            captured["target"] = args[-1]
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_enable()  # no explicit port → defaults to backend_port()
    assert r["ok"]
    assert captured["target"] == "http://127.0.0.1:4000"


# ── Own port only: never take over or wipe another app's share ───────────────

_OTHER_APP_ROOT = (443, "https", {"/": "http://127.0.0.1:5202", "/api": "http://127.0.0.1:8019"})
_OTHER_APP_8443 = (8443, "https", {"/": "http://127.0.0.1:5273"})


def _enable(payload, calls, serve_config=None, **kw):
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run",
               side_effect=_runner(payload, serve_config=serve_config, calls=calls, **kw)):
        return ts.serve_enable(3900)


def _disable(serve_config, calls, **kw):
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run",
               side_effect=_runner(_RUNNING, serve_config=serve_config, calls=calls, **kw)):
        return ts.serve_disable(3900)


def test_serve_enable_https_uses_dedicated_port_not_root(monkeypatch):
    monkeypatch.delenv(ts.HTTPS_PORT_ENV, raising=False)
    calls = []
    r = _enable({**_RUNNING, "CertDomains": ["box.ts.net"]}, calls,
                serve_config=_serve_config(_OTHER_APP_ROOT))
    assert r["ok"] and r["scheme"] == "https"
    assert r["url"] == "https://box.ts.net:10000"
    assert _serve_commands(calls) == [["serve", "--bg", "--https=10000", "http://127.0.0.1:3900"]]


def test_serve_enable_http_uses_dedicated_port_not_80(monkeypatch):
    monkeypatch.delenv(ts.HTTP_PORT_ENV, raising=False)
    calls = []
    r = _enable({**_RUNNING, "CertDomains": None}, calls)
    assert r["ok"] and r["url"] == "http://box.ts.net:10000"
    assert _serve_commands(calls) == [["serve", "--bg", "--http=10000", "http://127.0.0.1:3900"]]


def test_serve_ports_are_configurable(monkeypatch):
    monkeypatch.setenv(ts.HTTPS_PORT_ENV, "8444")
    monkeypatch.setenv(ts.HTTP_PORT_ENV, "8081")
    calls = []
    r = _enable({**_RUNNING, "CertDomains": ["box.ts.net"]}, calls, https_ok=False)
    assert r["ok"] and r["url"] == "http://box.ts.net:8081"
    assert [c[2] for c in _serve_commands(calls)] == ["--https=8444", "--http=8081"]


def test_configured_root_port_gives_bare_url(monkeypatch):
    monkeypatch.setenv(ts.HTTPS_PORT_ENV, "443")
    r = _enable({**_RUNNING, "CertDomains": ["box.ts.net"]}, [])
    assert r["url"] == "https://box.ts.net"


def test_bad_port_env_falls_back_to_default(monkeypatch):
    for bad in ("abc", "0", "70000"):
        monkeypatch.setenv(ts.HTTPS_PORT_ENV, bad)
        assert ts.https_port() == ts.DEFAULT_SERVE_PORT


def test_serve_enable_refuses_port_owned_by_another_app(monkeypatch):
    monkeypatch.setenv(ts.HTTPS_PORT_ENV, "443")
    calls = []
    r = _enable({**_RUNNING, "CertDomains": ["box.ts.net"]}, calls,
                serve_config=_serve_config(_OTHER_APP_ROOT))
    assert r["ok"] is False
    assert ts.HTTPS_PORT_ENV in r["error"]
    assert _serve_commands(calls) == []  # nothing was overwritten


def test_serve_enable_again_on_own_port_is_fine():
    calls = []
    r = _enable({**_RUNNING, "CertDomains": ["box.ts.net"]}, calls,
                serve_config=_serve_config((10000, "https", {"/": "http://127.0.0.1:3900"})))
    assert r["ok"]


def test_serve_disable_turns_off_only_own_port_never_reset():
    calls = []
    config = _serve_config(
        _OTHER_APP_ROOT, _OTHER_APP_8443, (10000, "https", {"/": "http://127.0.0.1:3900"})
    )
    r = _disable(config, calls)
    assert r == {"ok": True}
    assert _serve_commands(calls) == [["serve", "--https=10000", "off"]]
    assert not any("reset" in c for c in calls)


def test_serve_disable_cleans_up_legacy_root_share_of_this_backend():
    """Older versions served on 443/80; Disable still removes that share."""
    calls = []
    r = _disable(_serve_config((80, "http", {"/": "http://127.0.0.1:3900"})), calls)
    assert r == {"ok": True}
    assert _serve_commands(calls) == [["serve", "--http=80", "off"]]


def test_serve_disable_with_nothing_shared_is_a_noop():
    calls = []
    r = _disable(_serve_config(_OTHER_APP_ROOT), calls)
    assert r == {"ok": True}
    assert _serve_commands(calls) == []


def test_serve_disable_leaves_mixed_port_alone():
    """A port that also serves something else is not ours to switch off."""
    calls = []
    mixed = (10000, "https", {"/": "http://127.0.0.1:3900", "/other": "http://127.0.0.1:9000"})
    r = _disable(_serve_config(mixed), calls)
    assert r == {"ok": True}
    assert _serve_commands(calls) == []


def test_serve_disable_fails_closed_when_config_unreadable():
    calls = []

    def run(args, **kw):
        calls.append(list(args))
        return MagicMock(returncode=1, stdout="", stderr="boom")

    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_disable(3900)
    assert r["ok"] is False
    assert calls == [["/usr/bin/tailscale", "serve", "status", "--json"]]  # no reset, no off
