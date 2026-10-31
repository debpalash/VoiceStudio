"""Engine-agnostic text normalization — a conservative pre-pass before TTS.

Raw user text trips TTS engines: digits, clock times, and title abbreviations
mispronounce; zero-width junk and pathological repeat runs cause hallucinations
and long dead air. This module cleans text *once*, at the point where each
pipeline hands text to an engine (single-shot /generate, dub segments,
longform chapters), so every engine benefits equally.

Design rules (load-bearing):

  * **Conservative.** A false negative (digits left alone) is fine; a false
    positive (mangled meaning) is not. Anything ambiguous — thousands-grouped
    numbers ("1,000"), ranges ("3-5"), version strings ("v2", "3.5.1"),
    leading-zero codes ("007"), 7+-digit IDs — is left unchanged. Roman
    numerals are out of scope entirely ("I" is a pronoun).
  * **Idempotent.** ``normalize_text(normalize_text(x)) == normalize_text(x)``:
    number/abbreviation output contains no digits or matchable tokens and the
    safety filters are fixed-point by construction, so an accidental second
    pass through a pipeline is harmless.
  * **Per-language.** Numbers go through ``num2words`` only for languages it
    supports (``_NUM2WORDS_LANGS``; the request's ``language`` is a full
    display name from electron/src/shared/languages.json or an ISO-ish code — both
    resolve via :func:`_num2words_lang`). A language num2words does not
    know may be listed in ``_NATIVE_VERBALIZERS`` and spoken by its own small
    module (Malayalam today); any other language keeps its digits.
    Clock times / ordinals / currency are English-only (their spoken form is
    language-specific); decimals only for locales whose num2words rendering
    was vetted. CJK scripts pass through the safety filters untouched — no
    CJK punctuation is stripped and no words are injected into unsegmented
    text.
  * **Markup-safe.** The single-bracket grammar (``[voice:…]``, ``[pause …]``,
    SSML-lite) and inline ``[[…]]`` pronunciation overrides are never touched:
    the language passes skip every ``[…]`` span (same shape as chunked_tts's
    ``_BRACKET_TAG_RE``), so ``[pause 300ms]`` / ``[rate 0.9]`` stay parseable.

Ordering vs. the pronunciation dictionary (audited 2026-07-10): normalization
runs **BEFORE** ``services.pronunciation.apply_pronunciation`` (and before the
audiobook ``apply_lexicon`` overlay). Rationale from the code:

  1. Dictionary respellings are the user's explicit, final say. If
     normalization ran second it would re-process them — a respelling that
     deliberately contains digits or an abbreviation must reach the engine
     verbatim.
  2. Users already write lexicon entries against display text (the lexicon
     docstring's own example is ``{"Dr": "Doctor"}``); entries keyed on
     normalized words keep firing, and the dictionary stays the override for
     anything the normalizer produced.
  3. Inline ``[[…]]`` overrides resolve last inside ``apply_pronunciation``
     (and their bracketed content is masked here), so the user retains a
     per-occurrence override over any normalizer output.

Pinned by ``tests/test_text_normalization.py`` (dictionary-order test).

Gate: default ON; the ``OMNIVOICE_TEXT_NORMALIZATION`` env var disables it,
the same contract as ``OMNIVOICE_PRONUNCIATION`` ("0"/"false"/"no"/"off").
:func:`normalize_for_tts` is the gated entry point every pipeline calls; it
never raises — normalization is never allowed to break synthesis.
"""

from __future__ import annotations

import importlib
import logging
import os
import re
import unicodedata
from functools import partial
from typing import Callable, Optional

logger = logging.getLogger("omnivoice.text_normalization")

ENV_VAR = "OMNIVOICE_TEXT_NORMALIZATION"


# ── Language resolution ───────────────────────────────────────────────────────
#
# The `language` kwarg across the app is normally a full display name from
# electron/src/shared/languages.json ("English", "German", …) — see
# resolve_kokoro_lang_code in services/tts_backend.py — but ISO-ish codes
# ("en", "pt-BR") also flow through dub/API callers. Map both to a num2words
# locale; anything unmapped keeps its digits (false negatives are fine).

_FULL_NAME_TO_CODE = {
    "english": "en",
    "malayalam": "ml",
    "urdu": "ur",
    "german": "de",
    "spanish": "es",
    "french": "fr",
    "italian": "it",
    "portuguese": "pt",
    "portuguese (brazil)": "pt",
    "portuguese (portugal)": "pt",
    "dutch": "nl",
    "russian": "ru",
    "ukrainian": "uk",
    "polish": "pl",
    "turkish": "tr",
    "czech": "cs",
    "danish": "da",
    "finnish": "fi",
    "swedish": "sv",
    "norwegian": "no",
    "norwegian bokmål": "no",
    "norwegian nynorsk": "no",
    "romanian": "ro",
    "hungarian": "hu",
    "indonesian": "id",
    "lithuanian": "lt",
    "latvian": "lv",
    "slovenian": "sl",
    "serbian": "sr",
    "hebrew": "he",
    "persian": "fa",
    "azerbaijani": "az",
    # "vietnamese" → "vi" kept for documentation, but vi is deliberately
    # absent from _NUM2WORDS_LANGS (see the note there): the membership gate
    # in _num2words_lang makes this entry inert, so Vietnamese keeps digits.
    "vietnamese": "vi",
    "kazakh": "kz",
    "standard arabic": "ar",
    # ko/ja/zh are not in _NUM2WORDS_LANGS, so these stay inert for num2words;
    # only _plain_lang_code (range speaking) resolves them.
    "korean": "ko",
    "japanese": "ja",
    "chinese": "zh",
    "mandarin chinese": "zh",
}

