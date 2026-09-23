"""Persisted Twilio configuration (local settings store only).

Non-secret fields are plain ``settings`` rows; the Auth Token is Fernet-
encrypted via :func:`services.settings_store.set_secret`, never returned by
any API, never logged, and never part of an export. Everything defaults OFF.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from urllib.parse import urlsplit

from services.telephony import twilio as provider

_PREFIX = "integrations.twilio."
SECRET_NAME = "integrations.twilio.auth_token"
MAX_GREETING_CHARS = 1000
DEFAULT_GREETING = ""


@dataclass(frozen=True)
class TwilioConfig:
    enabled: bool = False
    account_sid: str = ""
    public_base_url: str = ""
    voice_id: str = ""
    engine: str = ""
    language: str = ""
    greeting: str = DEFAULT_GREETING

    @property
    def webhook_url(self) -> str:
        return f"{self.public_base_url}{provider.VOICE_PATH}" if self.public_base_url else ""

    @property
    def stream_url(self) -> str:
        if not self.public_base_url:
            return ""
        return "wss://" + self.public_base_url.removeprefix("https://") + provider.STREAM_PATH


class ConfigError(ValueError):
    """A user-correctable configuration problem; ``code`` is UI-localizable."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def normalize_public_base_url(value: str) -> str:
    """The tunnel's public origin: ``https://host[:port]``, nothing else.

    Twilio signs the exact URL it calls, so the signature check reconstructs
    it from this value — never from the Host header a tunnel rewrites. A path,
    query, fragment or credentials would make that reconstruction ambiguous.
    """
    value = (value or "").strip().rstrip("/")
    if not value:
        return ""
    parts = urlsplit(value)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username
        or parts.password
        or parts.path
        or parts.query
        or parts.fragment
    ):
        raise ConfigError(
            "invalid_public_url",
            "Public base URL must be an https:// origin such as https://example.trycloudflare.com",
        )
    try:
        parts.port  # noqa: B018 — raises on a malformed port
    except ValueError as exc:
        raise ConfigError("invalid_public_url", "Public base URL has an invalid port") from exc
    return f"https://{parts.netloc.lower()}"


def normalize_account_sid(value: str) -> str:
    value = (value or "").strip()
    if value and not provider.ACCOUNT_SID_RE.match(value):
        raise ConfigError("invalid_account_sid", "Account SID starts with AC followed by 32 hex characters")
    return value


def normalize_greeting(value: str) -> str:
    value = (value or "").strip()
    if len(value) > MAX_GREETING_CHARS:
        raise ConfigError("greeting_too_long", f"Greeting is limited to {MAX_GREETING_CHARS} characters")
    return value


def load() -> TwilioConfig:
    from services import settings_store

    def text(key: str) -> str:
        return settings_store.get_text(_PREFIX + key, "") or ""

    return TwilioConfig(
        enabled=text("enabled") == "1",
        account_sid=text("account_sid"),
        public_base_url=text("public_base_url"),
        voice_id=text("voice_id"),
        engine=text("engine"),
        language=text("language"),
        greeting=text("greeting"),
    )


def auth_token() -> str:
    from services import settings_store

    return settings_store.get_secret(SECRET_NAME) or ""


def has_auth_token() -> bool:
    from services import settings_store

    return SECRET_NAME in settings_store.list_secret_names()


def save(cfg: TwilioConfig) -> None:
    from services import settings_store

    for key in ("account_sid", "public_base_url", "voice_id", "engine", "language", "greeting"):
        settings_store.set_text(_PREFIX + key, getattr(cfg, key))
    settings_store.set_text(_PREFIX + "enabled", "1" if cfg.enabled else "0")


def set_auth_token(value: str) -> None:
    """'' clears the stored token."""
    from services import settings_store

    settings_store.set_secret(SECRET_NAME, value.strip())


def missing_for_enable(cfg: TwilioConfig, token_present: bool) -> list[str]:
    """Fields that must be set before calls can be answered (UI codes)."""
    missing = []
    if not cfg.account_sid:
        missing.append("account_sid")
    if not token_present:
        missing.append("auth_token")
    if not cfg.public_base_url:
        missing.append("public_base_url")
    if not cfg.greeting:
        missing.append("greeting")
    return missing


def _int_env(name: str, default: int, lo: int, hi: int) -> int:
    try:
        return max(lo, min(hi, int(os.environ.get(name, default))))
    except (TypeError, ValueError):
        return default


def max_concurrent_calls() -> int:
    return _int_env("OMNIVOICE_TWILIO_MAX_CALLS", 2, 1, 16)


def max_call_seconds() -> int:
    return _int_env("OMNIVOICE_TWILIO_MAX_CALL_SECONDS", 300, 30, 3600)


def webhooks_per_minute() -> int:
    return _int_env("OMNIVOICE_TWILIO_WEBHOOKS_PER_MINUTE", 30, 1, 600)
