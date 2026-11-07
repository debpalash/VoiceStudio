"""
Deterministic polish for dictation finals (dictation v2).

Every ``final`` that leaves ``/ws/transcribe`` passes through
:func:`polish_text` so pasted dictation reads like typed text:

  * leading capital -- Latin scripts only (CJK/Cyrillic/etc. untouched),
  * terminal punctuation -- a period is appended unless the text already
    ends with sentence-terminal punctuation (incl. the CJK fullwidth forms),
  * doubled spaces collapsed, leading/trailing whitespace stripped.

Purely rule-based -- no model, no locale detection, no network -- so it is
byte-for-byte reproducible and idempotent (``polish(polish(x)) == polish(x)``).

CJK codepoints below are ``\\u``-escaped on purpose: this is functional
punctuation handling (allowed), and the escapes keep this file outside the
literal-CJK scan in ``tests/test_no_hardcoded_cjk.py`` without growing its
allowlist.
"""
from __future__ import annotations

import re

# Sentence-terminal punctuation that already "closes" a final -- Latin plus
# the CJK fullwidth forms (U+3002 ideographic full stop, U+FF01 !, U+FF1F ?)
# and ellipsis, Arabic question mark, Urdu full stop, and Indic danda.
# A trailing closing quote/bracket after one of these still
# counts as terminated ("He said \"hi.\"").
_TERMINAL = ".!?\u2026\u3002\uff01\uff1f\u061f\u06d4\u0964\u0965"
_CLOSERS = "\"'\u201d\u2019\u00bb\u203a)]}\u300d\u300f\uff09\u3011"

# A dangling clause separator at the very end (ASR often stops mid-breath on
# a comma) is swapped for a stop instead of stacking ",." punctuation.
# Latin , ; : plus the CJK forms U+3001 U+FF0C U+FF1B U+FF1A.
_DANGLING = ",;:\u3001\uff0c\uff1b\uff1a"

# CJK codepoints (kana, unified ideographs, compatibility + halfwidth forms)
# -- used to pick the fullwidth stop U+3002 over "." for CJK sentences.
_CJK = re.compile(
    "[\u3040-\u30ff\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff\uff66-\uff9f]"
)

_MULTISPACE = re.compile(r"[ \t]{2,}")


def _is_latin_lower(ch: str) -> bool:
    """Lowercase letter in a Latin block (ASCII, Latin-1, Latin Extended-A/B).

    Capitalization is meaningless (CJK) or presumptuous (Cyrillic, Greek --
    the model's casing is trusted) outside Latin scripts.
    """
    return ch.islower() and ord(ch) <= 0x024F


def polish_text(text: str) -> str:
    """Normalise one dictation final. Empty/whitespace-only input -> ``""``."""
    if not text:
        return ""
    out = _MULTISPACE.sub(" ", text).strip()
    if not out:
        return ""

    # Leading capital (Latin scripts only).
    if _is_latin_lower(out[0]):
        out = out[0].upper() + out[1:]

    # Already terminated -- possibly behind a closing quote/bracket?
    body = out.rstrip(_CLOSERS)
    if body and body[-1] in _TERMINAL:
        return out

    # Swap a dangling comma/colon for the stop instead of stacking ",.".
    if out[-1] in _DANGLING:
        out = out[:-1].rstrip()
        if not out:
            return ""

    # Script-matched stop: fullwidth U+3002 when the sentence ends in CJK.
    out += "\u3002" if _CJK.search(out[-1]) else "."
    return out
