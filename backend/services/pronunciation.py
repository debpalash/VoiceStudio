"""Pronunciation lexicon — per-project word respelling (longform render, PR 8).

A pronunciation lexicon maps a word (or short phrase) to a respelling the TTS
engine pronounces correctly: ``{"OmniVoice": "Omni Voice", "Dr": "Doctor",
"GIF": "jiff"}``. The narration pipeline applies it to each span's text just
before chunking, so the engine never sees the hard-to-say original.

This module is the engine-agnostic, pure core:

  * ``apply_lexicon(text, lexicon)`` — whole-word, case-insensitive replacement
    of every key with its respelling. Word-boundary aware (``\\b``), so a key
    ``cat`` never touches ``category``; surrounding punctuation/whitespace is
    preserved (``"smith,"`` → ``"Smith,"``). Longest key first, so a key
    ``Dr. Smith`` wins over ``Dr`` on overlapping input.
  * ``normalize_lexicon(lexicon)`` — drop empty/whitespace keys, coerce values.
  * ``load_lexicon(path)`` / ``save_lexicon(path, lexicon)`` — JSON round-trip.

ReDoS safety: the matcher is a single anchored-alternation regex built from
``re.escape``'d keys joined by ``|`` and wrapped in word boundaries
(``\\b(?:k1|k2|…)\\b``). No nested/overlapping quantifiers, no user-controlled
quantifier — the keys are literals, so there is no catastrophic backtracking
(CodeQL py/polynomial-redos clean). Matching is done in one ``re.sub`` pass with
a callback, so a respelling that happens to contain another key is never
re-scanned (idempotent against its own output).
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Optional

# A "word" character for boundary purposes. We treat the standard regex word
# class (``\w`` = ``[A-Za-z0-9_]`` plus Unicode letters under ``re.UNICODE``,
# the default for ``str`` patterns). A key only gets ``\b`` boundaries on a side
# that actually abuts a word char, so a key like ``Dr.`` (ends in a non-word
# char) still matches when followed by a space.


def normalize_lexicon(lexicon: Optional[dict]) -> dict[str, str]:
    """Return a clean ``{key: respelling}`` dict.

    Drops entries whose key is empty or whitespace-only; coerces keys/values to
    stripped strings. A value may be empty (``""``) — that deletes the word
    (valid: e.g. stripping a stray marker). ``None``/non-dict input → ``{}``.
    """
    if not isinstance(lexicon, dict):
        return {}
    out: dict[str, str] = {}
    for k, v in lexicon.items():
        if k is None:
            continue
        key = str(k).strip()
        if not key:
            continue
        out[key] = "" if v is None else str(v)
    return out


def _boundary_prefix(key: str) -> str:
    """``\\b`` only if the key starts with a word char (else the boundary would
    never match — e.g. a key opening with punctuation)."""
    return r"\b" if key[:1].isalnum() or key[:1] == "_" else ""


def _boundary_suffix(key: str) -> str:
    """``\\b`` only if the key ends with a word char."""
    return r"\b" if key[-1:].isalnum() or key[-1:] == "_" else ""


def _compile(lexicon: dict[str, str]) -> tuple[Optional[re.Pattern], dict[str, str]]:
    """Build the single alternation regex + a casefold→respelling lookup.

    Keys are sorted longest-first so an overlapping longer key (``Dr. Smith``)
    is tried before a shorter one (``Dr``). Each alternative carries its own
    word-boundary guards based on its own edge characters, which keeps a
    punctuation-edged key (``Dr.``) matchable while still protecting a
    letter-edged key (``cat``) from partial hits inside ``category``.
    """
    keys = sorted(lexicon.keys(), key=len, reverse=True)
    if not keys:
        return None, {}
    # casefold (not lower) for robust Unicode case-insensitive lookup.
    lookup = {k.casefold(): lexicon[k] for k in keys}
    alts = [f"{_boundary_prefix(k)}{re.escape(k)}{_boundary_suffix(k)}" for k in keys]
    # No capturing groups, no nested quantifiers — pure literal alternation.
    pattern = re.compile("(?:" + "|".join(alts) + ")", re.IGNORECASE)
    return pattern, lookup


def apply_lexicon(text: str, lexicon: Optional[dict]) -> str:
    """Replace whole-word occurrences of each lexicon key with its respelling.

    Case-insensitive match; word-boundary aware (a key never matches inside a
    longer word); longest key wins on overlap; surrounding punctuation and
    whitespace are untouched. A single left-to-right ``re.sub`` pass means a
    respelling is never itself rescanned, so applying twice is idempotent when
    no key is a substring of another key's output.

    Returns ``text`` unchanged when ``text`` is falsy or the lexicon is empty.
    """
    if not text:
        return text or ""
    clean = normalize_lexicon(lexicon)
    pattern, lookup = _compile(clean)
    if pattern is None:
        return text

    def _repl(m: re.Match) -> str:
        return lookup.get(m.group(0).casefold(), m.group(0))

    return pattern.sub(_repl, text)


# ── JSON persistence ─────────────────────────────────────────────────────────

def load_lexicon(path) -> dict[str, str]:
    """Load + normalize a lexicon from a JSON file.

    A missing file, empty file, or non-object JSON yields ``{}`` rather than
    raising — a project simply has no lexicon yet. Malformed JSON still raises
    (caller's choice to surface it).
    """
    p = Path(path)
    if not p.is_file():
        return {}
    raw = p.read_text(encoding="utf-8").strip()
    if not raw:
        return {}
    data = json.loads(raw)
    return normalize_lexicon(data)


def save_lexicon(path, lexicon: Optional[dict]) -> dict[str, str]:
    """Normalize + write a lexicon to ``path`` as pretty JSON (utf-8).

    Returns the normalized dict that was written. Parent dirs are created.
    """
    clean = normalize_lexicon(lexicon)
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps(clean, indent=2, ensure_ascii=False, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return clean


# ── DB-backed global / per-language dictionary (Expressive-TTS Spec 01) ───────
#
# The JSON ``load_lexicon``/``save_lexicon`` above stay the per-project audiobook
# override. THIS layer is the user-editable, DB-persisted, per-language default
# dictionary surfaced in Settings → Pronunciation. Rows scoped ``language="*"``
# apply to every request; a 2-letter language row applies only when the request
# language's prefix matches (case-insensitive), so a German entry never fires on
# an English render. Both layers are pure text substitution — they ride the same
# ReDoS-safe ``apply_lexicon`` matcher, so every engine honors them.

_ALL_LANG = "*"


def _lang_prefix(language: Optional[str]) -> Optional[str]:
    """Normalize a request language to a lowercase 2-letter prefix.

    ``"Auto"``/``None``/``""`` → ``None`` (means "no language pin": only global
    ``*`` rows apply, language-tagged rows are skipped, mirroring how the engines
    treat an unset language). A value like ``"en-US"`` / ``"English"`` →
    ``"en"`` (first two letters); matching against entries is on this prefix.
    """
    if not language:
        return None
    s = str(language).strip().lower()
    if not s or s == "auto":
        return None
    return s[:2]


def entries_for_language(
    entries, language: Optional[str], *, include_skipped: bool = False
):
    """Collapse DB rows into a ``{term: replacement}`` map for ``apply_lexicon``.

    Filters to ``enabled`` rows whose scope is global (``*``) OR whose language
    prefix matches the request language. Only the **respelling** path produces a
    plain substitution here (Phase 1); IPA/CMU rows that carry no respelling are
    skipped at this layer (they're handled — or honestly degraded — by the
    engine-markup path, never silently mangling text). A language-specific row
    overrides a global row with the same (case-folded) term, so a per-language
    pronunciation can refine the global default.

    ``entries`` is any iterable of mappings/rows with ``term``, ``replacement``,
    ``type``, ``language``, ``enabled`` keys (a ``sqlite3.Row`` works directly).

    If ``include_skipped`` is True, also returns a list of in-scope entries that
    were dropped because their type isn't ``respelling`` yet (Phase 1 has no
    substitution for them) — used by ``/pronunciation/test`` to report a
    matching-but-inert entry instead of silently reporting no match at all.
    """
    req_prefix = _lang_prefix(language)
    # Two passes so language rows win over global rows on the same term: collect
    # global first, then overlay matching-language rows.
    glob: dict[str, str] = {}
    lang: dict[str, str] = {}
    skipped: list[dict] = []
    for e in entries:
        try:
            if not int(e["enabled"]):
                continue
        except (KeyError, IndexError, TypeError, ValueError):
            continue
        term = (e["term"] or "").strip()
        if not term:
            continue
        etype = (e["type"] or "respelling").strip().lower()
        replacement = e["replacement"] if e["replacement"] is not None else ""
        scope = (e["language"] or _ALL_LANG).strip() or _ALL_LANG
        scope_matches = scope == _ALL_LANG or (
            req_prefix is not None and scope[:2].lower() == req_prefix
        )
        # Phase 1: only respelling rows substitute text. IPA/CMU rows without a
        # respelling fall through (Phase 2 lowers them to engine markup); we do
        # NOT feed a raw IPA string into the grapheme stream. If the caller wants
        # to know, record the skip instead of silently dropping it.
        if etype != "respelling":
            if include_skipped and scope_matches:
                skipped.append({"term": term, "type": etype, "language": scope})
            continue
        if scope == _ALL_LANG:
            glob[term] = str(replacement)
        elif scope_matches:
            lang[term] = str(replacement)
    merged = dict(glob)
    merged.update(lang)  # language rows override global on the same term
    if include_skipped:
        return merged, skipped
    return merged


# ── Inline one-off override:  [[term|replacement]]  /  [[replacement]] ─────────
#
# Double brackets are unambiguous against the single-bracket grammar
# (``[voice:]``/``[pause]``/SSML-lite/``[Name]``): ``_VOICE_RE`` is
# ``\[voice:([^\]\[]*)\]`` — it forbids inner brackets, so it can't span a
# ``[[…]]``; the SSML-lite / pause vocabularies are closed literal sets that
# ``[[…]]`` is not a member of. We resolve ``[[…]]`` BEFORE chunking so the
# splitter never sees it. ReDoS-safe: ``\[\[[^\]]*\]\]`` is a bounded literal
# class, no nested quantifier.
#
#   [[gif|jiff]]      → replaces the literal "gif" → "jiff" for this occurrence
#   [[Nuh-VAD-uh]]    → the bracket content itself is spoken (brackets stripped)
# Bounded inner repetition ({0,256}) keeps this strictly linear: ``[^\]]`` also
# matches ``[``, so an unbounded run of ``[`` with no closing ``]]`` would let the
# engine re-scan O(n) content from O(n) start positions (polynomial ReDoS). The
# bound caps per-position work; an inline override is a short respelling, so 256
# chars is far more than any real ``[[term|replacement]]`` needs.
_INLINE_RE = re.compile(r"\[\[([^\]]{0,256})\]\]")


def apply_inline_overrides(text: str) -> str:
    """Resolve ``[[…]]`` one-off pronunciation overrides to plain spoken text.

    ``[[term|replacement]]`` → ``replacement`` (the ``term`` half is a label for
    the author; only the replacement is spoken). ``[[replacement]]`` (no pipe) →
    ``replacement`` with the brackets stripped. Empty ``[[]]`` collapses away.
    Applied once per occurrence; nothing persists. Single ``[…]`` tags are left
    untouched (the regex requires a double bracket on both sides).
    """
    if not text or "[[" not in text:
        return text or ""

    def _repl(m: re.Match) -> str:
        inner = m.group(1)
        if "|" in inner:
            inner = inner.split("|", 1)[1]
        return inner

    return _INLINE_RE.sub(_repl, text)


def apply_pronunciation(
    text: str,
    entries=None,
    language: Optional[str] = None,
    *,
    lexicon: Optional[dict] = None,
) -> str:
    """Apply the pronunciation dictionary + inline overrides to ``text``.

    Order (load-bearing):
      1. DB dictionary rows (``entries``) filtered to ``language`` + an optional
         per-project ``lexicon`` JSON overlay (project wins on term conflict,
         matching the audiobook layering). Both go through one ``apply_lexicon``
         pass (longest-term-first, word-boundary aware, idempotent).
      2. Inline ``[[…]]`` one-off overrides resolved last, so an inline override
         always wins over any dictionary entry for that occurrence.

    A falsy ``text`` / empty dictionary / no inline markers is a pass-through, so
    legacy plain text is byte-identical.
    """
    if not text:
        return text or ""
    merged = entries_for_language(entries or [], language)
    if lexicon:
        # Project-local JSON overlays the DB defaults; project wins on conflict.
        merged.update(normalize_lexicon(lexicon))
    out = apply_lexicon(text, merged) if merged else text
    return apply_inline_overrides(out)


# ── DB load/save ──────────────────────────────────────────────────────────────

def load_entries_from_db() -> list[dict]:
    """Return every pronunciation_entries row as a list of plain dicts.

    Import-light: the DB module is imported lazily so the pure-parser path (and
    the audiobook JSON path) never pull in sqlite/config.
    """
    from core.db import db_conn

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT id, term, replacement, type, language, enabled, created_at "
            "FROM pronunciation_entries ORDER BY created_at ASC, id ASC"
        ).fetchall()
    return [dict(r) for r in rows]


def load_dict_for_request(language: Optional[str] = None) -> dict[str, str]:
    """Convenience: DB rows → ``{term: replacement}`` for a request language.

    Returns ``{}`` (a no-op for ``apply_pronunciation``) if the table is absent
    or the DB can't be opened — pronunciation is never allowed to break synth.
    """
    try:
        return entries_for_language(load_entries_from_db(), language)
    except Exception:  # noqa: BLE001 — table missing / DB locked → no-op
        return {}