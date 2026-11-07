"""RFC 6266 ``Content-Disposition`` construction.

#1262: exporting a voice profile whose name isn't spelled in Latin letters
returned a 500:

    'latin-1' codec can't encode characters in position 22-25:
    ordinal not in range(256)

``attachment; filename="`` is exactly 22 characters, so positions 22-25 were
the first four characters of the user's own profile name. HTTP header values
are latin-1 by definition, and every download endpoint built the header by
f-string interpolation, so any name outside latin-1 — Chinese, Japanese,
Korean, Greek, Cyrillic, Hebrew, emoji — crashed the request.

The sanitisers in front of those f-strings did not catch it because they all
filtered with ``str.isalnum()``, which is **True for every alphabetic script**,
not just ASCII. ``"我的声音".isalnum()`` is ``True``. They were removing
punctuation and passing the exact characters that break the header.

`content_disposition` is the one construction site: an ASCII-safe
``filename=`` that any client can read, plus the RFC 5987 ``filename*=`` that
gives modern browsers the user's real name back, correctly encoded.
"""
from __future__ import annotations

import re
import unicodedata
from urllib.parse import quote

__all__ = ["ascii_filename", "content_disposition"]

#: Characters Windows forbids in a filename, plus the quoting/injection risks
#: (`"` and `\` end the quoted-string; CR/LF would split the header).
_UNSAFE = re.compile(r'[\\/:*?"<>|\x00-\x1f\x7f]')


def _fold(text: str) -> str:
    """One filename part, reduced to safe ASCII."""
    folded = unicodedata.normalize("NFKD", text)
    # Drop the combining marks NFKD split off, keeping the base letters.
    folded = "".join(c for c in folded if not unicodedata.combining(c))
    folded = folded.encode("ascii", "ignore").decode("ascii")
    return _UNSAFE.sub("_", folded).strip()


def ascii_filename(filename: str, fallback: str = "download") -> str:
    """A latin-1-safe rendering of *filename* for the legacy ``filename=``.

    Accented Latin is folded to its base letters (``Sébastien`` →
    ``Sebastien``) rather than deleted, since that stays readable. Scripts with
    no ASCII form (CJK, Cyrillic, Hebrew, emoji) have no meaningful fold, so
    they drop out and *fallback* carries the name — the ``filename*`` parameter
    is what actually preserves those, and every browser released this decade
    prefers it.
    """
    raw = filename or ""
    # Split the extension off FIRST: folding runs per-part so a name that is
    # entirely non-ASCII loses its stem without also losing ".ovsvoice", which
    # is what tells the OS (and the user) what the file actually is.
    stem, dot, suffix = raw.rpartition(".")
    if not dot:
        stem, suffix = raw, ""

    stem, suffix = _fold(stem), _fold(suffix)
    if not stem.strip("_ ."):
        stem = fallback
    return f"{stem}.{suffix}" if suffix else stem


def content_disposition(
    filename: str,
    *,
    disposition: str = "attachment",
    fallback: str = "download",
) -> str:
    """A ``Content-Disposition`` value that is safe for ANY filename (#1262).

    Emits both forms per RFC 6266 §4.3: ``filename=`` for the lowest common
    denominator and ``filename*=UTF-8''…`` for the real name. Clients that
    understand the extended form ignore the plain one, so the user gets
    ``我的声音.ovsvoice`` while nothing anywhere has to encode it as latin-1.
    """
    # The fallback is a caller-supplied string that lands in the header
    # verbatim whenever the real name folds away entirely, so it gets the same
    # treatment as the name itself — otherwise a non-ASCII or quote/CRLF
    # fallback walks straight past every guard here (#1262 review).
    safe_fallback = _fold(fallback) or "download"
    safe = ascii_filename(filename, fallback=safe_fallback)
    encoded = quote(_UNSAFE.sub("_", filename or safe_fallback), safe="")
    return f"{disposition}; filename=\"{safe}\"; filename*=UTF-8''{encoded}"
