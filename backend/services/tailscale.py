"""Thin wrapper around the `tailscale` CLI. Every call degrades gracefully
when the CLI is missing or not logged in (installed/running flags).

`tailscale serve --https=443` requires the tailnet's HTTPS Certificates
feature (reported as non-empty ``CertDomains`` in `status --json`). Most
tailnets have it off — and forcing `--https` then fails with
"error enabling https feature: 404". So we serve over **HTTP** (`--http=80`)
by default — the WireGuard tunnel already encrypts the transport — and only
use HTTPS when the tailnet actually has certs provisioned.
"""
import json
import logging
import os
import shutil
import subprocess
import sys

from services import network_share

_ADMIN_DNS = "https://login.tailscale.com/admin/dns"
logger = logging.getLogger("omnivoice.tailscale")


# The macOS Tailscale app (Mac App Store and standalone builds) ships its CLI
# inside the bundle and only puts `tailscale` on PATH if the user installs it
# from the app's settings, so a signed-in Mac usually has no `tailscale` on PATH.
_MACOS_APP_CLI = "/Applications/Tailscale.app/Contents/MacOS/Tailscale"


def _cli():
    cli = shutil.which("tailscale")
    if cli:
        return cli
    if sys.platform == "darwin" and os.access(_MACOS_APP_CLI, os.X_OK):
        return _MACOS_APP_CLI
    return None


def _command_env(cli):
    # The app executable doubles as the GUI; scripts must explicitly select CLI
    # mode. Copy the environment so PATH and user configuration are preserved.
    if sys.platform == "darwin" and os.path.realpath(cli) == _MACOS_APP_CLI:
        return {**os.environ, "TAILSCALE_BE_CLI": "1"}
    return None


def status() -> dict:
    out = {
        "installed": False,
        "running": False,
        "magic_dns_name": "",
        "tailnet_ips": [],
        "cert_domains": [],
    }
    cli = _cli()
    if not cli:
        return out
    out["installed"] = True
    try:
        r = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=10, env=_command_env(cli))
        if r.returncode != 0:
            return out
        data = json.loads(r.stdout or "{}")
        out["running"] = data.get("BackendState") == "Running"
        self_ = data.get("Self") or {}
        out["magic_dns_name"] = (self_.get("DNSName") or "").rstrip(".")
        out["tailnet_ips"] = self_.get("TailscaleIPs") or []
        out["cert_domains"] = data.get("CertDomains") or []
    except Exception:
        pass
    return out


def _run(args):
    try:
        r = subprocess.run(args, capture_output=True, text=True, timeout=20, env=_command_env(args[0]))
        if r.returncode != 0:
            # Command output can contain credentials or home paths. Keep only
            # stable process metadata in the local log, not arbitrary output.
            logger.error("Tailscale command exited with code %s", r.returncode)
            return {"ok": False, "error": "tailscale command failed"}
        return {"ok": True, "error": ""}
    except (OSError, subprocess.TimeoutExpired, UnicodeDecodeError) as exc:
        logger.error("Tailscale command failed (%s)", type(exc).__name__)
        return {"ok": False, "error": "tailscale command failed"}


def serve_enable(port: int | None = None) -> dict:
    if port is None:
        port = network_share.backend_port()
    cli = _cli()
    if not cli:
        return {"ok": False, "error": "Tailscale CLI not found. Install Tailscale and sign in first."}
    st = status()
    if not st.get("running"):
        return {
            "ok": False,
            "error": "Tailscale is installed but not running. Run `tailscale up` (and sign in), then retry.",
        }
    dns = st.get("magic_dns_name", "")
    target = f"http://127.0.0.1:{port}"

    # Use HTTPS only if the tailnet has certificate domains provisioned;
    # otherwise `--https` 404s ("error enabling https feature"). Fall back to
    # HTTP on any failure.
    if st.get("cert_domains"):
        https = _run([cli, "serve", "--bg", "--https=443", target])
        if https["ok"]:
            return {"ok": True, "scheme": "https", "url": f"https://{dns}" if dns else ""}

    http = _run([cli, "serve", "--bg", "--http=80", target])
    if http["ok"]:
        return {
            "ok": True,
            "scheme": "http",
            "url": f"http://{dns}" if dns else "",
            "note": (
                "Served over HTTP on your tailnet (the WireGuard tunnel encrypts it). "
                f"For an https:// URL, enable HTTPS Certificates in the admin console ({_ADMIN_DNS})."
            ),
        }
    return {
        "ok": False,
        "error": (
            f"tailscale serve failed: {http['error'] or 'unknown error'}. "
            f"Ensure MagicDNS is enabled for your tailnet ({_ADMIN_DNS})."
        ),
    }


def serve_disable() -> dict:
    cli = _cli()
    if not cli:
        return {"ok": True}
    result = _run([cli, "serve", "reset"])
    if not result["ok"]:
        logger.warning("Tailscale sharing could not be disabled")
        return {
            "ok": False,
            "error": "Tailscale sharing could not be disabled. Check that Tailscale is running and retry.",
        }
    return {"ok": True}
