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


def _runner(status_payload, https_ok=True, http_ok=True, serve_config=None):
    def run(args, **kw):
        if args[1:3] == ["serve", "status"]:
            return MagicMock(returncode=0, stdout=json.dumps(serve_config or {}), stderr="")
        if "status" in args and "--json" in args:
            return MagicMock(returncode=0, stdout=json.dumps(status_payload), stderr="")
        if "--https=443" in args:
            return MagicMock(
                returncode=0 if https_ok else 1,
                stdout="",
                stderr="" if https_ok else "error enabling https feature: 404",
            )
        if "--http=80" in args:
            return MagicMock(
                returncode=0 if http_ok else 1,
                stdout="",
                stderr="" if http_ok else "serve failed",
            )
        return MagicMock(returncode=0, stdout="", stderr="")

    return run


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
    assert r["url"] == "http://box.ts.net"
    assert "note" in r


def test_serve_enable_uses_https_when_certs_present():
    payload = {**_RUNNING, "CertDomains": ["box.ts.net"]}
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=_runner(payload, https_ok=True)):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["scheme"] == "https"
    assert r["url"] == "https://box.ts.net"


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
        if "status" in args and "--json" in args:
            return MagicMock(returncode=0, stdout=json.dumps(payload))
        if "--http=80" in args:
            captured["target"] = args[-1]
            return MagicMock(returncode=0, stdout="", stderr="")
        return MagicMock(returncode=0, stdout="", stderr="")

    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_enable()  # no explicit port → defaults to backend_port()
    assert r["ok"]
    assert captured["target"] == "http://127.0.0.1:4000"


# ── Ownership: never touch other apps' serve config (#3068) ─────────────────

def _ours(port=3900):
    return {"Proxy": f"http://127.0.0.1:{port}"}


def _other():
    return {"Proxy": "http://127.0.0.1:5173"}


def _config(**ports):
    """Build a `serve status --json` payload: port=(scheme, {mount: handler})."""
    tcp, web = {}, {}
    for key, (scheme, handlers) in ports.items():
        port = key.lstrip("p")
        tcp[port] = {"HTTPS": True} if scheme == "https" else {"HTTP": True}
        web[f"box.ts.net:{port}"] = {"Handlers": handlers}
    return {"TCP": tcp, "Web": web}


def _recording_runner(serve_config, status_payload=None):
    calls = []

    def run(args, **kw):
        calls.append(list(args))
        if args[1:3] == ["serve", "status"]:
            if serve_config is None:
                return MagicMock(returncode=1, stdout="", stderr="not running")
            return MagicMock(returncode=0, stdout=json.dumps(serve_config))
        if "status" in args:
            return MagicMock(returncode=0, stdout=json.dumps(status_payload or _RUNNING))
        return MagicMock(returncode=0, stdout="", stderr="")

    return run, calls


def _serve_calls(calls):
    return [c[2:] for c in calls if c[1] == "serve" and c[2] != "status"]


def test_disable_never_resets_and_removes_only_our_handler():
    config = _config(p443=("https", {"/": _other()}), p80=("http", {"/": _ours()}))
    run, calls = _recording_runner(config)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_disable(3900)
    assert r == {"ok": True}
    assert _serve_calls(calls) == [["--http=80", "off"]]
    assert not any("reset" in c for c in calls)


def test_disable_removes_only_our_mount_on_a_shared_port():
    config = _config(p443=("https", {"/": _other(), "/voice": _ours()}))
    run, calls = _recording_runner(config)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        assert ts.serve_disable(3900)["ok"]
    assert _serve_calls(calls) == [["--https=443", "--set-path=/voice", "off"]]


def test_disable_with_nothing_of_ours_changes_nothing():
    config = _config(p443=("https", {"/": _other()}), p8443=("https", {"/": _ours(4000)}))
    run, calls = _recording_runner(config)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        assert ts.serve_disable(3900)["ok"]
    assert _serve_calls(calls) == []


def test_disable_fails_closed_when_config_unreadable():
    run, calls = _recording_runner(None)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_disable(3900)
    assert r["ok"] is False
    assert _serve_calls(calls) == []


def test_enable_keeps_default_url_when_port_is_free_or_ours():
    for config in ({}, _config(p80=("http", {"/": _ours()}))):
        run, calls = _recording_runner(config)
        with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
             patch("services.tailscale.subprocess.run", side_effect=run):
            r = ts.serve_enable(3900)
        assert r["ok"] and r["url"] == "http://box.ts.net"
        assert _serve_calls(calls) == [["--bg", "--http=80", "http://127.0.0.1:3900"]]


def test_enable_moves_to_dedicated_port_instead_of_overwriting_another_app():
    payload = {**_RUNNING, "CertDomains": ["box.ts.net"]}
    config = _config(p443=("https", {"/": _other()}))
    run, calls = _recording_runner(config, payload)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["url"] == "https://box.ts.net:10000"
    assert _serve_calls(calls) == [["--bg", "--https=10000", "http://127.0.0.1:3900"]]


def test_enable_refuses_when_every_candidate_port_is_taken():
    config = _config(p80=("http", {"/": _other()}), p10000=("http", {"/": _other()}))
    run, calls = _recording_runner(config)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_enable(3900)
    assert r["ok"] is False
    assert _serve_calls(calls) == []


def test_enable_treats_tcp_forward_as_another_app():
    config = {"TCP": {"80": {"TCPForward": "127.0.0.1:22"}}}
    run, calls = _recording_runner(config)
    with patch("services.tailscale.shutil.which", return_value="/usr/bin/tailscale"), \
         patch("services.tailscale.subprocess.run", side_effect=run):
        r = ts.serve_enable(3900)
    assert r["ok"] and r["url"] == "http://box.ts.net:10000"


def test_serve_config_tolerates_version_skew_warning():
    out = 'Warning: client version "1" != tailscaled server version "2"\n' + json.dumps(_config(p80=("http", {"/": _ours()})))
    with patch("services.tailscale.subprocess.run", return_value=MagicMock(returncode=0, stdout=out)):
        assert "80" in ts._serve_config("/usr/bin/tailscale")["TCP"]