# ISO codes whose num2words locale name differs.
_ISO_ALIASES = {"kk": "kz"}

# Locales verified against the pinned num2words (cardinal + basic rendering).
# zh/ja/ko/th are deliberately absent: unsegmented scripts where injecting
# space-delimited words is wrong, and their engines read digits natively.
# vi is absent too (#1139): num2words' Vietnamese cardinals misuse "lẻ" for
# 2001-2099 ("hai nghìn lẻ hai mươi bốn" for 2024 — "lẻ" is only valid before
# a lone units digit) and there is no to="year" form, so years read wrong;
# the engine pronounces Vietnamese digits natively, so digits pass through —
# the same conservative rule that already excludes vi from _DECIMAL_LANGS.
_NUM2WORDS_LANGS = frozenset({
    "en", "de", "es", "fr", "it", "pt", "nl", "ru", "uk", "pl", "tr", "cs",
    "da", "fi", "sv", "no", "ro", "hu", "id", "lt", "lv", "sl", "sr", "ar",
    "he", "fa", "az", "kz",
})

# Locales whose num2words decimal rendering was vetted ("drei Komma fünf",
# "три целых пять десятых", …). tr/vi are excluded on purpose: their 0.5
# renders as "fifty" (wrong), so decimals keep their digits there.
_DECIMAL_LANGS = frozenset({
    "en", "de", "es", "fr", "it", "pt", "nl", "ru", "uk", "pl", "cs", "da",
    "no", "sv", "fi", "ro", "hu", "id",
})

# Every vetted locale except English writes decimals with a comma and groups
# thousands with a period: "1.000" is one thousand there, not 1.0.
_COMMA_DECIMAL_LANGS = _DECIMAL_LANGS - {"en"}


def _thousands_group(lang: str, fraction: str) -> bool:
    """True when a period-separated ``fraction`` is a thousands group for
    ``lang`` ("10.000" in German), which keeps its digits like "1,000" in
    English does. English keeps "3.500" as a decimal."""
    return lang in _COMMA_DECIMAL_LANGS and len(fraction) == 3


# "50%" → "fifty <word>" only where the spoken percent word is unambiguous.
_PERCENT_WORD = {
    "en": "percent",
    "de": "Prozent",
    "es": "por ciento",
    "fr": "pour cent",
    "it": "per cento",
    "pt": "por cento",
    "nl": "procent",
}

_ISO_CODE_RE = re.compile(r"^([a-z]{2,3})(?:[-_]|$)")


def _num2words_lang(language: Optional[str]) -> Optional[str]:
    """Resolve a request language (display name or ISO-ish code) to a
    num2words locale, or ``None`` when digits should be left alone.

    Both lookup paths gate on ``_NUM2WORDS_LANGS`` — the vetted set is the
    single authority. Display names used to bypass it (#1139: "Vietnamese"
    reached num2words while "vi" wouldn't have), so an unvetted locale could
    mangle numbers depending on how the caller spelled the language.
    """
    if not language:
        return None
    s = str(language).strip().lower()
    if not s or s == "auto":
        return None
    code = _FULL_NAME_TO_CODE.get(s)
    if code:
        return code if code in _NUM2WORDS_LANGS else None
    m = _ISO_CODE_RE.match(s)
    if m:
        c = _ISO_ALIASES.get(m.group(1), m.group(1))
        if c in _NUM2WORDS_LANGS:
            return c
    return None


# num2words spells some variants with their own locale: its ``pt`` is European
# Portuguese ("dezasseis") and ``pt_BR`` Brazilian ("dezesseis"). Rule tables
# stay keyed by the base code; only the num2words call takes the locale.
_NUM2WORDS_DIALECT_LOCALES = {"pt-BR": "pt_BR", "pt-PT": "pt"}


def _num2words_locale(language: Optional[str], lang: str) -> str:
    """num2words locale for ``lang`` honouring the request's dialect (#3143).

    A plain "Portuguese"/"pt" means Brazilian Portuguese, so it uses
    ``pt_BR``; before the split every Portuguese request — even "pt-BR" —
    was read with European number words.
    """
    from core.language_variants import dialect_for

    dialect = dialect_for(language)
    if dialect and dialect.split("-", 1)[0] == lang:
        return _NUM2WORDS_DIALECT_LOCALES.get(dialect, lang)
    return lang


def _plain_lang_code(language: Optional[str]) -> Optional[str]:
    """Resolve a request language to a bare ISO code (no num2words vetting).

    Mirrors :func:`_num2words_lang` minus its membership check, so ko, ja, zh,
    th and vi resolve as well. Unusable or ``auto`` input gives ``None``.
    """
    key = (str(language).strip().lower()) if language else ""
    if key in ("", "auto"):
        return None
    if key in _FULL_NAME_TO_CODE:
        return _FULL_NAME_TO_CODE[key]
    hit = _ISO_CODE_RE.match(key)
    if hit is None:
        return None
    raw = hit.group(1)
    return _ISO_ALIASES.get(raw, raw)


