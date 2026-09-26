"""Privacy scrubber for diagnostic text that may leave the machine.

Everything VoiceStudio renders into a bug report or diagnostic dump goes
through ``scrub_text()`` before it can reach a prefilled GitHub Issues URL
(the only outbound path — see CLAUDE.md Capability 2). The scrubber is the
backend twin of ``electron/src/shared/utils/bugReport.js``'s ``scrubText`` and
must stay at least as strict:

  - home directories → ``~`` (macOS ``/Users/<name>``, Linux ``/home/<name>``,
    Windows ``C:\\Users\\<name>``, plus the *actual* ``$HOME`` of this process)
  - credential-shaped substrings → ``***REDACTED***`` (HF tokens, GitHub
    PATs, OpenAI-style ``sk-`` keys)
  - values of env vars whose NAME matches ``*TOKEN*|*KEY*|*SECRET*|
    *PASSWORD*|*CREDENTIAL*`` — so a stack trace that interpolated a real
    secret still comes out clean

Unlike ``core.logging_filter`` (which rewrites log records in-flight and
must stay cheap), this module runs on report-sized strings at report time,
so it can afford the env-var sweep.
"""
from __future__ import annotations

import os
import re

REDACTED = "***REDACTED***"

# Env-var NAMES whose values must never appear in scrubbed output.
_SECRET_NAME_RE = re.compile(r"TOKEN|KEY|SECRET|PASSWORD|CREDENTIAL", re.IGNORECASE)

# Credential-shaped substrings, independent of where they came from.
# Thresholds mirror core.logging_filter: long enough that identifiers like
# `hf_hub` or `sk-learn` survive, short enough that real tokens never do.
_TOKEN_PATTERNS = (
    re.compile(r"hf_[A-Za-z0-9]{30,}"),            # HuggingFace
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),   # GitHub fine-grained PAT
    re.compile(r"gh[pousr]_[A-Za-z0-9]{30,}"),     # GitHub classic tokens
    re.compile(r"sk-[A-Za-z0-9_\-]{20,}"),         # OpenAI-style API keys
    # A backend error can carry a secret from *any* provider (the LLM-providers
    # feature ships a dozen), so match the common credential shapes too, not
    # just the four vendors above — a leaked key in a public issue is real harm.
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{6,}"),  # JWT (Bearer)
    re.compile(r"AIza[0-9A-Za-z_\-]{35}"),         # Google API key
    re.compile(r"xox[baprs]-[A-Za-z0-9\-]{10,}"),  # Slack token
    re.compile(r"AKIA[0-9A-Z]{16}"),               # AWS access key id
    re.compile(r"(?i)bearer\s+[A-Za-z0-9._\-]{16,}"),  # opaque bearer tokens
)

# Secrets carried in a URL query string (`?token=…`, `&api_key=…`). Redact the
# VALUE while keeping the param name + separator so the URL stays legible. Bare
# `key=` is intentionally excluded — too common in non-secret text; shaped keys
# are already caught above and named env vars by the sweep below.
_URL_SECRET_RE = re.compile(
    r"((?:access[_-]?token|api[_-]?key|apikey|auth[_-]?token|token|secret|password|passwd|pwd)=)"
    r"([^&\s\"'#]{6,})",
    re.IGNORECASE,
)

# Home-directory shapes for all three supported platforms. Matched
# pattern-wise (not just this machine's $HOME) so paths quoted from a
# user's pasted log on another OS get cleaned too.
# IGNORECASE because Windows is case-insensitive and tools routinely emit the
# lowercase `c:\users\<name>` form, which the CLAUDE.md redaction spec still
# requires to become `~`. `Users`/`users`, `Home`/`home` all match.
_HOME_PATTERNS = (
    # Windows-with-forward-slashes must run BEFORE the bare macOS shape, or
    # `/Users/<name>` inside `C:/Users/<name>` gets eaten first, leaving `C:~`.
    re.compile(r"[A-Za-z]:/Users/[^/\s\"']+", re.IGNORECASE),   # Windows, forward slashes
    re.compile(r"/Users/[^/\s\"']+", re.IGNORECASE),           # macOS
    re.compile(r"/home/[^/\s\"']+", re.IGNORECASE),            # Linux
    re.compile(r"[A-Za-z]:\\Users\\[^\\\s\"']+", re.IGNORECASE),  # Windows, backslashes
)

# Values shorter than this are too entropy-poor to be real secrets and too
# likely to shred unrelated text (e.g. PASSWORD_MIN_LENGTH=8 would otherwise
# turn every "8" in the report into ***REDACTED***).
_MIN_SECRET_LEN = 8


def _env_secret_values() -> list[str]:
    """Values of secret-named env vars, longest first so overlapping
    values (e.g. a token and its prefix) redact cleanly."""
    vals = [
        v
        for k, v in os.environ.items()
        if _SECRET_NAME_RE.search(k) and v and len(v) >= _MIN_SECRET_LEN
    ]
    return sorted(vals, key=len, reverse=True)


def scrub_text(text: str | None) -> str:
    """Return ``text`` with secrets and home paths redacted.

    Never raises. If a redaction pass itself fails, return only the redaction
    marker: diagnostic detail is less important than keeping credentials and
    private paths out of a report.
    """
    if not text:
        return "" if text is None else str(text)
    s = str(text)

    # 1. Exact env-var secret values (most specific — run first).
    try:
        for val in _env_secret_values():
            s = s.replace(val, REDACTED)
    except Exception:
        return REDACTED

    # 2. Credential-shaped substrings + URL query secrets.
    for pat in _TOKEN_PATTERNS:
        try:
            s = pat.sub(REDACTED, s)
        except Exception:
            return REDACTED
    try:
        s = _URL_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, s)
    except Exception:
        return REDACTED

    # 3. This process's real home dir (covers symlinked/nonstandard homes
    #    the generic patterns miss), then the per-OS shapes. Boundary-aware so
    #    a home of `/Users/john` doesn't rewrite `/Users/johnny` to `~ny`
    #    (leaking the fragment + mangling the path).
    try:
        home = os.path.expanduser("~")
        if home and home not in ("/", "~"):
            s = re.sub(re.escape(home) + r"(?=[/\\\s\"']|$)", "~", s)
    except Exception:
        return REDACTED
    for pat in _HOME_PATTERNS:
        try:
            s = pat.sub("~", s)
        except Exception:
            return REDACTED

    return s


def scrub_provider_error(detail: object, api_key: str | None = None) -> str:
    """UI-safe text for an LLM/translation provider failure.

    Some OpenAI-compatible providers echo the caller's key or a stable
    ``user_id`` back inside their error bodies, and a raw ``str(exc)`` on the
    translate / glossary paths would surface that verbatim. This redacts the
    exact resolved ``api_key`` first (in the provider-registry case it isn't a
    shaped/known-env secret, so ``scrub_text`` alone can miss it) then runs the
    generic secret + home-path scrub. Never raises — scrubbing must not mask a
    failure with a new one. Mirrors ``settings._scrub_llm_detail`` so every
    surface redacts identically.
    """
    s = str(detail if detail is not None else "")
    try:
        if api_key and api_key != "local" and len(api_key) >= _MIN_SECRET_LEN:
            s = s.replace(api_key, REDACTED)
    except Exception:
        return REDACTED
    return scrub_text(s)
