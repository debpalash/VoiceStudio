"""Sentence chunker for streaming TTS (Wave 1.4 — /ws/tts polish).

Adapted from Patter (https://github.com/PatterAI/Patter), MIT License,
Copyright (c) 2026 Patter Contributors. Ported behavior-identical (the
golden parity scenarios ship as fixtures in tests/test_sentence_chunker.py);
only this header differs.

Accumulates streaming text (LLM tokens or a whole request) and yields
complete sentences. Regex-based marker replacement handles abbreviations,
acronyms, decimals, websites, ellipsis, and CJK/non-Latin punctuation —
the terminator tables below contain functional CJK and are allowlisted in
tests/test_no_hardcoded_cjk.py. Used by api/routers/tts_stream.py to
synthesize sentence-by-sentence for low time-to-first-audio.
"""

from __future__ import annotations

import re

# Default minimum sentence length before emitting.
# Fragments shorter than this are merged with the next sentence.
DEFAULT_MIN_SENTENCE_LEN = 20

# Minimum word count for emitting a "short" sentence (one whose total length
# is below ``min_sentence_len``) as soon as a terminator is seen. Default is
# 1: a single-word reply ("Yes.", "Done.") flushes immediately on the
# terminator so TTS can speak it without waiting for ``flush()``. Acronym
# and decimal guards in ``_maybe_short_flush`` still block dangerous cases
# ("U.S.", "f(x) = 2."). Bumping this to 2+ keeps single-word utterances
# buffered until ``flush()`` is called by the caller.
DEFAULT_MIN_WORDS_FOR_SHORT_FLUSH = 1

# ---------------------------------------------------------------------------
# Per-language honorific / abbreviation prefixes.
#
# Each entry is the ALPHA prefix (no trailing period) — the regex framework
# in ``_split_sentences`` appends the ``[.]`` itself. We merge all language
# lists into a single regex alternation so the chunker handles mixed-language
# text correctly out of the box (this is the behaviour shipped since the
# SDK introduced sentence chunking; per-language constants are an
# organisational refactor that also lets callers verify per-language
# coverage in tests).
#
# Single-letter honorifics (French "M.", "A.") are deliberately omitted —
# they are handled by the existing ``\\s + alphabets + [.] `` rule which
# preserves any single-letter-period sequence.
# ---------------------------------------------------------------------------

# English (NLTK Punkt training set + common military/civic).
HONORIFICS_EN = (
    "Mr",
    "St",
    "Mrs",
    "Ms",
    "Dr",
    "Prof",
    "Gen",
    "Sen",
    "Rep",
    "Lt",
    "Cpt",
    "Capt",
    "Col",
    "Cmdr",
    "Adm",
)

# Italian. Compound abbreviations like "Sig.ra" / "Dott.ssa" / "Prof.ssa"
# are handled implicitly: the prefix regex matches the leading word
# ("Sig", "Dott", "Prof") and the trailing letters after the period are
# preserved as part of the same token by the marker-replacement pass.
HONORIFICS_IT = (
    "Sig",
    "Sgr",
    "Dott",
    "Prof",
    "Avv",
    "Ing",
    "Geom",
    "Rag",
    "Arch",
    "On",
    "Egr",
    "Spett",
    "Gent",
    "Ill",
)

# Spanish.
HONORIFICS_ES = (
    "Sr",
    "Sra",
    "Sres",
    "Sras",
    "Srta",
    "Srtas",
    "Dr",
    "Dra",
    "Dres",
    "Lic",
    "Licda",
    "Ing",
    "Prof",
    "Profa",
    "Arq",
    "Mtro",
    "Mtra",
)

# German.
HONORIFICS_DE = (
    "Hr",
    "Fr",
    "Frl",
    "Dr",
    "Prof",
    "Dipl",
    "Mag",
)

# French.
HONORIFICS_FR = (
    "Mme",
    "Mmes",
    "Mlle",
    "Mlles",
    "MM",
    "Dr",
    "Pr",
    "Mgr",
    "Me",
)

# Portuguese (European + Brazilian).
HONORIFICS_PT = (
    "Sr",
    "Sra",
    "Srs",
    "Sras",
    "Srta",
    "Srtas",
    "Dr",
    "Dra",
    "Eng",
    "Enga",
    "Prof",
    "Profa",
)

# Mapping for callers who want to know which language ships which list.
HONORIFICS_BY_LANGUAGE: dict[str, tuple[str, ...]] = {
    "en": HONORIFICS_EN,
    "it": HONORIFICS_IT,
    "es": HONORIFICS_ES,
    "de": HONORIFICS_DE,
    "fr": HONORIFICS_FR,
    "pt": HONORIFICS_PT,
}

