"""Persisted Twilio configuration (local settings store only).

Non-secret fields are plain ``settings`` rows; the Auth Token is Fernet-
encrypted via :func:`services.settings_store.set_secret`, never returned by
any API, never logged, and never part of an export. Everything defaults OFF.
"""
from __future__ import annotations

import os
import re
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


def missing_for_enable(cfg: TwilioConfig, token_present: bool, inbound_mode: str | None = None) -> list[str]:
    """Fields that must be set before the listener may run (UI codes).

    The greeting is only required while incoming calls are answered with it;
    in agent mode the call agent answers instead.
    """
    if inbound_mode is None:
        inbound_mode = load_call_settings().inbound_mode
    missing = []
    if not cfg.account_sid:
        missing.append("account_sid")
    if not token_present:
        missing.append("auth_token")
    if not cfg.public_base_url:
        missing.append("public_base_url")
    if not cfg.greeting and inbound_mode == "greeting":
        missing.append("greeting")
    return missing


# ── Call agent settings (docs/integrations/calls.md) ────────────────────────

_CALLS_PREFIX = "integrations.twilio.calls."
MAX_DISCLOSURE_CHARS = 500
MAX_BRIEF_CHARS = 4000
MAX_NAME_CHARS = 80
DEFAULT_DISCLOSURE = "Hi, this is {name}'s AI assistant calling on their behalf."
INBOUND_MODES = ("greeting", "agent")
#: E.164: "+", a non-zero country-code digit, up to 15 digits in total.
E164_RE = re.compile(r"^\+[1-9]\d{6,14}$")
_NUMBER_PUNCTUATION_RE = re.compile(r"[\s().\-]")


@dataclass(frozen=True)
class CallSettings:
    from_number: str = ""
    #: ``{name}`` is replaced with ``user_name``. May be empty (no disclosure):
    #: that is the user's decision and responsibility (see the docs).
    disclosure_template: str = DEFAULT_DISCLOSURE
    user_name: str = ""
    inbound_mode: str = "greeting"
    inbound_brief: str = ""
    max_concurrent: int = 1
    record_calls: bool = False

    def as_dict(self) -> dict:
        return {
            "from_number": self.from_number,
            "disclosure_template": self.disclosure_template,
            "user_name": self.user_name,
            "inbound_mode": self.inbound_mode,
            "inbound_brief": self.inbound_brief,
            "max_concurrent": self.max_concurrent,
            "record_calls": self.record_calls,
        }


def normalize_phone_number(value: str, *, code: str = "invalid_number") -> str:
    """``+1 (415) 555-0123`` → ``+14155550123``; anything not E.164 is refused."""
    value = _NUMBER_PUNCTUATION_RE.sub("", (value or "").strip())
    if value.startswith("00"):
        value = "+" + value[2:]
    if not E164_RE.match(value):
        raise ConfigError(
            code,
            "Enter the number in international format with its country code, such as +14155550123",
        )
    return value


def normalize_disclosure(value: str) -> str:
    value = (value or "").strip()
    if len(value) > MAX_DISCLOSURE_CHARS:
        raise ConfigError("disclosure_too_long", f"The disclosure is limited to {MAX_DISCLOSURE_CHARS} characters")
    return value


def normalize_brief(value: str, *, required: bool = True) -> str:
    value = (value or "").strip()
    if required and not value:
        raise ConfigError("missing_brief", "Describe what the call should achieve")
    if len(value) > MAX_BRIEF_CHARS:
        raise ConfigError("brief_too_long", f"The brief is limited to {MAX_BRIEF_CHARS} characters")
    return value


def render_disclosure(template: str, user_name: str) -> str:
    """Fill ``{name}`` (``someone`` when no name is known)."""
    template = (template or "").strip()
    return template.replace("{name}", (user_name or "").strip() or "someone") if template else ""


def load_call_settings() -> CallSettings:
    from services import settings_store

    def text(key: str, default: str = "") -> str:
        value = settings_store.get_text(_CALLS_PREFIX + key, None)
        return default if value is None else value

    mode = text("inbound_mode", "greeting")
    try:
        concurrent = max(1, min(2, int(text("max_concurrent", "1") or 1)))
    except ValueError:
        concurrent = 1
    return CallSettings(
        from_number=text("from_number"),
        disclosure_template=text("disclosure_template", DEFAULT_DISCLOSURE),
        user_name=text("user_name"),
        inbound_mode=mode if mode in INBOUND_MODES else "greeting",
        inbound_brief=text("inbound_brief"),
        max_concurrent=concurrent,
        record_calls=text("record_calls") == "1",
    )


def save_call_settings(cfg: CallSettings) -> None:
    from services import settings_store

    for key in ("from_number", "disclosure_template", "user_name", "inbound_mode", "inbound_brief"):
        settings_store.set_text(_CALLS_PREFIX + key, getattr(cfg, key))
    settings_store.set_text(_CALLS_PREFIX + "max_concurrent", str(cfg.max_concurrent))
    settings_store.set_text(_CALLS_PREFIX + "record_calls", "1" if cfg.record_calls else "0")


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