# ── Spoken digit ranges ("20~30") ────────────────────────────────────────────
#
# A tilde between two numbers gets swallowed by some engines: OmniVoice voiced
# a Korean "20~30" as the single number 23. Where we know the natural linking
# word we say it; elsewhere the text is left alone. The ASCII hyphen is never
# treated as a range mark, since it also writes dates, phones and part codes.

# language -> (word between the endpoints, space before it, space after it)
_RANGE_JOINERS = {
    "ko": ("에서", False, True),
    "ja": ("から", False, False),
    "zh": ("到", False, False),
    "en": ("to", True, True),
}

_TILDES = "~\u301c\uff5e"
_NUM = r"[+-]?(?:\d{1,6}(?:\.\d{1,6})?|\.\d{1,6})"
_GLUE = r"\d.,A-Za-z+\-" + _TILDES  # neighbours that make a number part of something bigger
_RANGE_RE = re.compile(
    rf"(?<![{_GLUE}])(?P<lo>{_NUM})\s*[{_TILDES}]\s*(?P<hi>{_NUM})(?![{_GLUE}])"
)


def _is_chained(text: str, hit: re.Match) -> bool:
    """True if another tilde sits next to this match, ignoring whitespace."""
    left = text[: hit.start()].rstrip()[-1:]
    right = text[hit.end():].lstrip()[:1]
    return bool(left and left in _TILDES) or bool(right and right in _TILDES)


def _speak_number_ranges(text: str, lang: Optional[str]) -> str:
    """Spell out ``<number><tilde><number>`` for languages with a known joiner.

    Unsupported languages and malformed chains (``1~2~3``) are returned as is.
    """
    joiner = _RANGE_JOINERS.get(lang or "")
    if joiner is None or not text:
        return text
    hits = list(_RANGE_RE.finditer(text))
    if not hits or any(_is_chained(text, h) for h in hits):
        return text
    word, pad_before, pad_after = joiner
    glue = (" " if pad_before else "") + word + (" " if pad_after else "")
    return _RANGE_RE.sub(lambda h: h.group("lo") + glue + h.group("hi"), text)


# ── Universal safety filters (all languages) ─────────────────────────────────

# Zero-width junk & bidi controls, C0/C1 controls (except \t \n \r), BOM, U+FFFD.
# Preserve ZWNJ/ZWJ (U+200C/U+200D): they encode spelling, shaping and emoji.
# Keep the set explicit rather than encoding it as regex character ranges: the
# latter is easy to widen accidentally and obscures the intentionally preserved
# whitespace controls at the C0 boundaries.
_UNSAFE_CONTROL_CODEPOINTS = frozenset(
    (
        *range(0x00, 0x09),
        0x0B,
        0x0C,
        *range(0x0E, 0x20),
        *range(0x7F, 0xA0),
        0x200B,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2065),
        0xFEFF,
        0xFFFD,
    )
)
_UNSAFE_CONTROL_TRANSLATION = dict.fromkeys(_UNSAFE_CONTROL_CODEPOINTS)


# Markdown emphasis delimiters (``**bold**``, ``__bold__``, ``*italic*``,
# ``_italic_``) reach the engine as spoken words ("asterisk", #3197): a lone
# ``*`` pair happens to survive as emphasis, but ``**`` is read aloud.
#
# Boundaries (#3205 review): ``*``/``**`` need whitespace flanks only — a
# non-space after the opener, a non-space before the closer — because
# CommonMark lets asterisks open inside words (``un**believ**able``). ``_``/
# ``__`` additionally refuse word characters on the outer side, so
# ``snake_case`` and ``foo__bar__baz`` survive. Arithmetic (``2 * 3 * 4``)
# survives via the whitespace rule, and ``2*3*4`` via a digit-flank rule.
# Bodies span soft line breaks.
#
# Linear single-pass scan (#3205 P1): lazy ``(.+?)`` backtracks quadratically
# on unmatched openers (``"**a " * 25000`` stalls synthesis), so delimiters
# are tokenized once per class and paired left-to-right — a token pairs as a
# closer when a pending opener exists, else opens, else is stray and kept.
# Result assembly is one join over retained slices: rebuilding per span is
# itself quadratic on many matched pairs. Idempotent: a second pass finds no
# pairs. Bracket spans are masked during the match so emphasis around a
# speech control (``**Hello [pause 300ms] world**``) strips while the control
# survives; NUL placeholders are safe because the safety filters deleted NUL.
# Word characters are Unicode-aware (``str.isalnum`` plus combining marks, as
# for Malayalam) so ``имя_файла`` keeps its underscores like ``snake_case``.


def _is_wordch(ch: str) -> bool:
    return (
        ch == "_"
        or ch.isalnum()
        or unicodedata.combining(ch) != 0
        or unicodedata.category(ch) in ("Mc", "Me")
    )