# Union of every language list, sorted longest-first so regex alternation
# prefers the most specific match (e.g. "Sras" before "Sr").
HONORIFICS_ALL: tuple[str, ...] = tuple(
    sorted(
        {p for prefixes in HONORIFICS_BY_LANGUAGE.values() for p in prefixes},
        key=lambda s: (-len(s), s),
    )
)

# Sentence-terminating characters. Includes Latin (`. ! ?`), full-width CJK
# (`。 ！ ？`), Japanese half-width (`｡`), full-width semicolon (`；`), full-
# width period (`．`), and Western ellipsis (`…`). Covers the most common
# multilingual response shapes.
_SENTENCE_TERMINATORS = ".!?…;。！？；．｡"

# Unambiguous non-Latin sentence terminators — punctuation that cannot also
# appear in numbers, abbreviations, or URLs in the script's typical usage,
# so a buffer ending in one of these can be flushed without a regex pass.
# Covers Hindi/Devanagari (। ॥), Arabic (؟ ؛ ۔ ؏), Armenian (։ ՜ ՞),
# Ethiopic (። ፧), Khmer (។ ៕), Burmese (။), Tibetan (༎ ༏), Thai (no
# terminator — relies on whitespace), and the Japanese half-width set we
# already have.
_UNAMBIGUOUS_NON_LATIN_TERMINATORS = "।॥؟؛۔؏։፧።។៕။༎༏"

# Pre-built regex character class covering both Latin/CJK and the
# non-Latin terminators above. Used by `_split_sentences` to mark `<stop>`.
_TERMINATOR_REGEX_CLASS = "".join(
    re.escape(c)
    for c in sorted(set(_SENTENCE_TERMINATORS + _UNAMBIGUOUS_NON_LATIN_TERMINATORS))
)

# "Soft" punctuation marks that terminate a clause but not a full sentence.
# These are candidates for the optional aggressive first-clause flush only.
# Includes em-dash (U+2014) and en-dash (U+2013); excludes ":" (often used
# as "Name: …" in LLM output) and ";" (rare in conversational speech).
_SOFT_TERMINATORS = ",—–"

# Default minimum buffer length before the aggressive first-clause flush is
# allowed to fire. Below ~40 chars TTS prosody suffers; ElevenLabs internally
# buffers up to 120 chars by default (``chunk_length_schedule``), so very
# short fragments are merged regardless of what we send.
DEFAULT_AGGRESSIVE_FIRST_MIN_LEN = 40

# Currency symbols that, when present near a comma, indicate the comma is a
# decimal/thousands separator and must not be treated as a clause boundary.
_CURRENCY_SYMBOLS = "$€£¥₹₩"

# Pre-built regex alternation for honorific prefixes (longest-first so that
# "Sras" matches before "Sra"). Kept at module scope so we build it once
# rather than every call to ``_split_sentences``.
_HONORIFICS_REGEX = "|".join(re.escape(p) for p in HONORIFICS_ALL)


