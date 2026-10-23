"""Thin wrapper around the `tailscale` CLI. Every call degrades gracefully
when the CLI is missing or not logged in (installed/running flags).

HTTPS serving requires the tailnet's HTTPS Certificates feature (reported
as non-empty ``CertDomains`` in `status --json`). Most tailnets have it off —
and forcing `--https` then fails with "error enabling https feature: 404". So
we serve over **HTTP** by default — the WireGuard tunnel already encrypts the
transport — and only use HTTPS when the tailnet actually has certs
provisioned.

The serve config is machine-wide and shared with every other app on the
node, so VoiceStudio only ever touches its own port: it serves on a
dedicated port (10000 by default, never the root 443/80 another app may own)
and Disable turns off only the ports that proxy to this backend. It never
runs `tailscale serve reset`, which would wipe every other share on the
machine.
"""
import json
import logging
import os
import shutil
import subprocess

from services import network_share

_ADMIN_DNS = "https://login.tailscale.com/admin/dns"
logger = logging.getLogger("omnivoice.tailscale")

DEFAULT_SERVE_PORT = 10000
HTTPS_PORT_ENV = "OMNIVOICE_TAILSCALE_HTTPS_PORT"
HTTP_PORT_ENV = "OMNIVOICE_TAILSCALE_HTTP_PORT"


def _cli():
    return shutil.which("tailscale")


def _env_port(name: str) -> int:
    """A serve port from the environment; the default on a missing or bad value."""
    raw = os.environ.get(name)
    if raw is None:
        return DEFAULT_SERVE_PORT
    try:
        port = int(raw)
    except (TypeError, ValueError):
        return DEFAULT_SERVE_PORT
    return port if 0 < port < 65536 else DEFAULT_SERVE_PORT


def https_port() -> int:
    return _env_port(HTTPS_PORT_ENV)


def http_port() -> int:
    return _env_port(HTTP_PORT_ENV)


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
        r = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, timeout=10)
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
        r = subprocess.run(args, capture_output=True, text=True, timeout=20)
        if r.returncode != 0:
            diagnostic = (r.stderr or r.stdout or "tailscale serve failed").strip()
            logger.error(
                "Tailscale command exited with code %s: %.2000r",
                r.returncode,
                diagnostic,
            )
            return {"ok": False, "error": "tailscale command failed"}
        return {"ok": True, "error": ""}
    except Exception:
        logger.exception("Tailscale command failed")
        return {"ok": False, "error": "tailscale command failed"}


def _serve_ports(cli) -> dict[int, dict] | None:
    """The node's current serve config, by port: ``{port: {"scheme", "proxies"}}``.

    ``scheme`` is ``"https"``/``"http"`` (``None`` for a raw TCP forward) and
    ``proxies`` is the set of proxy targets the port's web handlers point at.
    ``None`` when the config cannot be read.
    """
    try:
        r = subprocess.run(
            [cli, "serve", "status", "--json"], capture_output=True, text=True, timeout=10
        )
        if r.returncode != 0:
            return None
        data = json.loads(r.stdout or "{}") or {}
    except Exception:
        logger.exception("Could not read the Tailscale serve config")
        return None
    ports: dict[int, dict] = {}
    for raw_port, tcp in (data.get("TCP") or {}).items():
        try:
            port = int(raw_port)
        except (TypeError, ValueError):
            continue
        tcp = tcp or {}
        scheme = "https" if tcp.get("HTTPS") else "http" if tcp.get("HTTP") else None
        ports[port] = {"scheme": scheme, "proxies": set()}
    for host_port, web in (data.get("Web") or {}).items():
        try:
            port = int(str(host_port).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        entry = ports.setdefault(port, {"scheme": None, "proxies": set()})
        for handler in ((web or {}).get("Handlers") or {}).values():
            entry["proxies"].add((handler or {}).get("Proxy") or "<non-proxy handler>")
    return ports


def _port_conflict(ports: dict[int, dict] | None, port: int, target: str) -> bool:
    """True when ``port`` already serves something other than this backend."""
    if not ports or port not in ports:
        return False
    return ports[port]["proxies"] != {target}


def _conflict_error(port: int, env: str) -> dict:
    return {
        "ok": False,
        "error": (
            f"Tailscale port {port} is already serving another app on this machine. "
            f"Set {env} to a free port and retry."
        ),
    }


def _url(scheme: str, dns: str, port: int) -> str:
    if not dns:
        return ""
    default = 443 if scheme == "https" else 80
    return f"{scheme}://{dns}" if port == default else f"{scheme}://{dns}:{port}"


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
    # Never take over a port another app on this node is already serving.
    ports = _serve_ports(cli)
    https_p, http_p = https_port(), http_port()

    # Use HTTPS only if the tailnet has certificate domains provisioned;
    # otherwise `--https` 404s ("error enabling https feature"). Fall back to
    # HTTP on any failure.
    if st.get("cert_domains"):
        if _port_conflict(ports, https_p, target):
            return _conflict_error(https_p, HTTPS_PORT_ENV)
        https = _run([cli, "serve", "--bg", f"--https={https_p}", target])
        if https["ok"]:
            return {"ok": True, "scheme": "https", "url": _url("https", dns, https_p)}

    if _port_conflict(ports, http_p, target):
        return _conflict_error(http_p, HTTP_PORT_ENV)
    http = _run([cli, "serve", "--bg", f"--http={http_p}", target])
    if http["ok"]:
        return {
            "ok": True,
            "scheme": "http",
            "url": _url("http", dns, http_p),
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


def serve_disable(port: int | None = None) -> dict:
    """Turn off only the serve ports that proxy to this backend.

    Other shares on the node (another app on 443, a second service on 8443)
    are left alone. This also cleans up a share an older version made on the
    root 443/80 port, as long as it points only at this backend.
    """
    if port is None:
        port = network_share.backend_port()
    cli = _cli()
    if not cli:
        return {"ok": True}
    failure = {
        "ok": False,
        "error": "Tailscale sharing could not be disabled. Check that Tailscale is running and retry.",
    }
    ports = _serve_ports(cli)
    if ports is None:
        logger.warning("Tailscale sharing could not be disabled: serve config unreadable")
        return failure
    target = f"http://127.0.0.1:{port}"
    for serve_port, entry in sorted(ports.items()):
        if entry["proxies"] != {target} or entry["scheme"] is None:
            continue
        result = _run([cli, "serve", f"--{entry['scheme']}={serve_port}", "off"])
        if not result["ok"]:
            logger.warning("Tailscale sharing could not be disabled on port %s", serve_port)
            return failure
    return {"ok": True}
