"""Thin wrapper around the `tailscale` CLI. Every call degrades gracefully
when the CLI is missing or not logged in (installed/running flags).

`tailscale serve --https=443` requires the tailnet's HTTPS Certificates
feature (reported as non-empty ``CertDomains`` in `status --json`). Most
tailnets have it off — and forcing `--https` then fails with
"error enabling https feature: 404". So we serve over **HTTP** (`--http=80`)
by default — the WireGuard tunnel already encrypts the transport — and only
use HTTPS when the tailnet actually has certs provisioned.

Enable and Disable only touch handlers that proxy to this backend; other apps'
serve config on the same machine is never overwritten or reset.
"""
import json
import logging
import shutil
import subprocess
from urllib.parse import urlsplit

from services import network_share

_ADMIN_DNS = "https://login.tailscale.com/admin/dns"
logger = logging.getLogger("omnivoice.tailscale")


def _cli():
    return shutil.which("tailscale")


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
        r = subprocess.run([cli, "status", "--json"], capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=10)
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
        r = subprocess.run(args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=20)
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


# The serve config is machine-wide: every app on this node shares it. We only
# ever add or remove handlers that proxy to this backend, so another app's
# share is never overwritten (Enable) or wiped (Disable, which used to run
# `tailscale serve reset`).
_DEFAULT_PORTS = {"https": 443, "http": 80}
# Used when the default port already serves another app on this machine.
_FALLBACK_PORT = 10000
_LOOPBACK_HOSTS = {"127.0.0.1", "localhost"}


def _serve_config(cli):
    """Read the machine's current ``tailscale serve`` configuration.

    Returns the parsed dict (empty when nothing is served), or ``None`` when
    the config could not be read; callers must then leave the config alone.
    """
    try:
        r = subprocess.run(
            [cli, "serve", "status", "--json"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
        )
        if r.returncode != 0:
            logger.warning("tailscale serve status exited with code %s", r.returncode)
            return None
        out = r.stdout or ""
        # Some versions print a version-mismatch notice before the JSON.
        brace = out.find("{")
        config = json.loads(out[brace:]) if brace >= 0 else {}
        return config if isinstance(config, dict) else None
    except Exception:
        logger.exception("Could not read the tailscale serve configuration")
        return None


def _is_our_proxy(proxy, backend_port: int) -> bool:
    if not isinstance(proxy, str) or not proxy:
        return False
    raw = proxy if "://" in proxy else f"http://{proxy}"
    try:
        u = urlsplit(raw)
        port = u.port
    except ValueError:
        return False
    return (
        u.scheme == "http"
        and (u.hostname or "") in _LOOPBACK_HOSTS
        and port == backend_port
        and u.path in ("", "/")
    )


def _web_handlers(config: dict):
    """Yield (port, mount_path, handler) for every web handler in the config."""
    web = config.get("Web") or {}
    if not isinstance(web, dict):
        return
    for host_port, entry in web.items():
        try:
            port = int(str(host_port).rsplit(":", 1)[1])
        except (IndexError, ValueError):
            continue
        handlers = (entry or {}).get("Handlers") or {}
        if not isinstance(handlers, dict):
            continue
        for mount, handler in handlers.items():
            yield port, mount, handler if isinstance(handler, dict) else {}


def _port_owner(config: dict, port: int, backend_port: int) -> str:
    """'free', 'ours' (only this backend is served there) or 'other'."""
    tcp = (config.get("TCP") or {}).get(str(port))
    handlers = [h for p, _, h in _web_handlers(config) if p == port]
    if tcp is None and not handlers:
        return "free"
    if isinstance(tcp, dict) and (tcp.get("TCPForward") or tcp.get("TerminateTLS")):
        return "other"
    if handlers and all(_is_our_proxy(h.get("Proxy"), backend_port) for h in handlers):
        return "ours"
    return "other"


def _pick_port(config: dict, scheme: str, backend_port: int):
    for candidate in (_DEFAULT_PORTS[scheme], _FALLBACK_PORT):
        if _port_owner(config, candidate, backend_port) in ("free", "ours"):
            return candidate
        logger.info("Tailscale %s port %s already serves another app; not overwriting it", scheme, candidate)
    return None


def _url(scheme: str, dns: str, port: int) -> str:
    if not dns:
        return ""
    if _DEFAULT_PORTS.get(scheme) == port:
        return f"{scheme}://{dns}"
    return f"{scheme}://{dns}:{port}"


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
    config = _serve_config(cli)
    if config is None:
        return {"ok": False, "error": "Could not read the Tailscale serve config; nothing was changed."}
    dns = st.get("magic_dns_name", "")
    target = f"http://127.0.0.1:{port}"

    # Use HTTPS only if the tailnet has certificate domains provisioned;
    # otherwise `--https` 404s ("error enabling https feature"). Fall back to
    # HTTP on any failure.
    if st.get("cert_domains"):
        https_port = _pick_port(config, "https", port)
        if https_port is not None:
            https = _run([cli, "serve", "--bg", f"--https={https_port}", target])
            if https["ok"]:
                return {"ok": True, "scheme": "https", "url": _url("https", dns, https_port)}

    http_port = _pick_port(config, "http", port)
    if http_port is None:
        return {
            "ok": False,
            "error": (
                f"Tailscale ports {_DEFAULT_PORTS['http']} and {_FALLBACK_PORT} already serve other apps "
                "on this machine; VoiceStudio left them unchanged."
            ),
        }
    http = _run([cli, "serve", "--bg", f"--http={http_port}", target])
    if http["ok"]:
        return {
            "ok": True,
            "scheme": "http",
            "url": _url("http", dns, http_port),
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
    """Remove only the serve handlers that proxy to this backend."""
    if port is None:
        port = network_share.backend_port()
    cli = _cli()
    if not cli:
        return {"ok": True}
    failed = {
        "ok": False,
        "error": "Tailscale sharing could not be disabled. Check that Tailscale is running and retry.",
    }
    config = _serve_config(cli)
    if config is None:
        # Fail closed: without the config we cannot tell our share from others.
        logger.warning("Tailscale sharing could not be disabled: serve config unreadable")
        return failed
    tcp = config.get("TCP") or {}
    ours = sorted(
        {(p, mount) for p, mount, h in _web_handlers(config) if _is_our_proxy(h.get("Proxy"), port)}
    )
    for serve_port, mount in ours:
        entry = tcp.get(str(serve_port)) or {}
        scheme = "https" if entry.get("HTTPS") else "http"
        args = [cli, "serve", f"--{scheme}={serve_port}"]
        if mount not in ("", "/"):
            args.append(f"--set-path={mount}")
        args.append("off")
        if not _run(args)["ok"]:
            logger.warning("Tailscale sharing could not be disabled on port %s", serve_port)
            return failed
    return {"ok": True}
