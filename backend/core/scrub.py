"""Privacy scrubber for diagnostic text that may leave the machine.

Everything VoiceStudio renders into a bug report or diagnostic dump goes
through ``scrub_text()`` before it can reach a prefilled GitHub Issues URL
(the only outbound path — see CLAUDE.md Capability 2). The scrubber is the
backend twin of ``electron/src/shared/utils/scrub.js``'s ``scrubText`` and
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

import json
import os
import re
from urllib.parse import unquote_plus

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
# are already caught above and named env vars by the sweep below. `pin` is the
# share PIN: the backend accepts it as `?pin=` and it authenticates paired
# clients, so a crash-logged request URL must not carry it into a bundle.
_URL_SECRET_NAMES = frozenset((
    "access_token", "access-token", "accesstoken", "api_key", "api-key", "apikey",
    "auth_token", "auth-token", "authtoken", "token", "secret", "password", "passwd", "pwd", "pin",
))
_URL_SECRET_NAME = "(?:" + "|".join(sorted(_URL_SECRET_NAMES)) + ")"
_URL_SECRET_RE = re.compile(
    r"(?<![\w%+.-])(" + _URL_SECRET_NAME + r"=)"
    r"([^&\s\"'#]{6,})",
    re.IGNORECASE,
)
# A known ASCII name uses at most three source characters per encoded byte.
# Bounding name probes by this finite vocabulary prevents overlapping suffix
# scans on malformed query text; matched values are consumed only once.
_MAX_QUERY_NAME_CHARS = 3 * max(map(len, _URL_SECRET_NAMES))


def _raw_quote_end(text: str, start: int, limit: int | None = None) -> int:
    quote = text[start]
    end = start + 1
    limit = len(text) if limit is None else limit
    while end < limit:
        char = text[end]
        end += 1
        if char == "\\" and end < limit:
            end += 1
        elif char == quote:
            break
    return end


def _quoted_url_end(text: str, position: int, cache: dict) -> int | None:
    # Validate each structured log line once. A real JSON string has an exact
    # end, unlike punctuation embedded in an opaque HTTP credential.
    if position >= cache.get('line_end', -1):
        start = text.rfind('\n', 0, position) + 1
        end = text.find('\n', position)
        cache['line_start'], cache['line_end'] = start, len(text) if end < 0 else end
        try:
            json.loads(text[start:cache['line_end']])
            cache['json'] = True
        except (ValueError, RecursionError):
            cache['json'] = False
    slash_start = position
    while slash_start > cache['line_start'] and text[slash_start - 1] == '\\':
        slash_start -= 1
    if text[position] == '"' and cache['json'] and (position - slash_start) % 2 == 0:
        return _raw_quote_end(text, position, cache['line_end']) - 1

    # Other log literals must end their whole URI token. Cache one scan per
    # token, including its final quotes and query continuation, rather than
    # repeatedly testing every bracket/quote suffix in attacker-controlled text.
    if position >= cache.get('token_end', -1):
        end = position + 1
        quotes = {"'": -1, '"': -1}
        last_query = -1
        while end < len(text) and not text[end].isspace():
            if text[end] in quotes:
                quotes[text[end]] = end
            if text[end] in '?&':
                last_query = end
            end += 1
        bounds = {}
        for quote, candidate in quotes.items():
            if candidate < 0 or candidate < last_query:
                bounds[quote] = None
                continue
            tail = candidate + 1
            while tail < end and text[tail] in '}]':
                tail += 1
            if tail < end and text[tail] == ',':
                tail += 1
            bounds[quote] = candidate if candidate >= last_query and tail == end else None
        cache['token_end'], cache['bounds'] = end, bounds
    bound = cache['bounds'][text[position]]
    return bound if bound is not None and bound > position else None


def _scrub_url_queries(text: str) -> str:
    parts = []
    copied = position = 0
    length = len(text)
    url_end = None
    quote_cache = {}
    while position < length:
        char = text[position]
        if url_end is not None and position >= url_end:
            url_end = None
        if url_end is None and char in "\"'" and text.startswith(("http://", "https://"), position + 1):
            url_end = _quoted_url_end(text, position, quote_cache)
        if text[position] not in "?&":
            position += 1
            continue
        name_start = position + 1
        name_end = name_start
        limit = min(length, name_start + _MAX_QUERY_NAME_CHARS)
        while name_end < limit and text[name_end] not in "=?&#\"'" and not text[name_end].isspace():
            name_end += 1
        if (name_end == length or text[name_end] != "="
                or unquote_plus(text[name_start:name_end]).lower() not in _URL_SECRET_NAMES):
            position += 1
            continue
        value_start = name_end + 1
        value_limit = url_end if url_end is not None else length
        end = value_start
        quote_start = end
        while quote_start < value_limit and text[quote_start] == "\\":
            quote_start += 1
        if quote_start > end and quote_start < value_limit and text[quote_start] in "\"'":
            # JSON/log repr can escape the enclosing quotes. Match that
            # same delimiter depth; more deeply escaped quotes are content.
            escape_depth = quote_start - end
            quote = text[quote_start]
            end = quote_start + 1
            while end < value_limit:
                slash_start = end
                while end < value_limit and text[end] == "\\":
                    end += 1
                closes = end - slash_start == escape_depth and end < value_limit and text[end] == quote
                if end < value_limit:
                    end += 1
                if closes:
                    break
        elif end < value_limit and text[end] in "\"'":
            end = _raw_quote_end(text, end, value_limit)
        # Quoted fragments may be adjacent parts of the same HTTP credential.
        while end < value_limit and text[end] not in "&#" and not text[end].isspace():
            if text[end] in "\"'":
                after = end + 1
                # Preserve terminal log quotes; otherwise consume the entire
                # fragment, failing closed if its closing quote is missing.
                if after == length or text[after].isspace() or text[after] in "&#":
                    break
                end = _raw_quote_end(text, end, value_limit)
            else:
                end += 1
        parts.extend((text[copied:value_start], REDACTED))
        copied = position = end
    parts.append(text[copied:])
    return "".join(parts)

# Home-directory shapes for all three supported platforms. Matched
# pattern-wise (not just this machine's $HOME) so paths quoted from a
# user's pasted log on another OS get cleaned too.
# IGNORECASE because Windows is case-insensitive and tools routinely emit the
# lowercase `c:\users\<name>` form, which the CLAUDE.md redaction spec still
# requires to become `~`. `Users`/`users`, `Home`/`home` all match.
# A home-directory name. It may contain spaces (`C:\Users\Mary Jane Smith`), but
# only when a separator or a closing quote follows (`'C:\\Users\\Mary Jane'` in an
# OSError), so the match cannot run into the sentence after a path:
# `/home/bob: Permission denied` keeps its words and redacts `/home/bob`.
_HOME_NAME = r"(?:[^\\/\s\"':]+(?:\s[^\\/\s\"':]+)*(?=[\\/\"'])|[^\\/\s\"':]+)"

_HOME_PATTERNS = (
    # repr() and JSON escape the separators, so an OSError or a log line can carry
    # `C:\\Users\\<name>`. Those doubled forms must be matched before the single
    # one, which does not match them anyway: the extra backslash breaks its shape.
    re.compile(r"[A-Za-z]:\\\\Users\\\\" + _HOME_NAME, re.IGNORECASE),  # repr-escaped
    # Windows-with-forward-slashes must run BEFORE the bare macOS shape, or
    # `/Users/<name>` inside `C:/Users/<name>` gets eaten first, leaving `C:~`.
    re.compile(r"[A-Za-z]:/Users/" + _HOME_NAME, re.IGNORECASE),   # Windows, forward slashes
    re.compile(r"/Users/" + _HOME_NAME, re.IGNORECASE),           # macOS
    re.compile(r"/home/" + _HOME_NAME, re.IGNORECASE),            # Linux
    re.compile(r"[A-Za-z]:\\Users\\" + _HOME_NAME, re.IGNORECASE),  # Windows, backslashes
)

# Lookahead that ends this machine's home path: the next character must not
# continue the directory name. A dot only continues it when a name character
# follows (`/Users/john.doe`); a trailing dot is sentence punctuation.
_HOME_NAME_ENDS = r"(?![\w\-]|\.[\w\-])"

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
        # Preserve quote delimiters for the scanner before free-text matching
        # can replace a long escaped prefix while leaving its value visible.
        s = _scrub_url_queries(s)
        s = _URL_SECRET_RE.sub(lambda m: m.group(1) + REDACTED, s)
    except Exception:
        return REDACTED

    # 3. This process's real home dir (covers symlinked/nonstandard homes
    #    the generic patterns miss), then the per-OS shapes. Boundary-aware so
    #    a home of `/Users/john` doesn't rewrite `/Users/johnny` to `~ny`
    #    (leaking the fragment + mangling the path). Only a character that
    #    continues the name blocks the match, so sentence punctuation after
    #    the home (`.../alice.` `.../alice,` `(.../alice)`) is still redacted.
    try:
        home = os.path.expanduser("~")
        if home and home not in ("/", "~"):
            s = re.sub(re.escape(home) + _HOME_NAME_ENDS, "~", s)
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