def _delim_tokens(text: str, ch: str) -> list[tuple[int, int, int, int]]:
    """Split each maximal run of ch into ``**`` tokens then at most one ``*``.

    ``***`` → one ``**`` plus one ``*`` so bold+italic nests resolve across
    the two passes instead of stranding the run. Each token carries its run's
    bounds so flank checks are O(1) (rescanning a long run per token is
    quadratic on ``"__**__**" * N``).
    """
    toks: list[tuple[int, int, int, int]] = []
    for m in re.finditer(re.escape(ch) + "+", text):
        rs, re_ = m.start(), m.end()
        # ``\*`` is an escaped literal: the first marker of the run is text.
        k = rs
        while k > 0 and text[k - 1] == "\\":
            k -= 1
        if (rs - k) % 2:
            rs += 1
            if rs >= re_:
                continue
        s, n = rs, re_ - rs
        while n > 3:
            toks.append((s, 2, rs, re_))
            s += 2
            n -= 2
        if n == 3:
            toks.append((s, 2, rs, re_))
            toks.append((s + 2, 1, rs, re_))
        else:
            toks.append((s, n, rs, re_))
    return toks


def _open_ok(
    text: str, tok: tuple[int, int, int, int], n: int, word_guarded: bool
) -> bool:
    s, L, rs, _re = tok
    if s + L > n - 1 or text[s + L].isspace():
        return False
    # ``2*3*4`` is arithmetic, not emphasis: a digit on both sides of ``*``.
    if not word_guarded and rs > 0 and text[rs - 1].isdigit() and text[s + L].isdigit():
        return False
    return not word_guarded or rs == 0 or not _is_wordch(text[rs - 1])


def _close_ok(
    text: str, tok: tuple[int, int, int, int], n: int, word_guarded: bool
) -> bool:
    s, _L, _rs, re_ = tok
    if s == 0 or text[s - 1].isspace():
        return False
    if not word_guarded and re_ < n and text[s - 1].isdigit() and text[re_].isdigit():
        return False
    return not word_guarded or re_ >= n or not _is_wordch(text[re_])


def _pair_spans(
    text: str, tokens: list[tuple[int, int, int, int]], word_guarded: bool
) -> list[tuple[int, int]]:
    drop: list[tuple[int, int]] = []
    # Stack, not a single slot: ``**outer **inner** outer**`` must pair the
    # inner span first and still close the outer one (idempotent output).
    stack: list[tuple[int, int]] = []
    n = len(text)
    for tok in tokens:
        s, L = tok[0], tok[1]
        if stack and _close_ok(text, tok, n, word_guarded):
            os_, oL = stack.pop()
            drop.append((os_, os_ + oL))
            drop.append((s, s + L))
        elif _open_ok(text, tok, n, word_guarded):
            stack.append((s, L))
    return drop


def _delete_spans(text: str, spans: list[tuple[int, int]]) -> str:
    """Remove ascending, non-overlapping spans with a single join."""
    parts: list[str] = []
    last = 0
    for a, b in spans:
        parts.append(text[last:a])
        last = b
    parts.append(text[last:])
    return "".join(parts)


_MASK_RE = re.compile("\x00(\\d+)\x00")


def _strip_markdown_emphasis(text: str) -> str:
    spans: list[str] = []

    def _mask(m: re.Match) -> str:
        spans.append(m.group(0))
        return f"\x00{len(spans) - 1}\x00"

    out = _BRACKET_SPAN_RE.sub(_mask, text)
    for ch, guarded in (("*", False), ("_", True)):
        for L in (1, 2):
            toks = [t for t in _delim_tokens(out, ch) if t[1] == L]
            out = _delete_spans(out, sorted(_pair_spans(out, toks, guarded)))
    # One pass: a replace per span rescans the whole text (quadratic).
    result = _MASK_RE.sub(lambda m: spans[int(m.group(1))], out)
    if result != text:
        # Removing markers can join punctuation ("!*!*!" -> "!!!!!"): re-cap.
        result = _REPEAT_RE.sub(lambda m: m.group(1) * 3, result)
    return result


def _strip_unsafe_controls(text: str) -> str:
    """Delete only the explicitly enumerated unsafe Unicode controls."""
    return text.translate(_UNSAFE_CONTROL_TRANSLATION)

# A tiny, unambiguous HTML-entity leftover set. `&amp;` is decoded only when
# NOT followed by a letter/`#` — so double-encoded junk ("&amp;nbsp;") is left
# alone rather than decoded one layer per pass (idempotency).
_ENTITIES = {
    "&nbsp;": " ",
    "&quot;": '"',
    "&#39;": "'",
    "&apos;": "'",
    "&hellip;": "…",
    "&mdash;": "—",
    "&ndash;": "–",
}
_ENTITY_RE = re.compile(
    "(?:" + "|".join(re.escape(k) for k in _ENTITIES) + "|&amp;(?![a-zA-Z#]))"
)

# Same ASCII punctuation char repeated more than 3 times → capped at 3
# ("!!!!!!!!" / "........." cause dead air and babble). CJK punctuation and
# letters are deliberately untouched ("Nooooo" is expressive).
_REPEAT_RE = re.compile(r"([!?.,;:~_*#=-])\1{3,}")

_HSPACE_RE = re.compile(r"[^\S\n]+")   # horizontal whitespace runs → one space
_NEWLINE_RE = re.compile(r"\n{3,}")    # blank-line floods → one blank line


def _safety_filters(text: str) -> str:
    out = _strip_unsafe_controls(text)
    out = _ENTITY_RE.sub(lambda m: _ENTITIES.get(m.group(0), "&"), out)
    out = _REPEAT_RE.sub(lambda m: m.group(1) * 3, out)
    out = _HSPACE_RE.sub(" ", out)
    out = _NEWLINE_RE.sub("\n\n", out)
    return out.strip()


