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
    display name from frontend/src/languages.json or an ISO-ish code — both
    resolve via :func:`_num2words_lang`), or through a hand-written native
    verbalizer for languages num2words lacks (``_NATIVE_VERBALIZERS``, e.g.
    Malayalam). Everything else keeps its digits.
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

Gate: prefs key ``text_normalization_enabled`` (default ON) with env override
``OMNIVOICE_TEXT_NORMALIZATION`` — the same env-wins contract as
``OMNIVOICE_PRONUNCIATION`` ("0"/"false"/"no"/"off" disable).
:func:`normalize_for_tts` is the gated entry point every pipeline calls; it
never raises — normalization is never allowed to break synthesis.
"""

from __future__ import annotations

import logging
import os
import re
import unicodedata
from typing import Callable, Optional

logger = logging.getLogger("omnivoice.text_normalization")

ENV_VAR = "OMNIVOICE_TEXT_NORMALIZATION"
PREF_KEY = "text_normalization_enabled"


# ── Language resolution ───────────────────────────────────────────────────────
#
# The `language` kwarg across the app is normally a full display name from
# frontend/src/languages.json ("English", "German", …) — see
# resolve_kokoro_lang_code in services/tts_backend.py — but ISO-ish codes
# ("en", "pt-BR") also flow through dub/API callers. Map both to a num2words
# locale; anything unmapped keeps its digits (false negatives are fine).

_FULL_NAME_TO_CODE = {
    "english": "en",
    "malayalam": "ml",
    "german": "de",
    "spanish": "es",
    "french": "fr",
    "italian": "it",
    "portuguese": "pt",
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
    # Below: inert for num2words (absent from _NUM2WORDS_LANGS, which reads
    # digits natively for these scripts), present so _plain_lang_code can
    # resolve them for the digit-range rule.
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


def _plain_lang_code(language: Optional[str]) -> Optional[str]:
    """Resolve a request language to a bare ISO code, with no num2words gate.

    :func:`_num2words_lang` answers "may I call num2words for this?" and so
    returns ``None`` for ko/ja/zh/th/vi. Rules that are not num2words-backed
    need the code itself, which is what this returns.
    """
    if not language:
        return None
    s = str(language).strip().lower()
    if not s or s == "auto":
        return None
    code = _FULL_NAME_TO_CODE.get(s)
    if code:
        return code
    m = _ISO_CODE_RE.match(s)
    if m:
        return _ISO_ALIASES.get(m.group(1), m.group(1))
    return None


# ── Digit ranges ─────────────────────────────────────────────────────────────
# "20~30" loses its separator at the engine and reads as ONE number: OmniVoice
# says "이십삼" (23) for "20~30초". Speak the separator instead. Verified by
# rendering each form and transcribing it back (ko, OmniVoice):
#     "20~30초"        → heard "23초"          ✗
#     "20에서 30초"     → heard "20에서 30초"    ✓
# Only the tilde family is rewritten — those are unambiguously range marks
# between digits. An ASCII hyphen is left alone on purpose: it also spells
# dates, phone numbers and product codes, where "to" would be wrong.
#: Spacing is part of the form, not decoration: a Korean postposition binds to
#: the numeral ("20에서 30"), Japanese and Chinese set no spaces at all, and
#: English needs them on both sides.
_RANGE_FORM = {
    "ko": "{a}에서 {b}",
    "ja": "{a}から{b}",
    "zh": "{a}到{b}",
    "en": "{a} to {b}",
}

#: ASCII tilde, wave dash, fullwidth tilde — Japanese and Korean IMEs emit the
#: latter two, so all three have to match.
#:
#: Match complete signed/decimal endpoints; reject partial numbers and product
#: codes while allowing adjacent CJK units. Guard all tilde forms so malformed
#: chains cannot be partially rewritten, including when their separators have
#: whitespace around them.
_RANGE_MARKS = "~\u301c\uff5e"
_RANGE_ENDPOINT = r"[+-]?(?:\d{1,6}(?:\.\d{1,6})?|\.\d{1,6})"
_NUM_RANGE_RE = re.compile(
    rf"(?<![\d.,A-Za-z+{_RANGE_MARKS}-])({_RANGE_ENDPOINT})"
    rf"\s*[{_RANGE_MARKS}]\s*({_RANGE_ENDPOINT})"
    rf"(?![\d.,A-Za-z+{_RANGE_MARKS}-])"
)


def _speak_number_ranges(text: str, lang: str) -> str:
    """Speak complete tilde ranges only for languages with a verified form."""
    form = _RANGE_FORM.get(lang)
    if not form:
        return text

    def replace(match: re.Match) -> str:
        before, after = match.start() - 1, match.end()
        while before >= 0 and text[before].isspace():
            before -= 1
        while after < len(text) and text[after].isspace():
            after += 1
        if ((before >= 0 and text[before] in _RANGE_MARKS)
                or (after < len(text) and text[after] in _RANGE_MARKS)):
            return match.group(0)
        return form.format(a=match.group(1), b=match.group(2))

    return _NUM_RANGE_RE.sub(replace, text)


# ── Universal safety filters (all languages) ─────────────────────────────────

# Zero-width & bidi controls, C0/C1 controls (except \t \n \r), BOM, U+FFFD.
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
        *range(0x200B, 0x2010),
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2065),
        0xFEFF,
        0xFFFD,
    )
)
_UNSAFE_CONTROL_TRANSLATION = dict.fromkeys(_UNSAFE_CONTROL_CODEPOINTS)


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
        return lookup.get(m.group(0), m.group(0))

    return pattern.sub(_repl, text)


# ── Numbers → words ──────────────────────────────────────────────────────────
#
# Every pattern requires clean word boundaries: digits glued to letters
# ("MP3", "v2"), separators ("1,000", "3-5", "1/2", "12:34:56"), leading
# zeros ("007") or 7+ digits (IDs, phone numbers) are all left alone.

# EN-only clock time: H:MM, 0-23 hours. Rejects H:MM:SS (durations).
_TIME_RE = re.compile(r"(?<![\d:.,])([01]?\d|2[0-3]):([0-5]\d)(?![\d:])")

# EN-only ordinal, suffix verified in the callback ("2th" stays as-is).
_ORDINAL_RE = re.compile(r"(?<![\w.,])(\d{1,4})(st|nd|rd|th)\b")

# EN-only dollars: $N or $N.CC. "$1,000" is blocked by the lookahead.
_CURRENCY_RE = re.compile(r"(?<!\w)\$(\d{1,6})(?:\.(\d{2}))?(?![\d.,])")

_PERCENT_RE = re.compile(r"(?<![\w.,])(\d{1,6}(?:\.\d{1,4})?)\s?%")

_DECIMAL_RE = re.compile(
    r"(?<![\w.,:/$%-])(\d{1,6})\.(\d{1,6})(?![\w:/%-])(?![.,]\d)"
)

_INTEGER_RE = re.compile(
    r"(?<![\w.,:/$%-])(?!0\d)(\d{1,6})(?![\w:/%-])(?![.,]\d)"
)

_ORDINAL_SUFFIX = {1: "st", 2: "nd", 3: "rd"}


def _glued_to_mark(m: re.Match) -> bool:
    """True when the match touches a combining mark (``ാ``, ``്``, …).

    Python's ``\\w`` excludes Unicode marks, so the regex boundaries above
    treat "10ാം" or "ക്ലാസ്10" as isolated numbers and splice words into the
    middle of a syllable. A mark belongs to the word it attaches to: leave it.
    """
    s, start, end = m.string, m.start(), m.end()
    return any(
        unicodedata.category(ch).startswith("M")
        for ch in (s[start - 1:start], s[end:end + 1]) if ch
    )


def _leading_zero(raw: str) -> bool:
    """``(?!0\\d)`` only sees ASCII zero; ``\\d`` also matches native digits
    (൦൦൭, ٠٠٧), which must stay codes just like "007"."""
    return len(raw) > 1 and unicodedata.digit(raw[0], -1) == 0


def _correct_ordinal_suffix(n: int) -> str:
    if 10 <= n % 100 <= 13:
        return "th"
    return _ORDINAL_SUFFIX.get(n % 10, "th")


def _numbers_to_words(text: str, lang: str) -> str:
    try:
        from num2words import num2words
    except ImportError:  # pragma: no cover — direct dependency; belt & braces
        return text

    def _safe(m: re.Match, render: Callable[[re.Match], str]) -> str:
        # Any num2words hiccup leaves this occurrence untouched.
        if _glued_to_mark(m):
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
                return f"{hw} o'clock"
            if mm < 10:
                return f"{hw} oh {num2words(mm, lang='en')}"
            return f"{hw} {num2words(mm, lang='en')}"

        text = _TIME_RE.sub(lambda m: _safe(m, _time), text)

        def _ordinal(m: re.Match) -> str:
            n = int(m.group(1))
            if m.group(2) != _correct_ordinal_suffix(n):
                return m.group(0)
            return num2words(n, lang="en", to="ordinal")

        text = _ORDINAL_RE.sub(lambda m: _safe(m, _ordinal), text)

        def _currency(m: re.Match) -> str:
            dollars = int(m.group(1))
            if m.group(2) is not None:
                amount = float(f"{m.group(1)}.{m.group(2)}")
                return num2words(amount, lang="en", to="currency", currency="USD")
            unit = "dollar" if dollars == 1 else "dollars"
            return f"{num2words(dollars, lang='en')} {unit}"

        text = _CURRENCY_RE.sub(lambda m: _safe(m, _currency), text)

    percent_word = _PERCENT_WORD.get(lang)
    if percent_word:
        def _percent(m: re.Match) -> str:
            raw = m.group(1)
            if "." in raw:
                if lang not in _DECIMAL_LANGS:
                    return m.group(0)
                value: object = float(raw)
            else:
                value = int(raw)
            return f"{num2words(value, lang=lang)} {percent_word}"

        text = _PERCENT_RE.sub(lambda m: _safe(m, _percent), text)

    if lang in _DECIMAL_LANGS:
        def _decimal(m: re.Match) -> str:
            return num2words(float(f"{m.group(1)}.{m.group(2)}"), lang=lang)

        text = _DECIMAL_RE.sub(lambda m: _safe(m, _decimal), text)

    def _integer(m: re.Match) -> str:
        raw = m.group(1)
        if _leading_zero(raw):
            return m.group(0)
        n = int(raw)
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


# ── Native verbalizers (languages num2words lacks) ──────────────────────────
#
# Same conservative regexes and bracket masking as the num2words path, but the
# rendering is a hand-written module. Malayalam (ml) is the first: num2words
# ships bn/kn/te but no ml, so Malayalam dubs reached the engine with raw
# digits. Each module exposes ``cardinal(int)``, ``decimal(int, str)`` and
# ``percent(str)``; any exception leaves that occurrence untouched.

_NATIVE_VERBALIZERS = {"ml": "services.number_words_ml"}


def _native_lang(language: Optional[str]) -> Optional[str]:
    """Resolve a request language to a native-verbalizer code, or ``None``."""
    plain = _plain_lang_code(language)
    return plain if plain in _NATIVE_VERBALIZERS else None


def _numbers_to_words_native(text: str, lang: str) -> str:
    """Verbalize percent, decimal and integer tokens via the native module
    for ``lang``; any failure leaves the input untouched."""
    import importlib

    try:
        mod = importlib.import_module(_NATIVE_VERBALIZERS[lang])
    except Exception:  # noqa: BLE001 — never break synthesis
        return text

    def _safe(m: re.Match, render: Callable[[re.Match], str]) -> str:
        """Render one match; on any error return the original text."""
        if _glued_to_mark(m):
            return m.group(0)
        try:
            return render(m)
        except Exception:  # noqa: BLE001 — conservative: never mangle
            return m.group(0)

    text = _PERCENT_RE.sub(lambda m: _safe(m, lambda m: mod.percent(m.group(1))), text)
    text = _DECIMAL_RE.sub(
        lambda m: _safe(m, lambda m: mod.decimal(int(m.group(1)), m.group(2))), text
    )
    def _integer(m: re.Match) -> str:
        raw = m.group(1)
        return m.group(0) if _leading_zero(raw) else mod.cardinal(int(raw))

    return _INTEGER_RE.sub(lambda m: _safe(m, _integer), text)


# ── Public API ───────────────────────────────────────────────────────────────

def normalize_text(text: str, language: Optional[str] = None) -> str:
    """Pure, idempotent normalization pass (no pref gate — see
    :func:`normalize_for_tts` for the gated entry point pipelines call)."""
    if not text:
        return text or ""
    out = _safety_filters(text)
    # Runs outside the num2words gate below: ko/ja/zh keep their digits (that
    # gate returns None for them) but still need the range mark spoken.
    plain = _plain_lang_code(language)
    if plain:
        out = _outside_brackets(out, lambda t: _speak_number_ranges(t, plain))
    lang = _num2words_lang(language)
    if lang:
        if lang in _ABBREV_COMPILED:
            out = _outside_brackets(out, lambda t: _expand_abbreviations(t, lang))
        out = _outside_brackets(out, lambda t: _numbers_to_words(t, lang))
    else:
        native = _native_lang(language)
        if native:
            out = _outside_brackets(out, lambda t: _numbers_to_words_native(t, native))
    return out


def normalization_enabled() -> bool:
    """Env wins (power-user override, mirrors OMNIVOICE_PRONUNCIATION);
    otherwise the ``text_normalization_enabled`` pref, default ON."""
    env = os.environ.get(ENV_VAR)
    if env is not None:
        return env.strip().lower() not in ("0", "false", "no", "off", "")
    try:
        from core import prefs
        return bool(prefs.get(PREF_KEY, True))
    except Exception:  # noqa: BLE001 — prefs unreadable → default ON
        return True


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
