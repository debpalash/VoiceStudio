# tests/test_tailscale_service.py
import json
import pytest
from unittest.mock import patch, MagicMock
from services import tailscale as ts


def test_status_absent_cli_is_graceful():
    with patch("services.tailscale.shutil.which", return_value=None), \
         patch.object(ts, "_MACOS_APP_CLI", "/nonexistent/Tailscale"):
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


def _runner(status_payload, https_ok=True, http_ok=True):
    """subprocess.run side_effect: serves the status JSON for `status --json`
    and ok/fail for the serve subcommands based on the --http/--https flag."""
    def run(args, **kw):
        if "status" in args and "--json" in args:
            return MagicMock(returncode=0, stdout=json.dumps(status_payload))
        if "--https=443" in args:
            return MagicMock(returncode=0 if https_ok else 1, stdout="",
                             stderr="" if https_ok else "error enabling https feature: error 404 Not Found")
        if "--http=80" in args:
            return MagicMock(returncode=0 if http_ok else 1, stdout="",
                             stderr="" if http_ok else "serve failed")
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


# Patched on the module object the tests call (`ts`), not by import path:
# another suite can purge services.* from sys.modules, and a dotted-path patch
# would then land on a fresh copy that `ts.status()` never reads.
_BUNDLED = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _only_bundle_is_executable(path, mode):
    return path == _BUNDLED


def test_macos_app_bundle_cli_is_used_when_not_on_path():
    # The Mac App Store / standalone app keeps its CLI inside the bundle.
    payload = {**_RUNNING}
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=json.dumps(payload)))
    with patch("services.tailscale.shutil.which", return_value=None), \
         patch.object(ts.sys, "platform", "darwin"), \
         patch.object(ts, "_MACOS_APP_CLI", _BUNDLED), \
         patch.object(ts.os, "access", _only_bundle_is_executable), \
         patch.object(ts.subprocess, "run", run):
        s = ts.status()
    assert s["installed"] and s["running"]
    assert run.call_args.args[0][0] == _BUNDLED


def test_macos_app_bundle_is_ignored_on_other_platforms():
    with patch("services.tailscale.shutil.which", return_value=None), \
         patch.object(ts.sys, "platform", "linux"), \
         patch.object(ts, "_MACOS_APP_CLI", _BUNDLED), \
         patch.object(ts.os, "access", _only_bundle_is_executable):
        assert ts.status()["installed"] is False


def test_bundled_commands_force_cli_without_mutating_parent_environment(monkeypatch):
    monkeypatch.setenv("TAILSCALE_BE_CLI", "0")
    monkeypatch.setenv("TAILSCALE_TEST_PRESERVE", "kept")
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=json.dumps(_RUNNING)))
    with patch.object(ts, "_cli", return_value=_BUNDLED), \
         patch.object(ts.sys, "platform", "darwin"), \
         patch.object(ts.subprocess, "run", run):
        assert ts.status()["running"]
        assert ts.serve_enable(3900)["ok"]
        assert ts.serve_disable()["ok"]
    assert len(run.call_args_list) == 4
    for call in run.call_args_list:
        assert call.kwargs.get("env", {}).get("TAILSCALE_BE_CLI") == "1"
        assert call.kwargs["env"]["TAILSCALE_TEST_PRESERVE"] == "kept"
    assert ts.os.environ["TAILSCALE_BE_CLI"] == "0"


@pytest.mark.parametrize("platform", ["darwin", "linux", "win32"])
def test_standalone_cli_keeps_inherited_environment(platform):
    run = MagicMock(return_value=MagicMock(returncode=0, stdout=json.dumps(_RUNNING)))
    with patch.object(ts, "_cli", return_value="/usr/local/bin/tailscale"), \
         patch.object(ts.sys, "platform", platform), \
         patch.object(ts.subprocess, "run", run):
        assert ts.status()["running"]
        assert ts.serve_disable()["ok"]
    assert all(call.kwargs.get("env") is None for call in run.call_args_list)


def test_path_symlink_to_bundle_also_forces_cli(monkeypatch):
    with patch.object(ts.sys, "platform", "darwin"), \
         patch.object(ts.os.path, "realpath", return_value=_BUNDLED):
        assert ts._command_env("/usr/local/bin/tailscale")["TAILSCALE_BE_CLI"] == "1"