# ── Bracket masking ──────────────────────────────────────────────────────────
#
# Language passes must never rewrite `[…]` spans: `[pause 300ms]` /
# `[rate 0.9]` / `[voice:NAME]` are grammar, and `[[term|replacement]]`
# belongs to the pronunciation layer. Bounded repetition keeps it linear.

_BRACKET_SPAN_RE = re.compile(r"\[[^\][\n]{0,128}\]")


def _outside_brackets(text: str, fn: Callable[[str], str]) -> str:
    if "[" not in text:
        return fn(text)
    parts: list[str] = []
    last = 0
    for m in _BRACKET_SPAN_RE.finditer(text):
        parts.append(fn(text[last:m.start()]))
        parts.append(m.group(0))
        last = m.end()
    parts.append(fn(text[last:]))
    return "".join(parts)


# ── Abbreviation expansion ────────────────────────────────────────────────────
#
# Per-language (key, expansion, guard) triples. Matching is case-sensitive
# (a lowercase "st." is NOT the title "St."); lowercase connective keys
# ("e.g.") get an auto-added sentence-initial variant. Guards:
#   "cap"   — only before a capitalized word (titles precede names; leaves
#             street-suffix "Elm St." / "Elm Dr." untouched).
#   "digit" — only before a number ("No. 5"; leaves the word "No." alone).

_ABBREVIATIONS: dict[str, list[tuple[str, str, Optional[str]]]] = {
    "en": [
        ("Dr.", "Doctor", "cap"),
        ("Mr.", "Mister", "cap"),
        ("Mrs.", "Missus", "cap"),
        ("Prof.", "Professor", "cap"),
        ("St.", "Saint", "cap"),
        ("Mt.", "Mount", "cap"),
        ("Jr.", "Junior", None),
        ("Sr.", "Senior", None),
        ("vs.", "versus", None),
        ("etc.", "et cetera", None),
        ("e.g.", "for example", None),
        ("i.e.", "that is", None),
        ("approx.", "approximately", None),
        ("No.", "number", "digit"),
    ],
    "de": [
        ("Dr.", "Doktor", "cap"),
        ("Prof.", "Professor", "cap"),
        ("Nr.", "Nummer", "digit"),
        ("z.B.", "zum Beispiel", None),
        ("z. B.", "zum Beispiel", None),
        ("d.h.", "das heißt", None),
        ("d. h.", "das heißt", None),
        ("usw.", "und so weiter", None),
        ("bzw.", "beziehungsweise", None),
        ("ca.", "circa", None),
    ],
    "es": [
        ("Sr.", "Señor", "cap"),
        ("Sra.", "Señora", "cap"),
        ("Srta.", "Señorita", "cap"),
        ("Dr.", "Doctor", "cap"),
        ("Dra.", "Doctora", "cap"),
        ("Ud.", "usted", None),
        ("Uds.", "ustedes", None),
        ("etc.", "etcétera", None),
        ("núm.", "número", "digit"),
    ],
    "fr": [
        # "M." is deliberately absent: indistinguishable from a middle initial.
        ("Mme", "Madame", "cap"),
        ("Mmes", "Mesdames", "cap"),
        ("Mlle", "Mademoiselle", "cap"),
        ("Mlles", "Mesdemoiselles", "cap"),
        ("etc.", "et cetera", None),
        ("n°", "numéro", "digit"),
        ("N°", "Numéro", "digit"),
    ],
}

_GUARD_LOOKAHEAD = {
    None: "",
    "cap": r"(?=\s+[A-ZÀ-ÖØ-Þ])",
    "digit": r"(?=\s*\d)",
}


def _compile_abbreviations() -> dict[str, tuple[re.Pattern, dict[str, str]]]:
    compiled: dict[str, tuple[re.Pattern, dict[str, str]]] = {}
    for lang, entries in _ABBREVIATIONS.items():
        entries = list(entries)
        # Sentence-initial variants for lowercase connectives ("E.g." → …).
        for key, expansion, guard in list(entries):
            if key[:1].islower():
                cap_key = key[0].upper() + key[1:]
                if not any(k == cap_key for k, _, _ in entries):
                    entries.append((cap_key, expansion[0].upper() + expansion[1:], guard))
        entries.sort(key=lambda e: len(e[0]), reverse=True)  # longest key wins
        lookup = {key: expansion for key, expansion, _ in entries}
        alts = []
        for key, _, guard in entries:
            suffix = r"(?!\w)" if key[-1:].isalnum() else ""
            alts.append(f"{re.escape(key)}{suffix}{_GUARD_LOOKAHEAD[guard]}")
        # Literal alternation with per-key guards; no nested quantifiers.
        pattern = re.compile(r"(?<![\w.])(?:" + "|".join(alts) + ")")
        compiled[lang] = (pattern, lookup)
    return compiled


_ABBREV_COMPILED = _compile_abbreviations()


def _expand_abbreviations(text: str, lang: str) -> str:
    entry = _ABBREV_COMPILED.get(lang)
    if entry is None:
        return text
    pattern, lookup = entry

    def _repl(m: re.Match) -> str:
        expansion = lookup.get(m.group(0), m.group(0))
        # "No.5" / "Nr.5": the digit guard admits a number glued to the
        # abbreviation, and the expansion must not be glued to it in turn
        # ("number5" is a word the number pass leaves alone).
        if expansion != m.group(0) and m.string[m.end():m.end() + 1].isdigit():
            expansion += " "
        return expansion

    return pattern.sub(_repl, text)


