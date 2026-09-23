"""Twilio phone-call integration.

Two routers with different audiences:

- ``router`` (main backend, admin-only): configuration, status and the local
  phone-quality preview used by the Electron Integrations → Twilio page.
- ``webhook_router`` (ONLY on the telephony gateway listener, see
  ``services.telephony.gateway``): the public endpoints a user's tunnel
  exposes to Twilio. Every request is authenticated — the voice webhook by
  ``X-Twilio-Signature`` (HMAC-SHA1 with the user's Auth Token), the media
  stream by a single-use per-call token issued in the TwiML.

Off by default; nothing is reachable from outside until the user enables it
and runs a tunnel. The Auth Token is stored encrypted and never returned.
"""
from __future__ import annotations

import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request, WebSocket
from fastapi.responses import Response
from pydantic import BaseModel, Field

from api.dependencies import require_admin
from services.telephony import config, gateway, session
from services.telephony import twilio as provider

logger = logging.getLogger("omnivoice.telephony")

router = APIRouter(
    prefix="/api/integrations/twilio",
    tags=["integrations"],
    dependencies=[Depends(require_admin)],
)
webhook_router = APIRouter(tags=["telephony"])

_XML = "text/xml"
_preview_lock = asyncio.Lock()


# ── Gateway app ─────────────────────────────────────────────────────────────


def build_gateway_app():
    """The telephony gateway's ASGI app: the public routes and nothing else."""
    from fastapi import FastAPI

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    app.include_router(webhook_router)
    return app


async def start_gateway_if_enabled() -> None:
    """Backend startup: resume the listener when the user left it enabled."""
    try:
        enabled = config.load().enabled
    except Exception:  # noqa: BLE001 — settings unreadable: stay off
        return
    if enabled:
        try:
            await gateway.start(build_gateway_app())
        except Exception as exc:  # noqa: BLE001 — never block startup
            logger.warning("Telephony gateway not started: %s", exc)


# ── Admin API (main backend) ────────────────────────────────────────────────


def _state() -> dict:
    cfg = config.load()
    token_present = config.has_auth_token()
    calls = session.registry.snapshot()
    return {
        "enabled": cfg.enabled,
        "account_sid": cfg.account_sid,
        "has_auth_token": token_present,
        "public_base_url": cfg.public_base_url,
        "voice_id": cfg.voice_id,
        "engine": cfg.engine,
        "language": cfg.language,
        "greeting": cfg.greeting,
        "webhook_url": cfg.webhook_url,
        "missing": config.missing_for_enable(cfg, token_present),
        "listener": gateway.state(),
        "calls": {**calls, "max_concurrent": config.max_concurrent_calls()},
        "limits": {
            "max_call_seconds": config.max_call_seconds(),
            "webhooks_per_minute": config.webhooks_per_minute(),
            "max_greeting_chars": config.MAX_GREETING_CHARS,
        },
    }


class _ConfigBody(BaseModel):
    enabled: Optional[bool] = None
    account_sid: Optional[str] = Field(None, max_length=64)
    auth_token: Optional[str] = Field(None, max_length=256, description="'' clears, None keeps")
    public_base_url: Optional[str] = Field(None, max_length=512)
    voice_id: Optional[str] = Field(None, max_length=128)
    engine: Optional[str] = Field(None, max_length=128)
    language: Optional[str] = Field(None, max_length=64)
    greeting: Optional[str] = Field(None, max_length=config.MAX_GREETING_CHARS + 100)


def _config_error(exc: config.ConfigError) -> HTTPException:
    return HTTPException(status_code=400, detail={"code": exc.code, "message": str(exc)})


@router.get("/state")
def get_state():
    return _state()


async def _go_offline() -> None:
    """Every path that leaves the integration off: revoke stream tokens
    already issued (a call told to connect cannot start after a later
    re-enable), then stop the listener."""
    session.tokens.reset()
    await gateway.stop()


@router.put("/config")
async def put_config(body: _ConfigBody):
    current = config.load()
    try:
        updated = config.TwilioConfig(
            enabled=current.enabled if body.enabled is None else body.enabled,
            account_sid=current.account_sid
            if body.account_sid is None
            else config.normalize_account_sid(body.account_sid),
            public_base_url=current.public_base_url
            if body.public_base_url is None
            else config.normalize_public_base_url(body.public_base_url),
            voice_id=current.voice_id if body.voice_id is None else body.voice_id.strip(),
            engine=current.engine if body.engine is None else body.engine.strip(),
            language=current.language if body.language is None else body.language.strip(),
            greeting=current.greeting
            if body.greeting is None
            else config.normalize_greeting(body.greeting),
        )
    except config.ConfigError as exc:
        raise _config_error(exc) from exc
    if body.auth_token is not None:
        config.set_auth_token(body.auth_token)
    missing = config.missing_for_enable(updated, config.has_auth_token())
    if updated.enabled and missing:
        # Save the fields, but never switch on a half-configured endpoint.
        config.save(config.TwilioConfig(**{**updated.__dict__, "enabled": False}))
        await _go_offline()
        raise HTTPException(
            status_code=400,
            detail={"code": "incomplete", "missing": missing, "message": "Complete the setup first"},
        )
    config.save(updated)
    if updated != current:
        session.ulaw_cache.clear()
    if updated.enabled:
        try:
            await gateway.start(build_gateway_app())
        except Exception as exc:  # noqa: BLE001
            config.save(config.TwilioConfig(**{**updated.__dict__, "enabled": False}))
            await _go_offline()
            raise HTTPException(
                status_code=503,
                detail={"code": "listener_failed", "message": str(exc)},
            ) from exc
    else:
        await _go_offline()
    return _state()


