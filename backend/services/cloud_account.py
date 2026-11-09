"""Short-lived VoiceStudio Cloud access tokens from Electron main.

The refresh token never leaves Electron main. Main answers {"signed_in": false}
whenever account features or sync are switched off, or the backend is remote,
so "no token" here means "make no Cloud call". Nothing is cached on this side:
main is asked on every call (a loopback round trip), so a switch turned off in
the app takes effect on the very next call. The capability is removed from
os.environ at import so engine and tool subprocesses never inherit it.
"""
from __future__ import annotations

import json
import os
import urllib.request
from typing import Optional
from urllib.parse import urlsplit

_url = os.environ.pop("VOICESTUDIO_ACCOUNT_BRIDGE_URL", "")
_capability = os.environ.pop("VOICESTUDIO_ACCOUNT_BRIDGE_TOKEN", "")


def _configure(url: str, capability: str) -> None:
    """Test seam."""
    global _url, _capability
    _url, _capability = url, capability


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def _ask(fresh: bool = False) -> Optional[dict]:
    """Main's answer, or None when no Cloud call may be made."""
    target = urlsplit(_url)
    if target.scheme != "http" or target.hostname != "127.0.0.1" or not _capability:
        return None
    request = urllib.request.Request(
        _url + ("/token?fresh=1" if fresh else "/token"),
        headers={"Authorization": "Bearer " + _capability},
    )
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())
    try:
        with opener.open(request, timeout=10) as response:
            body = json.loads(response.read(65_536))
    except (OSError, ValueError):
        return None
    if not isinstance(body, dict) or not body.get("signed_in"):
        return None
    token = body.get("access_token")
    return body if isinstance(token, str) and token else None


def access_token(fresh: bool = False) -> Optional[str]:
    """The current access token, or None when the app says no Cloud call may be made.

    ``fresh=True`` tells main the last token was rejected (401), so it refreshes.
    """
    body = _ask(fresh)
    return body["access_token"] if body else None


def _usable_endpoint(url: object) -> bool:
    if not isinstance(url, str):
        return False
    parts = urlsplit(url)
    return parts.scheme == "https" or (parts.scheme == "http" and parts.hostname == "127.0.0.1")


def session() -> Optional[dict]:
    """Token, Organization and sync endpoint for the engine; None means stay idle."""
    body = _ask()
    if not body or not isinstance(body.get("organization_id"), str) or not body["organization_id"]:
        return None
    if not _usable_endpoint(body.get("sync_api")):
        return None
    return {"access_token": body["access_token"], "organization_id": body["organization_id"],
            "sync_api": body["sync_api"]}