# ── Numbers → words ──────────────────────────────────────────────────────────
#
# Every pattern requires clean word boundaries: digits glued to letters
# ("MP3", "v2"), separators ("1,000", "3-5", "1/2", "12:34:56"), leading
# zeros ("007") or 7+ digits (IDs, phone numbers) are all left alone.

# EN-only clock time: H:MM, 0-23 hours. Rejects H:MM:SS (durations).
_TIME_RE = re.compile(r"(?<![\w:.,/$%+-])([01]?\d|2[0-3]):([0-5]\d)(?![\w:/%+-])(?![.,]\d)")

# EN-only ordinal, suffix verified in the callback ("2th" stays as-is).
_ORDINAL_RE = re.compile(r"(?<![\w.,:/$%+-])(\d{1,4})(st|nd|rd|th)(?![\w:/%+-])(?![.,]\d)")

# EN-only dollars: $N or $N.CC. "$1,000" and "$5.5" are blocked by the
# lookahead; a period or comma that ends the sentence or clause is not, the
# same guard the integer and decimal rules use ("It costs $5." is spoken).
_CURRENCY_RE = re.compile(r"(?<![\w.,:/$%+-])\$(\d{1,6})(?:\.(\d{2}))?(?![\w:/%+-])(?![.,]\d)")

_PERCENT_RE = re.compile(r"(?<![\w.,:/$%+-])(\d{1,6}(?:\.\d{1,4})?)\s?%(?![\w:/%+-])(?![.,]\d)")

_DECIMAL_RE = re.compile(
    r"(?<![\w.,:/$%-])(\d{1,6})\.(\d{1,6})(?![\w:/%-])(?![.,]\d)"
)

_INTEGER_RE = re.compile(
    r"(?<![\w.,:/$%-])(?!0\d)(\d{1,6})(?![\w:/%-])(?![.,]\d)"
)

# Day-of-month ordinals in the locales that mark ordinals with a period
# ("am 3. Mai", "den 17. mai", "3. května"). The spoken form inflects with the
# phrase, so the day is left as written before a month name; the integer rule
# used to speak the cardinal and leave the period behind ("drei. Mai").
_DATE_MONTHS = {
    "de": r"Jan(?:uar|\.)|Jänner|Feb(?:ruar|\.)|März|Mär\.|Mrz\.|Apr(?:il|\.)|Mai|Jun(?:i|\.)"
          r"|Jul(?:i|\.)|Aug(?:ust|\.)|Sep(?:tember|t\.|\.)|Okt(?:ober|\.)|Nov(?:ember|\.)"
          r"|Dez(?:ember|\.)",
    "da": r"januar|februar|marts|april|maj|juni|juli|august|september|oktober|november|december",
    "no": r"januar|februar|mars|april|mai|juni|juli|august|september|oktober|november|desember",
    "fi": r"(?:tammi|helmi|maalis|huhti|touko|kesä|heinä|elo|syys|loka|marras|joulu)kuu\w*",
    "cs": r"led(?:en|na)|únor[a]?|břez(?:en|na)|dub(?:en|na)|květ(?:en|na)|červ(?:en|na|enec|ence)"
          r"|srp(?:en|na)|září|říj(?:en|na)|listopad[u]?|prosin(?:ec|ce)",
    "pl": r"stycz(?:eń|nia)|lut(?:y|ego)|mar(?:zec|ca)|kwie(?:cień|tnia)|maj[a]?|czerw(?:iec|ca)"
          r"|lip(?:iec|ca)|sierp(?:ień|nia)|wrze(?:sień|śnia)|październik[a]?|listopad[a]?|grud(?:zień|nia)",
}
_DATE_MONTH_RE = {
    code: re.compile(r"\.\s+(?:" + months + r")(?![^\W_])", re.IGNORECASE)
    for code, months in _DATE_MONTHS.items()
}
# Space-grouped thousands ("1 234", "10 000", "1 234,5", "12 500 %") in the
# locales that also group with a period. The run is kept as written, like
# "1,000" in English; without this the percent and integer rules read the
# trailing or leading group on its own. A leading minus ("-1 234 %") belongs
# to the run; the lookbehind still rejects it after a word ("a-1 234").
_SPACE_GROUP_RE = re.compile(
    r"(?<![\w.,:/$%-])-?\d{1,3}(?: \d{3})+(?:,\d+)?(?:\s?%)?(?![\w:/%-])(?![.,]\d)"
)


# English reads "2 100 meters" as two numbers, so only a run whose groups are
# all "000" ("10 000", "1 000 000", "12 000%") is kept whole there: a "000"
# group never starts a number of its own, and the integer rule used to speak
# the leading group and leave the zeros behind ("ten 000").
_EN_SPACE_GROUP_RE = re.compile(
    r"(?<![\w.,:/$%-])-?\d{1,3}(?: 000)+(?:\s?%)?(?![\w:/%-])(?![.,]\d)"
)


def _grouped_spans(text: str, lang: str) -> list[tuple[int, int]]:
    if lang == "en":
        return [g.span() for g in _EN_SPACE_GROUP_RE.finditer(text)]
    if lang not in _COMMA_DECIMAL_LANGS:
        return []
    return [g.span() for g in _SPACE_GROUP_RE.finditer(text)]


