"""Twilio provider adapter: webhook signatures, TwiML, Media Streams frames.

Everything Twilio-specific lives here so the call session
(:mod:`services.telephony.session`) stays provider-agnostic. Plivo and Telnyx
speak near-identical bidirectional media-stream protocols (JSON envelopes with
base64 μ-law payloads); an adapter for either implements the same
:class:`services.telephony.session.MediaStreamProvider` surface.

References: https://www.twilio.com/docs/usage/webhooks/webhooks-security and
https://www.twilio.com/docs/voice/media-streams/websocket-messages
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from html import escape

from services.telephony.session import StreamEvent

NAME = "twilio"
VOICE_PATH = "/integrations/twilio/voice"
STREAM_PATH = "/integrations/twilio/stream"
STATUS_PATH = "/integrations/twilio/status"
SIGNATURE_HEADER = "x-twilio-signature"
ACCOUNT_SID_RE = re.compile(r"^AC[0-9a-fA-F]{32}$")
CALL_SID_RE = re.compile(r"^CA[0-9a-fA-F]{32}$")
#: Twilio webhooks are small form posts; anything bigger is not Twilio.
MAX_WEBHOOK_BYTES = 16 * 1024


def parse_form(body: bytes) -> list[tuple[str, str]]:
    """Decode an ``application/x-www-form-urlencoded`` body, keeping repeats."""
    return parse_qsl(body.decode("utf-8", errors="strict"), keep_blank_values=True)


def compute_signature(auth_token: str, url: str, params: list[tuple[str, str]]) -> str:
    """Twilio's documented scheme: HMAC-SHA1 over the full URL followed by each
    POST parameter name+value, sorted by name (repeats sorted by value),
    base64-encoded."""
    payload = url + "".join(k + v for k, v in sorted(params))
    digest = hmac.new(auth_token.encode("utf-8"), payload.encode("utf-8"), hashlib.sha1)
    return base64.b64encode(digest.digest()).decode("ascii")


def _port_variants(url: str) -> list[str]:
    """The URL as configured, plus with the default port added/removed.

    Twilio's own validators accept both spellings: depending on the call path
    the signed URL may or may not carry an explicit ``:443``.
    """
    parts = urlsplit(url)
    variants = [url]
    default = {"https": 443, "http": 80}.get(parts.scheme)
    if default is None or not parts.hostname:
        return variants
    host = parts.hostname if ":" not in parts.hostname else f"[{parts.hostname}]"
    if parts.port is None:
        variants.append(urlunsplit(parts._replace(netloc=f"{host}:{default}")))
    elif parts.port == default:
        variants.append(urlunsplit(parts._replace(netloc=host)))
    return variants


def signature_valid(
    auth_token: str, url: str, params: list[tuple[str, str]], signature: str
) -> bool:
    if not auth_token or not signature:
        return False
    supplied = signature.strip().encode("ascii", errors="replace")
    return any(
        hmac.compare_digest(compute_signature(auth_token, candidate, params).encode("ascii"), supplied)
        for candidate in _port_variants(url)
    )


def _attr(value: str) -> str:
    """A double-quoted XML attribute value (escapes & < > " ')."""
    return f'"{escape(value, quote=True)}"'


def connect_twiml(stream_url: str, parameters: dict[str, str]) -> str:
    """``<Connect><Stream>`` a bidirectional media stream, then hang up.

    Twilio moves on to the next verb once the stream's WebSocket closes, so the
    trailing ``<Hangup/>`` ends the call when VoiceStudio finishes speaking.
    """
    params = "".join(
        f"<Parameter name={_attr(k)} value={_attr(v)}/>" for k, v in parameters.items()
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f"<Response><Connect><Stream url={_attr(stream_url)}>{params}</Stream>"
        "</Connect><Hangup/></Response>"
    )


def hangup_twiml() -> str:
    """End the call without speaking (an outbound call nobody is waiting for)."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Hangup/></Response>'


def reject_twiml() -> str:
    """Decline the call with a busy signal (at capacity / rate limited)."""
    return '<?xml version="1.0" encoding="UTF-8"?><Response><Reject reason="busy"/></Response>'


def parse_event(message: dict) -> StreamEvent | None:
    """Normalize one inbound Media Streams message; None for unknown events."""
    event = message.get("event")
    stream_id = str(message.get("streamSid") or "")
    if event == "connected":
        return StreamEvent(kind="connected")
    if event == "start":
        start = message.get("start") or {}
        custom = start.get("customParameters") or {}
        return StreamEvent(
            kind="start",
            stream_id=str(start.get("streamSid") or stream_id),
            call_id=str(start.get("callSid") or ""),
            account_id=str(start.get("accountSid") or ""),
            params={str(k): str(v) for k, v in custom.items()} if isinstance(custom, dict) else {},
        )
    if event == "media":
        media = message.get("media") or {}
        try:
            payload = base64.b64decode(str(media.get("payload") or ""), validate=True)
        except (ValueError, TypeError):
            payload = b""
        return StreamEvent(
            kind="media", stream_id=stream_id, payload=payload, track=str(media.get("track") or "")
        )
    if event == "mark":
        return StreamEvent(
            kind="mark", stream_id=stream_id, mark=str((message.get("mark") or {}).get("name") or "")
        )
    if event == "stop":
        return StreamEvent(kind="stop", stream_id=stream_id)
    return None


def media_message(stream_id: str, payload: bytes) -> dict:
    return {
        "event": "media",
        "streamSid": stream_id,
        "media": {"payload": base64.b64encode(payload).decode("ascii")},
    }


def mark_message(stream_id: str, name: str) -> dict:
    return {"event": "mark", "streamSid": stream_id, "mark": {"name": name}}


def clear_message(stream_id: str) -> dict:
    """Drop audio Twilio has buffered but not yet played (barge-in hook)."""
    return {"event": "clear", "streamSid": stream_id}


# ── REST API (outbound calls) ───────────────────────────────────────────────
# Only ever called from an explicit user action (placing or hanging up a call).
# The host is fixed; the only caller-supplied path elements are the Account SID
# and Call SID, both validated against their exact formats first.

API_BASE = "https://api.twilio.com/2010-04-01"
_REST_TIMEOUT_S = 15.0


class TwilioAPIError(RuntimeError):
    """Twilio answered with an error. The message is Twilio's own text (it
    names the problem, e.g. an unverified number on a trial account)."""

    def __init__(self, status: int, message: str, code: int | None = None):
        super().__init__(message)
        self.status = status
        self.code = code


def _http_post_form(url: str, fields: list[tuple[str, str]], username: str, password: str) -> tuple[int, bytes]:
    """POST a form with HTTP Basic auth; returns ``(status, body)``. Test seam."""
    import urllib.error
    import urllib.request

    credentials = base64.b64encode(f"{username}:{password}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        url,
        data=urlencode(fields).encode("ascii"),
        method="POST",
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
        },
    )
    if not url.startswith(API_BASE + "/"):
        raise ValueError("Twilio REST calls only go to api.twilio.com over https")
    try:
        with urllib.request.urlopen(request, timeout=_REST_TIMEOUT_S) as resp:  # nosec B310 — https api.twilio.com only (checked above)
            return resp.status, resp.read()
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read() or b""


def _rest(account_sid: str, auth_token: str, path: str, fields: list[tuple[str, str]]) -> dict:
    if not ACCOUNT_SID_RE.match(account_sid or ""):
        raise TwilioAPIError(400, "The Twilio Account SID is not valid")
    try:
        status, body = _http_post_form(f"{API_BASE}/Accounts/{account_sid}/{path}", fields, account_sid, auth_token)
    except OSError as exc:
        raise TwilioAPIError(502, f"Could not reach Twilio ({type(exc).__name__})") from exc
    try:
        data = json.loads(body.decode("utf-8")) if body else {}
    except ValueError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    if not 200 <= status < 300:
        code = data.get("code")
        raise TwilioAPIError(
            status,
            str(data.get("message") or f"Twilio returned HTTP {status}"),
            code if isinstance(code, int) else None,
        )
    return data


def create_call(
    account_sid: str,
    auth_token: str,
    *,
    to: str,
    from_: str,
    url: str,
    status_callback: str,
    time_limit_s: int,
    ring_timeout_s: int = 30,
) -> str:
    """Place an outbound call and return its CallSid. Twilio fetches TwiML from
    the signed voice webhook ``url`` once the callee answers, and reports
    progress to ``status_callback``."""
    fields = [
        ("To", to),
        ("From", from_),
        ("Url", url),
        ("Method", "POST"),
        ("StatusCallback", status_callback),
        ("StatusCallbackMethod", "POST"),
        *[("StatusCallbackEvent", e) for e in ("initiated", "ringing", "answered", "completed")],
        ("Timeout", str(int(ring_timeout_s))),
        ("TimeLimit", str(int(time_limit_s))),
    ]
    sid = str(_rest(account_sid, auth_token, "Calls.json", fields).get("sid") or "")
    if not CALL_SID_RE.match(sid):
        raise TwilioAPIError(502, "Twilio did not return a call ID")
    return sid


def end_call(account_sid: str, auth_token: str, call_sid: str, *, answered: bool) -> None:
    """Hang up an answered call, or cancel one that is still ringing."""
    if not CALL_SID_RE.match(call_sid or ""):
        raise TwilioAPIError(400, "The call ID is not valid")
    _rest(account_sid, auth_token, f"Calls/{call_sid}.json", [("Status", "completed" if answered else "canceled")])