def _split_sentences(
    text: str,
    *,
    min_sentence_len: int = DEFAULT_MIN_SENTENCE_LEN,
) -> list[tuple[str, int, int]]:
    """Split text into sentences using regex marker replacement.

    Returns a list of (sentence, start_pos, end_pos) tuples.
    Internal markers are chosen outside the input so literal marker text survives.
    """
    # Collect literal marker IDs once; membership checks then stay in this set.
    # At most one more ID than the number present is needed to find a free pair.
    marker_ids = {match.group(1) for match in re.finditer(r"<(?:prd|stop)_(\d+)>", text)}
    marker_id = 0
    while str(marker_id) in marker_ids:
        marker_id += 1
    prd, stop = f"<prd_{marker_id}>", f"<stop_{marker_id}>"

    alphabets = r"([A-Za-z])"
    # Title/honorific prefixes that take a trailing period. Sourced from the
    # union of every language list in ``HONORIFICS_BY_LANGUAGE`` (en / it /
    # es / de / fr / pt). The period after these is preserved (treated as
    # part of the word, not as sentence end).
    prefixes = rf"({_HONORIFICS_REGEX})[.]"
    # Suffix-style abbreviations: typically lowercase Italian ones (ecc, art,
    # pag, …) plus the existing English company-suffix list. English additions
    # from the NLTK Punkt training set: vs, etc, e.g., i.e., No, Vol, pp, cf, ca, op.
    suffixes = (
        r"(Inc|Ltd|Jr|Sr|Co|ecc|cit|cap|sez|art|pag|fig|tab|cfr|vol|ed|"
        r"vs|etc|No|Vol|pp|cf|ca|op|Mt|Hwy|Rt|Pl|Ave|Blvd|Sq)"
    )
    starters = (
        r"(Mr|Mrs|Ms|Dr|Prof|Capt|Cpt|Lt|He\s|She\s|It\s|They\s|Their\s|"
        r"Our\s|We\s|But\s|However\s|That\s|This\s|Wherever)"
    )
    acronyms = r"([A-Z][.][A-Z][.](?:[A-Z][.])?)"
    websites = r"[.](com|net|org|io|gov|edu|me)"
    digits = r"([0-9])"
    multiple_dots = r"\.{2,}"

    text = text.replace("\n", " ")

    text = re.sub(prefixes, rf"\1{prd}", text)
    text = re.sub(websites, rf"{prd}\1", text)
    text = re.sub(digits + r"[.]" + digits, rf"\1{prd}\2", text)
    text = re.sub(multiple_dots, lambda m: prd * len(m.group(0)), text)

    if "Ph.D" in text:
        text = text.replace("Ph.D.", f"Ph{prd}D{prd}")

    text = re.sub(r"\s" + alphabets + r"[.] ", rf" \1{prd} ", text)
    text = re.sub(acronyms + r" " + starters, rf"\1{stop} \2", text)
    text = re.sub(
        alphabets + r"[.]" + alphabets + r"[.]" + alphabets + r"[.]",
        rf"\1{prd}\2{prd}\3{prd}",
        text,
    )
    text = re.sub(alphabets + r"[.]" + alphabets + r"[.]", rf"\1{prd}\2{prd}", text)
    # Preserve the period of the suffix abbreviation when it precedes a starter,
    # e.g. "Patter Inc. He left" → keep "Inc." in the emitted sentence.
    text = re.sub(r" " + suffixes + r"[.] " + starters, rf" \1.{stop} \2", text)
    text = re.sub(r" " + suffixes + r"[.]", rf" \1{prd}", text)
    text = re.sub(r" " + alphabets + r"[.]", rf" \1{prd}", text)

    # Mark sentence-ending punctuation (Latin + CJK + non-Latin scripts).
    text = re.sub(rf"([{_TERMINATOR_REGEX_CLASS}])([\"\u201d])", rf"\1\2{stop}", text)
    text = re.sub(rf"([{_TERMINATOR_REGEX_CLASS}])(?![\"\u201d])", rf"\1{stop}", text)

    # Restore periods
    text = text.replace(prd, ".")

    splitted = text.split(stop)
    text = text.replace(stop, "")

    sentences: list[tuple[str, int, int]] = []
    buff = ""
    start_pos = 0
    end_pos = 0

    for match in splitted:
        sentence = match.strip()
        if not sentence:
            continue

        buff += " " + sentence
        end_pos += len(match)

        if len(buff) > min_sentence_len:
            sentences.append((buff.lstrip(), start_pos, end_pos))
            start_pos = end_pos
            buff = ""

    if buff:
        sentences.append((buff.lstrip(), start_pos, len(text) - 1))

    return sentences