def _in_span(m: re.Match, spans: list[tuple[int, int]]) -> bool:
    return any(start <= m.start() < end for start, end in spans)

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _glued_to_mark(m: re.Match) -> bool:
    """Is a combining mark (Unicode category M*) directly next to this match?

    Regex word boundaries ignore marks, so a number stuck to a vowel sign or a
    virama looks standalone to them; it is really part of a written word.
    """
    text = m.string
    neighbours = []
    if m.start() > 0:
        neighbours.append(text[m.start() - 1])
    if m.end() < len(text):
        neighbours.append(text[m.end()])
    return any(unicodedata.category(c)[0] == "M" for c in neighbours)


def _leading_zero(raw: str) -> bool:
    """Zero-padded identifiers such as "007" (any script's zero digit) are codes, not quantities."""
    if len(raw) < 2:
        return False
    return unicodedata.digit(raw[0], -1) == 0


def _correct_ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 13:
        return "th"
    return _ORDINAL_SUFFIX.get(n % 10, "th")


def _numbers_to_words(text: str, lang: str, locale: Optional[str] = None) -> str:
    """``lang`` keys the rule tables; ``locale`` (default ``lang``) is the
    num2words locale, which differs for regional variants (``pt_BR``)."""
    try:
        from num2words import num2words as _num2words
    except ImportError:  # pragma: no cover — direct dependency; belt & braces
        return text
    base, n2w_locale = lang, locale or lang

    def num2words(value, lang: str, **kwargs):
        # Every call below names its rule language; swap in the variant locale.
        return _num2words(value, lang=n2w_locale if lang == base else lang, **kwargs)

    def _safe(m: re.Match, render: Callable[[re.Match], str], *, clock: bool = False) -> str:
        # Any num2words hiccup leaves this occurrence untouched.
        whole = m.group(1).partition(".")[0]
        if _glued_to_mark(m) or (_leading_zero(whole) and not clock):
            return m.group(0)
        try:
            return render(m)
        except Exception:  # noqa: BLE001 — conservative: never mangle
            return m.group(0)

    if lang == "en":
        def _time(m: re.Match) -> str:
            h, mm = int(m.group(1)), int(m.group(2))
            hw = num2words(h, lang="en")
            if mm == 0:
                # "o'clock" belongs to the 12-hour dial: a 24-hour hour on the
                # hour is read in hundreds ("thirteen hundred"), never
                # "thirteen o'clock" or "zero o'clock".
                return f"{hw} o'clock" if 1 <= h <= 12 else f"{hw} hundred"
            if mm < 10:
                return f"{hw} oh {num2words(mm, lang='en')}"
            return f"{hw} {num2words(mm, lang='en')}"

        text = _TIME_RE.sub(lambda m: _safe(m, _time, clock=True), text)

        def _ordinal(m: re.Match) -> str:
            n = int(m.group(1))
            if m.group(2) != _correct_ordinal_suffix(n):
                return m.group(0)
            return num2words(n, lang="en", to="ordinal")

        text = _ORDINAL_RE.sub(lambda m: _safe(m, _ordinal), text)

        def _currency(m: re.Match) -> str:
            dollars = int(m.group(1))
            unit = "dollar" if dollars == 1 else "dollars"
            spoken = f"{num2words(dollars, lang='en')} {unit}"
            if m.group(2) is None:
                return spoken
            cents = int(m.group(2))
            # "$0.50" is "fifty cents" and "$5.00" is "five dollars": the
            # zero part is not spoken ("zero dollars, fifty cents").
            if cents == 0:
                return spoken
            if dollars == 0:
                return f"{num2words(cents, lang='en')} {'cent' if cents == 1 else 'cents'}"
            amount = float(f"{m.group(1)}.{m.group(2)}")
            return num2words(amount, lang="en", to="currency", currency="USD")

        text = _CURRENCY_RE.sub(lambda m: _safe(m, _currency), text)

    def _spoken_decimal(whole: str, fraction: str) -> str:
        spoken = num2words(float(f"{whole}.{fraction}"), lang=lang)
        # A float has no trailing zeros: "3.0" read as "three" and "2.50"
        # as "two point five". Speak the zeros that were written.
        # Unicode digits (fullwidth, Arabic-Indic) count as zeros too.
        ascii_fraction = "".join(str(unicodedata.digit(c, 0)) for c in fraction)
        zeros = len(ascii_fraction) - len(ascii_fraction.rstrip("0"))
        if zeros:
            from num2words import CONVERTER_CLASSES
            point = getattr(CONVERTER_CLASSES.get(n2w_locale), "pointword", None)
            if not isinstance(point, str):
                return spoken
            zero = num2words(0, lang=lang)
            if zeros == len(ascii_fraction):
                spoken = f"{num2words(int(whole), lang=lang)} {point}"
            spoken += f" {zero}" * zeros
        return spoken

    percent_word = _PERCENT_WORD.get(lang)
    if percent_word:
        grouped = _grouped_spans(text, lang)

        def _percent(m: re.Match) -> str:
            if _in_span(m, grouped):
                return m.group(0)
            raw = m.group(1)
            if "." in raw:
                fraction = raw.split(".", 1)[1]
                if lang not in _DECIMAL_LANGS or _thousands_group(lang, fraction):
                    return m.group(0)
                spoken = _spoken_decimal(raw.split(".", 1)[0], fraction)
            else:
                spoken = num2words(int(raw), lang=lang)
            return f"{spoken} {percent_word}"

        text = _PERCENT_RE.sub(lambda m: _safe(m, _percent), text)

    if lang in _DECIMAL_LANGS:
        def _decimal(m: re.Match) -> str:
            whole, fraction = m.group(1), m.group(2)
            if _thousands_group(lang, fraction):
                return m.group(0)
            return _spoken_decimal(whole, fraction)

        text = _DECIMAL_RE.sub(lambda m: _safe(m, _decimal), text)

    month_re = _DATE_MONTH_RE.get(lang)
    grouped = _grouped_spans(text, lang)

    def _integer(m: re.Match) -> str:
        raw = m.group(1)
        if _leading_zero(raw):
            return m.group(0)
        if _in_span(m, grouped):
            return m.group(0)
        n = int(raw)
        if month_re and 1 <= n <= 31 and month_re.match(text, m.end()):
            return m.group(0)
        if len(raw) == 4 and 1500 <= n <= 2099:
            # Bare 4-digit numbers in this range read as years
            # ("nineteen eighty-four"); fall back to cardinal where the
            # locale has no year form (sv).
            try:
                return num2words(n, lang=lang, to="year")
            except Exception:  # noqa: BLE001
                pass
        return num2words(n, lang=lang)

    return _INTEGER_RE.sub(lambda m: _safe(m, _integer), text)


