"""omnivoice-mcp — stdio ↔ Streamable-HTTP MCP proxy (Wave 2.2).

Adapted from voicebox (https://github.com/jamiepine/voicebox), MIT License,
Copyright (c) voicebox contributors.

Some MCP clients only speak stdio. They spawn this binary; we pipe each
JSON-RPC message to ``http://127.0.0.1:<port>/mcp/`` (the FastMCP app mounted
on the running VoiceStudio backend) and stream the server's response back.

Environment variables:
  OMNIVOICE_URL        full backend base URL (http or https, may carry a
                       path prefix, e.g. https://gpu-box/voicestudio). Wins
                       over OMNIVOICE_HOST / OMNIVOICE_PORT.
  OMNIVOICE_PORT       backend port (default 3900).
  OMNIVOICE_HOST       host (default 127.0.0.1).
  OMNIVOICE_API_KEY    sent as a Bearer token when set (remote backends that
                       require an API key; loopback never needs one). Refused
                       for a remote plain-http URL: use https:// there.
  OMNIVOICE_CLIENT_ID  forwarded as X-OmniVoice-Client-Id on every request
                       (drives per-agent voice binding).

Stdout is JSON-RPC only. Diagnostics go to stderr.
Exit 0 on clean EOF, 1 on transport error, 2 if the backend never answers.

Usage in an MCP client config (stdio):
    command: python
    args: ["-m", "backend.mcp_shim"]
    env: { OMNIVOICE_CLIENT_ID: "claude-code" }
"""

from __future__ import annotations

import asyncio
import ipaddress
import json
import os
import sys
from typing import Any
from urllib.parse import urlsplit

import httpx

CLIENT_ID_HEADER = "X-OmniVoice-Client-Id"
SESSION_HEADER = "mcp-session-id"
HEALTH_TIMEOUT_S = 30.0
DEFAULT_PORT = 3900


def _err(msg: str) -> None:
    print(f"omnivoice-mcp: {msg}", file=sys.stderr, flush=True)


def _base_url() -> tuple[str, str]:
    base = os.environ.get("OMNIVOICE_URL", "").strip().rstrip("/")
    if not base:
        host = os.environ.get("OMNIVOICE_HOST", "127.0.0.1")
        port = int(os.environ.get("OMNIVOICE_PORT", str(DEFAULT_PORT)))
        base = f"http://{host}:{port}"
    return f"{base}/mcp/", f"{base}/health"


def _credentials_allowed(url: str) -> bool:
    """A key may go over https, or to loopback; never plain http elsewhere."""
    target = urlsplit(url)
    host = (target.hostname or "").strip("[]")
    if target.scheme.lower() == "https" or host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


async def _wait_for_backend(
    client: httpx.AsyncClient, health_url: str, headers: dict[str, str] | None = None
) -> bool:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + HEALTH_TIMEOUT_S
    while loop.time() < deadline:
        try:
            r = await client.get(health_url, headers=headers, timeout=2.0)
            if r.status_code == 200:
                return True
        except Exception:
            pass
        await asyncio.sleep(0.5)
    return False


async def _read_stdin_line() -> str | None:
    loop = asyncio.get_running_loop()
    line = await loop.run_in_executor(None, sys.stdin.readline)
    return line or None


def _write_stdout(obj: Any) -> None:
    sys.stdout.write(json.dumps(obj, separators=(",", ":")))
    sys.stdout.write("\n")
    sys.stdout.flush()


async def _handle_request(
    client: httpx.AsyncClient,
    url: str,
    raw: str,
    headers: dict[str, str],
    session_id: list[str | None],
) -> None:
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        _err(f"invalid JSON on stdin: {exc}")
        return

    req_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        **headers,
    }
    if session_id[0]:
        req_headers[SESSION_HEADER] = session_id[0]

    is_notification = isinstance(message, dict) and "id" not in message

    async with client.stream("POST", url, headers=req_headers, content=raw.encode("utf-8")) as response:
        if session_id[0] is None:
            sid = response.headers.get(SESSION_HEADER)
            if sid:
                session_id[0] = sid

        if response.status_code == 202:
            return  # notification acknowledged
        if response.status_code >= 400:
            body = await response.aread()
            _err(f"server {response.status_code}: {body.decode('utf-8', errors='replace')[:400]}")
            if is_notification:
                return
            _write_stdout({
                "jsonrpc": "2.0",
                "id": message.get("id"),
                "error": {"code": -32000, "message": f"VoiceStudio MCP proxy got HTTP {response.status_code}"},
            })
            return

        ctype = response.headers.get("content-type", "")
        if "text/event-stream" in ctype:
            async for line in response.aiter_lines():
                if line.startswith("data:"):
                    payload = line[5:].strip()
                    if not payload:
                        continue
                    try:
                        _write_stdout(json.loads(payload))
                    except json.JSONDecodeError:
                        _err(f"malformed SSE payload: {payload[:200]}")
        else:
            body = await response.aread()
            try:
                _write_stdout(json.loads(body))
            except json.JSONDecodeError:
                _err(f"non-JSON response ({ctype}): {body.decode('utf-8', errors='replace')[:200]}")


async def _run() -> int:
    url, health_url = _base_url()
    forward_headers: dict[str, str] = {}
    client_id = os.environ.get("OMNIVOICE_CLIENT_ID")
    if client_id:
        forward_headers[CLIENT_ID_HEADER] = client_id
    api_key = os.environ.get("OMNIVOICE_API_KEY", "").strip()
    if api_key:
        if not _credentials_allowed(url):
            _err("remote VoiceStudio API keys require https:// (set OMNIVOICE_URL)")
            return 1
        forward_headers["Authorization"] = f"Bearer {api_key}"

    session_id: list[str | None] = [None]

    async with httpx.AsyncClient(timeout=httpx.Timeout(300.0)) as client:
        if not await _wait_for_backend(client, health_url, forward_headers):
            _err(f"timed out waiting for VoiceStudio at {health_url} — is the app running?")
            return 2
        try:
            while True:
                line = await _read_stdin_line()
                if line is None:
                    return 0
                line = line.strip()
                if not line:
                    continue
                await _handle_request(client, url, line, forward_headers, session_id)
        except (KeyboardInterrupt, SystemExit):
            return 0
        except Exception as exc:
            _err(f"proxy failed: {exc!r}")
            return 1


def main() -> int:
    try:
        return asyncio.run(_run())
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