class SentenceChunker:
    """Accumulates streaming tokens and yields complete sentences.

    Usage::

        chunker = SentenceChunker()
        for token in llm_stream:
            for sentence in chunker.push(token):
                await tts.synthesize(sentence)
        for sentence in chunker.flush():
            await tts.synthesize(sentence)
    """

    def __init__(
        self,
        *,
        min_sentence_len: int = DEFAULT_MIN_SENTENCE_LEN,
        min_words_for_short_flush: int = DEFAULT_MIN_WORDS_FOR_SHORT_FLUSH,
        aggressive_first_flush: bool = False,
        aggressive_first_min_len: int = DEFAULT_AGGRESSIVE_FIRST_MIN_LEN,
        language: str = "en",
    ) -> None:
        self._buffer = ""
        self._min_sentence_len = min_sentence_len
        self._min_words_for_short_flush = min_words_for_short_flush
        self._aggressive_first_min_len = aggressive_first_min_len
        self._language = (language or "en").lower()
        # Italian uses comma as decimal separator (3,14) and dot as thousands
        # separator (1.000) — both invert the English convention. Aggressive
        # comma-flush would split decimals, so we hard-disable it for Italian
        # regardless of caller preference.
        self._aggressive_first_flush = (
            aggressive_first_flush and not self._language.startswith("it")
        )
        self._is_first_flush = True

    def push(self, token: str) -> list[str]:
        """Feed a token. Returns zero or more complete sentences.

        Two emission paths:

        * **Standard path** — when the buffer is at least ``min_sentence_len``
          characters long and the regex tokenizer reports more than one
          sentence, all but the last (potentially incomplete) sentence are
          emitted.
        * **Short-flush path** — when the buffer is shorter than
          ``min_sentence_len`` but ends with a sentence terminator AND the
          preceding text has at least ``min_words_for_short_flush`` words
          (default 1 — single-word replies like ``"Yes."`` flush immediately
          for low TTS TTFB). Acronym ("U.S.") and decimal ("f(x) = 2.")
          guards still block dangerous cases. Bump
          ``min_words_for_short_flush`` to 2+ if you want the legacy
          behaviour where single-word utterances stay buffered until
          ``flush()``.
        """
        self._buffer += token

        # Aggressive first-clause flush: when enabled, emit the first clause
        # of the response on a soft punctuation boundary (",", em/en-dash) as
        # soon as enough characters accumulate. Saves 200-500 ms TTFA on the
        # first sentence of each turn. Subsequent sentences fall through to
        # the standard sentence-boundary path.
        if self._aggressive_first_flush and self._is_first_flush:
            flushed = self._maybe_aggressive_first_flush()
            if flushed is not None:
                self._is_first_flush = False
                return [flushed]

        if len(self._buffer) < self._min_sentence_len:
            return self._maybe_short_flush()

        sentences = _split_sentences(
            self._buffer, min_sentence_len=self._min_sentence_len
        )

        if len(sentences) <= 1:
            return []

        # Emit all sentences except the last (which may be incomplete)
        result: list[str] = []
        for sent_text, _, _ in sentences[:-1]:
            if sent_text.strip():
                result.append(sent_text.strip())

        # Keep the last (potentially incomplete) sentence in the buffer
        last_text = sentences[-1][0] if sentences else ""
        self._buffer = last_text

        if result:
            # A standard-path emission ends the "first flush" window too:
            # only the aggressive flush cleared the flag, so a comma in
            # sentence 2+ could still trigger a clause-level flush mid-turn
            # — choppy prosody, contradicting the documented "first clause
            # of each turn" contract.
            self._is_first_flush = False

        return result

    def _maybe_short_flush(self) -> list[str]:
        """Emit the buffer when it's a short, complete single-sentence utterance.

        A buffer qualifies when **all** of these hold:

        1. Last non-whitespace char is a sentence terminator.
        2. Word count is at least ``min_words_for_short_flush`` (default 1 —
           single-word replies like ``"Yes."`` flush immediately).
        3. The buffer contains exactly one terminator (the trailing one).
           Multiple terminators mean we may be mid-stream of a longer merged
           utterance like ``"Hey! Hi! Hello! This is a sentence."`` — let
           the standard path keep merging.
        4. The char immediately before the terminator is **not** a digit
           (avoids decimal mid-stream like ``"f(x) = x * 2."`` flushing
           before the ``54`` arrives).
        5. The trailing word is **not** a short ASCII all-caps acronym of
           1-3 chars (``"U."`` / ``"U.S."`` / ``"USA."``) — those are
           likely abbreviation periods, not sentence ends.
        6. The trailing word is **not** a known honorific from any of the
           per-language ``HONORIFICS_*`` constants (``"Mr."``, ``"Sr."``,
           ``"Dr."``, ``"Hr."``, ``"Mme."``, ...) — those signal a name
           continuation, not a sentence end.

        Together these gates preserve the merging behaviour of the standard
        path while letting genuine short greetings flush immediately for low
        TTS TTFB.
        """
        stripped = self._buffer.rstrip()
        if not stripped or stripped[-1] not in _SENTENCE_TERMINATORS:
            return []

        # Only one terminator in the entire buffer (the trailing one).
        if sum(1 for c in stripped if c in _SENTENCE_TERMINATORS) != 1:
            return []

        # Word count: ``"Hi there!".split()`` -> 2.
        word_count = len(stripped.split())
        if word_count < self._min_words_for_short_flush:
            return []

        # Don't flush on potential decimals.
        if len(stripped) >= 2:
            prev = stripped[-2]
            if prev.isdigit():
                return []
            # Don't flush on short all-caps acronyms ("U.", "US.", "USA.") —
            # these are likely abbreviation periods, not sentence ends. Only
            # block if the trailing word is **purely uppercase** AND **at most
            # 3 chars** (matches U/US/USA/NATO patterns without dots; longer
            # all-caps words like RAMESH or SPEAKING are real sentences and
            # must still be allowed to flush).
            terminator = stripped[-1]
            last_word = (
                stripped.rstrip(_SENTENCE_TERMINATORS).split()[-1]
                if stripped.rstrip(_SENTENCE_TERMINATORS).split()
                else ""
            )
            if (
                terminator == "."
                and last_word.isascii()
                and last_word.isupper()
                and len(last_word) <= 3
            ):
                return []
            # Don't flush when the trailing token is a known honorific — the
            # next token will be a name (e.g. "Mr. Theo" / "Sr. García" /
            # "Hr. Müller"). Only applies to "." since "Hi!" / "Yes?" never
            # name-continue.
            if terminator == "." and last_word in HONORIFICS_ALL:
                return []

        self._buffer = ""
        self._is_first_flush = False
        return [stripped]

    def _maybe_aggressive_first_flush(self) -> str | None:
        """Try to flush the first clause of the response on a soft punctuation
        boundary (comma / em-dash / en-dash) to minimise TTFA.

        Returns the flushed clause text (terminator stripped) or ``None`` if
        no safe boundary is found. All of these guards must pass:

        1. **Min length** — buffer ≥ ``aggressive_first_min_len`` (default 40).
        2. **Trailing terminator** — last non-whitespace char in
           ``_SOFT_TERMINATORS``.
        3. **Decimal/thousands guard** — refuse if the comma is between two
           digits (``3,14``) or surrounded by digit-thousands grouping.
        4. **Currency guard** — refuse if a currency symbol appears in the
           preceding 8 characters (``€1.000,50``).
        5. **Balanced delimiter** — refuse if open parens/brackets/braces or
           unmatched double-quotes still pending (avoids splitting JSON,
           parenthetical asides, quoted speech).
        6. **Ellipsis** — refuse if buffer ends with ``...`` or ``…`` (it's an
           intentional pause, not a clause boundary).
        7. **Sub-token ambiguity** — only fire when at least one trailing char
           after the terminator has arrived OR the terminator is followed by
           whitespace (avoids firing mid-token when next char might extend
           the number/abbreviation).
        """
        rstripped = self._buffer.rstrip()
        if len(rstripped) < self._aggressive_first_min_len:
            return None

        last_char = rstripped[-1]
        if last_char not in _SOFT_TERMINATORS:
            return None

        pos = len(rstripped) - 1

        # Sub-token ambiguity: require at least one char (whitespace or other)
        # in the original buffer after the terminator. Without this we may
        # fire mid-decimal before the next digit arrives.
        if pos + 1 >= len(self._buffer):
            return None
        next_char = self._buffer[pos + 1]

        # Decimal/thousands guard for comma: refuse if surrounded by digits.
        if last_char == ",":
            prev_char = rstripped[pos - 1] if pos >= 1 else ""
            if prev_char.isdigit() and next_char.isdigit():
                return None
            # Also refuse for "1,000" thousands separator pattern: digit-comma-
            # whitespace-or-end is OK only when not in a number context. Be
            # conservative — if a digit immediately precedes the comma and the
            # last 4 chars contain another comma in a digit context, skip.
            tail = rstripped[max(0, pos - 6) : pos]
            if prev_char.isdigit() and ("," in tail and any(c.isdigit() for c in tail)):
                return None

        # Currency guard: any currency symbol in the trailing 8 chars before
        # the terminator suggests a number context.
        snippet = rstripped[max(0, pos - 8) : pos]
        if any(c in snippet for c in _CURRENCY_SYMBOLS):
            return None

        # Balanced delimiter guard.
        opens = sum(rstripped.count(c) for c in "([{")
        closes = sum(rstripped.count(c) for c in ")]}")
        if opens > closes:
            return None
        # Odd number of double-quotes ⇒ inside a quoted span; don't split.
        if rstripped.count('"') % 2 != 0:
            return None

        # Ellipsis guard.
        if rstripped.endswith("...") or rstripped.endswith("…"):
            return None

        # Comma-before-quote guard (orphan fragment).
        if last_char == "," and next_char == '"':
            return None

        # All guards passed. Emit the clause and trim the buffer.
        flushed = rstripped
        self._buffer = self._buffer[len(rstripped) :].lstrip()
        return flushed

    def flush(self) -> list[str]:
        """Flush remaining buffer as final sentence(s). Call at end of stream."""
        remaining = self._buffer.strip()
        self._buffer = ""
        self._is_first_flush = True

        if not remaining:
            return []

        return [remaining]

    def reset(self) -> None:
        """Discard buffered text. Call on interrupt."""
        self._buffer = ""
        self._is_first_flush = True