# ── Languages with their own number-word module ─────────────────────────────
#
# For languages that num2words cannot do, a plain module supplies
# cardinal(int), decimal(int, str) and percent(str). The token patterns and
# bracket masking are shared with the num2words route; a token that fails to
# render is simply left as written.

# Urdu (ur) followed for the same reason: OmniVoice read "1956" as "انیس سو سپنس", while
# the sentence spelled out in Urdu words came back with 0% CER.
_NATIVE_VERBALIZERS = {
    "ml": "services.number_words_ml",
    "ur": "services.number_words_ur",
}


def _native_lang(language: Optional[str]) -> Optional[str]:
    """Language code if a hand-written verbalizer exists for it, else ``None``."""
    code = _plain_lang_code(language)
    return code if code in _NATIVE_VERBALIZERS else None


def _numbers_to_words_native(text: str, lang: str) -> str:
    try:
        words = importlib.import_module(_NATIVE_VERBALIZERS[lang])
    except Exception:  # noqa: BLE001
        return text

    def rewrite(pattern: re.Pattern, speak: Callable[[re.Match], str], source: str) -> str:
        def one(m: re.Match) -> str:
            head = m.group(1).partition(".")[0]
            if _glued_to_mark(m) or _leading_zero(head):
                return m.group(0)
            try:
                return speak(m)
            except Exception:  # noqa: BLE001
                return m.group(0)

        return pattern.sub(one, source)

    # Percentages and decimals first, so the integer pass never sees their digits.
    steps = (
        (_PERCENT_RE, lambda m: words.percent(m.group(1))),
        (_DECIMAL_RE, lambda m: words.decimal(int(m.group(1)), m.group(2))),
        (_INTEGER_RE, lambda m: words.cardinal(int(m.group(1)))),
    )
    for pattern, speak in steps:
        text = rewrite(pattern, speak, text)
    return text


# ── Public API ───────────────────────────────────────────────────────────────

def normalize_text(text: str, language: Optional[str] = None) -> str:
    """Pure, idempotent normalization pass (no pref gate — see
    :func:`normalize_for_tts` for the gated entry point pipelines call)."""
    if not text:
        return text or ""
    out = _safety_filters(text)
    out = _strip_markdown_emphasis(out)
    # Runs outside the num2words gate: ko/ja/zh keep their digits but still
    # need the tilde spoken.
    spoken_as = _plain_lang_code(language)
    if spoken_as in _RANGE_JOINERS:
        out = _outside_brackets(out, partial(_speak_number_ranges, lang=spoken_as))
    lang = _num2words_lang(language)
    if lang:
        if lang in _ABBREV_COMPILED:
            out = _outside_brackets(out, lambda t: _expand_abbreviations(t, lang))
        locale = _num2words_locale(language, lang)
        out = _outside_brackets(out, lambda t: _numbers_to_words(t, lang, locale))
    elif _native_lang(language):
        own = _native_lang(language)
        out = _outside_brackets(out, lambda t: _numbers_to_words_native(t, own))
    return out


def normalization_enabled() -> bool:
    """Default ON; the env var is the power-user off switch. (A prefs key was
    once read here too, but nothing ever wrote it.)"""
    env = os.environ.get(ENV_VAR)
    if env is None:
        return True
    return env.strip().lower() not in ("0", "false", "no", "off", "")


def normalize_for_tts(text: str, language: Optional[str] = None) -> str:
    """Gated + hardened entry point: pref/env toggle, never raises.

    Every TTS pipeline calls this exactly once, at its text→engine choke
    point, BEFORE the pronunciation dictionary (see module docstring).
    """
    if not text:
        return text or ""
    if not normalization_enabled():
        return text
    try:
        return normalize_text(text, language)
    except Exception:  # noqa: BLE001 — normalization must never break synth
        logger.warning("text normalization failed; using raw text", exc_info=True)
        return text