class _PreviewBody(BaseModel):
    text: Optional[str] = Field(None, max_length=config.MAX_GREETING_CHARS + 100)
    voice_id: Optional[str] = Field(None, max_length=128)
    engine: Optional[str] = Field(None, max_length=128)
    language: Optional[str] = Field(None, max_length=64)


@router.post("/test")
async def test_locally(body: _PreviewBody):
    """Render the greeting exactly as a caller would hear it — resampled to
    8 kHz, μ-law encoded, decoded — and return it as a WAV. Works while the
    integration is disabled; nothing leaves the machine. Also warms the cache,
    so a following call with the same settings answers instantly."""
    from api.routers.tts_stream import StreamUnavailableError
    from services.telephony.audio import ulaw_to_wav

    cfg = config.load()
    try:
        text = config.normalize_greeting(cfg.greeting if body.text is None else body.text)
    except config.ConfigError as exc:
        raise _config_error(exc) from exc
    if not text:
        raise HTTPException(status_code=400, detail={"code": "missing_greeting", "message": "Enter a greeting"})
    kw = {
        "voice": cfg.voice_id if body.voice_id is None else body.voice_id.strip(),
        "engine": cfg.engine if body.engine is None else body.engine.strip(),
        "language": cfg.language if body.language is None else body.language.strip(),
    }
    if _preview_lock.locked():
        raise HTTPException(status_code=409, detail={"code": "busy", "message": "A preview is already rendering"})
    async with _preview_lock:
        try:
            ulaw = await session.render_ulaw_all(text, **kw)
        except StreamUnavailableError as exc:
            raise HTTPException(
                status_code=409, detail={"code": "engine_unavailable", "message": str(exc)}
            ) from exc
    return Response(ulaw_to_wav(ulaw), media_type="audio/wav", headers={"Cache-Control": "no-store"})


# ── Public endpoints (telephony gateway only) ───────────────────────────────


def _forbidden() -> Response:
    # One uniform body: never tell a prober which check failed.
    return Response("Forbidden", status_code=403, media_type="text/plain")


async def _read_limited(request: Request) -> bytes | None:
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > provider.MAX_WEBHOOK_BYTES):
        return None
    body = bytearray()
    async for chunk in request.stream():
        body += chunk
        if len(body) > provider.MAX_WEBHOOK_BYTES:
            return None
    return bytes(body)


@webhook_router.post(provider.VOICE_PATH)
async def twilio_voice_webhook(request: Request):
    # Captured before any await: a disable while this request is in flight
    # bumps the epoch, and no token is then issued for it.
    epoch = session.tokens.epoch
    cfg = config.load()
    auth_token = config.auth_token() if cfg.enabled else ""
    if not cfg.enabled or not auth_token or not cfg.account_sid or not cfg.public_base_url:
        return _forbidden()
    body = await _read_limited(request)
    if body is None:
        return Response("Payload Too Large", status_code=413)
    try:
        params = provider.parse_form(body)
    except (UnicodeDecodeError, ValueError):
        return Response("Bad Request", status_code=400)
    # Twilio signs the URL it called: the tunnel's public URL, not our loopback one.
    url = cfg.webhook_url + (f"?{request.url.query}" if request.url.query else "")
    signature = request.headers.get(provider.SIGNATURE_HEADER, "")
    if not provider.signature_valid(auth_token, url, params, signature):
        # Throttle only unsigned traffic, after verifying: a flood of forged
        # requests must never lock out genuine, correctly signed webhooks.
        if session.signature_failures_exceeded():
            return Response("Too Many Requests", status_code=429, headers={"Retry-After": "60"})
        session.note_signature_failure()
        logger.warning("Rejected a Twilio webhook with an invalid signature")
        return _forbidden()
    fields = dict(params)
    call_sid = fields.get("CallSid", "")
    if fields.get("AccountSid") != cfg.account_sid or not provider.CALL_SID_RE.match(call_sid):
        session.registry.note("rejected_signature", call_sid)
        return _forbidden()
    max_calls = config.max_concurrent_calls()
    if not session.webhook_window.allow(config.webhooks_per_minute()) or not session.registry.has_capacity(
        max_calls, session.tokens.pending()
    ):
        session.registry.note("busy", call_sid)
        return Response(provider.reject_twiml(), media_type=_XML)
    token = session.tokens.issue(call_sid, epoch)
    if token is None:
        return _forbidden()
    return Response(provider.connect_twiml(cfg.stream_url, {"token": token}), media_type=_XML)


@webhook_router.websocket(provider.STREAM_PATH)
async def twilio_media_stream(websocket: WebSocket):
    cfg = config.load()
    if not cfg.enabled or not cfg.greeting:
        await websocket.close(code=1008)
        return
    await session.run_call(
        websocket,
        provider,
        account_id=cfg.account_sid,
        responder_factory=lambda: session.AnnouncementResponder(
            cfg.greeting, voice=cfg.voice_id, engine=cfg.engine, language=cfg.language
        ),
        max_calls=config.max_concurrent_calls(),
        max_seconds=config.max_call_seconds(),
    )
