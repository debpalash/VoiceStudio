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
import re
from urllib.parse import parse_qsl, urlsplit, urlunsplit
from html import escape

from services.telephony.session import StreamEvent

NAME = "twilio"
VOICE_PATH = "/integrations/twilio/voice"
STREAM_PATH = "/integrations/twilio/stream"
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
