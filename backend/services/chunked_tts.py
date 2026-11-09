"""Chunked TTS generation utilities (Wave 1.2 — unlimited-length generation).

Adapted from voicebox (https://github.com/jamiepine/voicebox), MIT License,
Copyright (c) voicebox contributors. The concatenation half is reworked for
torch tensors (our inference helpers pass raw model output — possibly
multi-channel — to the effect chain), and the sample rate comes from the
engine's declared rate rather than the first chunk (fixes a latent upstream
bug where a mid-run rate change was silently ignored).

Splits long text into sentence-boundary chunks and joins the per-chunk audio
with a short crossfade. Pure functions — the generation loop itself lives in
``api/routers/generation.py`` next to the existing ``[pause]`` span stitcher,
so this module stays unit-testable without a model.

Short text (<= max_chunk_chars) never reaches this module's concat path; the
callers keep their unchanged single-shot fast path.
"""

from __future__ import annotations

import logging

from core.render_trace import timed as _render_timed
import re
from typing import List

logger = logging.getLogger("omnivoice.chunked_tts")

# Default chunk size in characters. 0 disables chunking entirely.
DEFAULT_MAX_CHUNK_CHARS = 800

# Default crossfade between chunks. 0 = hard cut.
DEFAULT_CROSSFADE_MS = 50

# Common abbreviations that should NOT be treated as sentence endings.
# Lowercase for case-insensitive matching.
_ABBREVIATIONS = frozenset({
    "mr", "mrs", "ms", "dr", "prof", "sr", "jr", "st", "ave", "blvd",
    "inc", "ltd", "corp", "dept", "est", "approx", "vs", "etc",
    "e.g", "i.e", "a.m", "p.m", "u.s", "u.s.a", "u.k",
})

# Non-Latin sentence enders: Arabic full stop (Urdu "khatma"), Arabic question
# mark, Devanagari danda / double danda. Without them an Urdu, Persian, Arabic
# or Hindi script over the chunk limit was cut at an arbitrary space
# mid-sentence. They never mark abbreviations or decimals, and are matched
# with or without a following space.
_SCRIPT_SENTENCE_ENDERS = "\u06d4\u061f\u0964\u0965"
# Arabic comma and semicolon — the clause-boundary fallback for those scripts.
_SCRIPT_CLAUSE_MARKS = "\u060c\u061b"

# Inline bracket tags (paralinguistic tags like [laugh]; our own
# [pause 300ms] markers). The splitter must never cut inside one.
_BRACKET_TAG_RE = re.compile(r"\[[^\]]*\]")

# Dense scripts (CJK ideographs, kana, Hangul) where ~1 character = 1 syllable,
# so an N-char chunk is far more *speech* than N Latin chars. Counted by code
# point (see _dense_char_count) so there are no literal CJK chars in source.
def _dense_char_count(text: str) -> int:
    """Number of CJK / kana / Hangul characters in *text* (dense scripts)."""
    n = 0
    for ch in text:
        o = ord(ch)
        if (0x3040 <= o <= 0x30FF or 0x3400 <= o <= 0x4DBF
                or 0x4E00 <= o <= 0x9FFF or 0xAC00 <= o <= 0xD7AF
                or 0xF900 <= o <= 0xFAFF):
            n += 1
    return n

# A chunk that is predominantly dense-script (>= this fraction) gets the smaller
# limit; below it, the text is mostly spaced/Latin and the full limit applies.
_DENSE_FRACTION_THRESHOLD = 0.3
# Speech-per-char multiplier for dense scripts vs Latin (~1 ideograph ≈ 2.5
# Latin chars of audio). Used to scale the char limit down.
_DENSE_SPEECH_FACTOR = 2.5


def _effective_max_chars(text: str, max_chars: int) -> int:
    """Scale *max_chars* down for dense-script text (#505).

    Long-form (5+ min) generation degrades — repeated / skipped / mispronounced
    words — when a single chunk's acoustic sequence gets too long. With CJK /
    kana / Hangul, ~1 char = 1 syllable, so an 800-char chunk is ~4-5 minutes of
    audio in one shot, well past the model's reliable range. When a chunk is
    predominantly dense-script, cap it to ``max_chars / _DENSE_SPEECH_FACTOR``
    (floored) so each chunk's spoken length stays bounded. Latin / spaced text
    is unchanged. ``max_chars <= 0`` (chunking disabled) is left untouched.
    """
    if max_chars <= 0 or not text:
        return max_chars
    dense = _dense_char_count(text)
    if dense and dense / len(text) >= _DENSE_FRACTION_THRESHOLD:
        return max(120, min(max_chars, round(max_chars / _DENSE_SPEECH_FACTOR)))
    return max_chars


DEFAULT_TRIM_THRESHOLD_DB = -40.0
DEFAULT_TRIM_KEEP_MS = 40

_PARAGRAPH_BREAK = re.compile(r"\r?\n[ \t\r]*\n(?:[ \t\r]*\n)*")


def trim_edge_silence(audio, sample_rate, *, threshold_db=DEFAULT_TRIM_THRESHOLD_DB,
                      keep_ms=DEFAULT_TRIM_KEEP_MS):
    """Cut the quiet lead-in and tail an engine adds around a render.

    Works on the last axis of a 1-D or (channels, samples) tensor. A short
    margin (``keep_ms``) of quiet audio is left on each side so the speech does
    not start or stop abruptly. Silent or empty input comes back untouched, and
    so does audio with nothing to cut (the same object).
    """
    if audio is None or audio.shape[-1] == 0:
        return audio
    level = 10 ** (threshold_db / 20)
    magnitude = audio.abs()
    if magnitude.dim() > 1:
        magnitude = magnitude.reshape(-1, magnitude.shape[-1]).amax(dim=0)
    loud = (magnitude > level).nonzero()
    if loud.numel() == 0:
        return audio
    margin = int(sample_rate * keep_ms / 1000)
    total = audio.shape[-1]
    start = max(0, int(loud[0].item()) - margin)
    end = min(total, int(loud[-1].item()) + 1 + margin)
    if start == 0 and end == total:
        return audio
    return audio[..., start:end]


def split_paragraphs(text) -> List[str]:
    """Break text into paragraphs at blank lines; a lone newline stays inside."""
    if not text:
        return []
    parts = (p.strip() for p in _PARAGRAPH_BREAK.split(text))
    return [p for p in parts if p]


def split_text_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> List[str]:
    """Split *text* at natural boundaries into chunks of at most *max_chars*.

    Priority: sentence-end (``.!?`` not after an abbreviation/decimal and not
    inside brackets, plus fullwidth CJK, Arabic-script and Devanagari enders)
    -> clause boundary (``;:,`` / em dash / Arabic comma and semicolon)
    -> whitespace -> hard cut that avoids splitting a
    ``[tag]``.
    """
    text = text.strip()
    if not text:
        return []
    # #505: dense-script text packs far more speech per char, so cap the chunk
    # smaller to keep each chunk's spoken length in the model's reliable range.
    max_chars = _effective_max_chars(text, max_chars)
    if max_chars <= 0 or len(text) <= max_chars:
        return [text]

    chunks: List[str] = []
    remaining = text

    while remaining:
        remaining = remaining.lstrip()
        if not remaining:
            break
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break

        segment = remaining[:max_chars]
        # #3062: boundary detection must see one character PAST the window.
        # The punctuation regexes accept end-of-string as "followed by
        # whitespace", so judged on the window alone, a window that happened to
        # end inside "3,5" (decimal comma), "e.g.", "site.com" or "..." looked
        # like a clause/sentence end and the token was cut in two — each half
        # then spoken on its own at the chunk boundary ("tres" + "cinco"
        # instead of "tres virgula cinco"). With the lookahead, the end of the
        # window is never a boundary unless the original text agrees.
        window = remaining[: max_chars + 1]

        split_pos = _find_last_sentence_end(window, limit=max_chars)
        if split_pos == -1:
            split_pos = _find_last_clause_boundary(window, limit=max_chars)
        if split_pos == -1:
            split_pos = segment.rfind(" ")
        if split_pos == -1:
            split_pos = _safe_hard_cut(segment, max_chars)

        chunk = remaining[: split_pos + 1].strip()
        if chunk:
            chunks.append(chunk)
        remaining = remaining[split_pos + 1:]

    return _merge_unspeakable(chunks, max_chars)


#: A character that can actually be voiced — any letter or digit, in any
#: script. Punctuation, brackets, quotes and dashes are not speech on their own.
_SPEAKABLE_RE = re.compile(r"[^\W_]", re.UNICODE)


def _merge_unspeakable(chunks: List[str], max_chars: int = 0) -> List[str]:
    """Fold chunks with nothing to say into their neighbour (#1330).

    A boundary can land so that the tail becomes a chunk of pure punctuation —
    ``'.'``, ``'"'``, ``'—'``, ``'...'``. Measured: text of 799 filler chars
    plus ``' ...'`` splits into ``['aaa…', '...']``.

    Sending that to an engine is at best a wasted GPU job, and at worst the
    engine returns no audio for it — which is indistinguishable from the
    silent-truncation bug this module now reports out loud. Users would get
    "part of your text produced no audio — '...'" for a chunk that never
    carried any speech, which teaches them to ignore a warning that exists to
    catch real data loss.

    The punctuation is not dropped: it is appended to the previous chunk (or
    prepended to the next, when it comes first), so the text the engine sees is
    unchanged in content and the join still covers every character.

    ``max_chars`` keeps that fold honest. Appending blindly would push the
    previous chunk past the caller's ceiling — 799 characters plus ``"..."``
    is 803 — and ``len(chunk) <= max_chars`` is an invariant the splitter's own
    tests assert (CodeRabbit). When the fold would overflow, the last WORD of
    the previous chunk moves across instead, so the fragment carries speech of
    its own and both chunks stay inside the limit.

    That is not always possible: the neighbour may be a single word, or its
    last word may itself be punctuation (borrowing it would just produce a
    second silent chunk — measured: ``"longer." + "." -> ". ."``). In those
    cases the fold wins and the chunk runs over, but **only ever by non-speech
    characters** — measured worst case, 3. ``max_chars`` bounds how much SPEECH
    a chunk holds (#505, the acoustic-degradation limit), and trailing
    punctuation is not speech, so the guarantee that matters is intact.
    """
    if len(chunks) < 2:
        return chunks
    out: List[str] = []
    for chunk in chunks:
        if _SPEAKABLE_RE.search(chunk) or not out:
            out.append(chunk)
            continue
        # Rejoin with a space: these were separated by whitespace the splitter
        # stripped, and gluing "word" to "..." would change the token the
        # engine sees.
        merged = f"{out[-1]} {chunk}"
        if max_chars <= 0 or len(merged) <= max_chars:
            out[-1] = merged
            continue
        # Overflow: hand the previous chunk's last word to the fragment. The
        # fragment then carries speech and stands on its own.
        head, sep, last_word = out[-1].rpartition(" ")
        # The borrowed word must itself carry speech, or the "fixed" chunk is
        # just as silent as the one being folded ("longer." + "." -> ". .").
        if sep and head and _SPEAKABLE_RE.search(last_word):
            out[-1] = head
            out.append(f"{last_word} {chunk}")
        else:
            # A single-word chunk has nothing to give; keeping the fragment
            # attached is still better than emitting a silent one, and the
            # overflow is a few punctuation characters.
            out[-1] = merged
    # A leading unspeakable chunk had nothing before it to merge into; fold it
    # forward instead so it still never renders alone.
    if len(out) > 1 and not _SPEAKABLE_RE.search(out[0]):
        merged = f"{out[0]} {out[1]}"
        if max_chars <= 0 or len(merged) <= max_chars:
            out[1] = merged
            out.pop(0)
        else:
            # Same trade as above, mirrored: borrow the next chunk's first word.
            first_word, sep, tail = out[1].partition(" ")
            if sep and tail and _SPEAKABLE_RE.search(first_word):
                out[0] = f"{out[0]} {first_word}"
                out[1] = tail
            else:
                out[1] = merged
                out.pop(0)
    return out


def _find_last_sentence_end(text: str, limit: int | None = None) -> int:
    """Index of the last sentence-ending punctuation, or -1.

    Skips periods after common abbreviations and decimals, anything inside
    a bracket tag, and also recognizes fullwidth sentence punctuation
    (ideographic full stop / fullwidth ! and ?) and the Arabic-script /
    Devanagari enders in ``_SCRIPT_SENTENCE_ENDERS``, with or without a
    following space.

    ``limit`` — only positions ``< limit`` count. Callers pass a window one
    character longer than the chunk so the character after a candidate is the
    real next character, not an artificial end-of-string (#3062).
    """
    if limit is None:
        limit = len(text)
    best = -1
    for m in re.finditer(r"[.!?](?:\s|$)", text):
        pos = m.start()
        if pos >= limit:
            continue
        if text[pos] == ".":
            word_start = pos - 1
            while word_start >= 0 and text[word_start].isalpha():
                word_start -= 1
            word = text[word_start + 1: pos].lower()
            if word in _ABBREVIATIONS:
                continue
            if word_start >= 0 and text[word_start].isdigit():
                continue
        if _inside_bracket_tag(text, pos):
            continue
        best = pos
    # Script-specific sentence enders that need no following space (writers
    # often omit it): fullwidth CJK (written as escapes to keep the repo's
    # no-literal-CJK gate clean), plus _SCRIPT_SENTENCE_ENDERS.
    for m in re.finditer("[\u3002\uff01\uff1f" + _SCRIPT_SENTENCE_ENDERS + "]", text):
        if best < m.start() < limit and not _inside_bracket_tag(text, m.start()):
            best = m.start()
    return best


def _find_last_clause_boundary(text: str, limit: int | None = None) -> int:
    """Index of the last clause punctuation (``;:,`` / em dash / Arabic
    comma and semicolon), or -1.

    ``limit`` as in :func:`_find_last_sentence_end`.
    """
    if limit is None:
        limit = len(text)
    best = -1
    for m in re.finditer(r"[;:,—](?:\s|$)|[" + _SCRIPT_CLAUSE_MARKS + "]", text):
        if m.start() >= limit:
            continue
        if _inside_bracket_tag(text, m.start()):
            continue
        best = m.start()
    return best


def _inside_bracket_tag(text: str, pos: int) -> bool:
    for m in _BRACKET_TAG_RE.finditer(text):
        if m.start() < pos < m.end():
            return True
    return False


def _safe_hard_cut(segment: str, max_chars: int) -> int:
    cut = max_chars - 1
    for m in _BRACKET_TAG_RE.finditer(segment):
        if m.start() < cut < m.end():
            return m.start() - 1 if m.start() > 0 else cut
    return cut


def _normalize_chunk_shapes(chunks: list) -> list:
    """Coerce mixed-rank / mixed-channel chunks to one concat-compatible shape.

    Engines return ``(1, samples)`` per the ``TTSBackend.generate`` contract,
    but silence buffers and some model paths hand over bare ``(samples,)``
    tensors — ``torch.cat`` then dies with "Tensors must have same number of
    dimensions" (#897). Promote lower-rank chunks with leading singleton dims
    to the highest rank present, then broadcast singleton channel dims up to
    the widest channel count (mono follows stereo). Rank-homogeneous,
    channel-homogeneous input is returned untouched, so all-1-D / all-2-D
    callers keep their exact output shape; a genuine channel conflict
    (e.g. 2 vs 3 channels) still raises, which is the honest outcome.
    """
    target = max(c.dim() for c in chunks)
    if any(c.dim() != target for c in chunks):
        promoted = []
        for c in chunks:
            while c.dim() < target:
                c = c.unsqueeze(0)
            promoted.append(c)
        chunks = promoted
    if target > 1:
        lead = tuple(max(c.shape[i] for c in chunks) for i in range(target - 1))
        chunks = [c if tuple(c.shape[:-1]) == lead else c.expand(*lead, -1)
                  for c in chunks]
    return chunks


def report_dropped_chunks(dropped: list, total: int, texts=None, sink=None) -> None:
    """Log the sentences that produced no audio. Never raises.

    Deliberately WARNING, not debug: this is missing output the user paid
    compute for.

    ``sink`` — an optional list the caller owns. The lost text lands in it so
    the *user* can be told too, which the log alone never did: a log line
    nobody reads is not a fix for silent truncation, it is a record of it.
    Kept as an explicit parameter rather than a contextvar because the render
    runs on a plain ThreadPoolExecutor, which does not carry context across.
    """
    try:
        named = []
        if texts:
            named = [str(texts[i]) for i in dropped if 0 <= i < len(texts)]
        if sink is not None:
            try:
                sink.extend(named or [""] * len(dropped))
            except Exception:  # noqa: BLE001 — a caller's odd sink must not break the join
                pass
        detail = ""
        if named:
            # The log is user-shareable diagnostics: scrub the whole sentence
            # before truncating so a cut cannot split a secret past the
            # scrubber. The sink keeps the raw text for the user's own view.
            from core.scrub import scrub_text

            detail = " — no audio for: " + "; ".join(
                repr(scrub_text(t)[:80]) for t in named)
        logger.warning(
            "Dropped %d of %d rendered chunk(s): the engine returned no audio "
            "for them, so the output is missing that text%s. This is silent in "
            "the waveform — the result sounds clean and is simply short (#1330).",
            len(dropped), total, detail,
        )
    except Exception:  # noqa: BLE001 — a diagnostic must not break the join
        # The fallback cannot assume logging works either: whatever broke the
        # report above may be the logger. Losing the diagnostic is acceptable;
        # turning missing audio into a failed render is not.
        try:
            logger.exception("Could not report dropped audio chunks")
        except Exception:  # noqa: BLE001
            pass


def join_rendered_chunks(rendered: list, sample_rate: int, *,
                         crossfade_ms: int = DEFAULT_CROSSFADE_MS,
                         texts=None, sink=None, trim_edges: bool = False):
    """Join what a multi-chunk render produced, reporting whatever it lost.

    ``None`` when nothing rendered — the caller's dead-render handling owns
    that case, and returning a silence buffer instead would hide it.

    This exists because the "obvious" inline version has a hole that shipped:
    a span that splits into several chunks where only ONE renders was returned
    directly, skipping the join and therefore skipping the reporting the join
    does. The chapter came back short and said nothing about it — the same
    silent-truncation bug (#1330) one branch over. Keeping the decision in one
    function means there is one place that can be wrong, and it is testable.
    """
    def _is_empty(item) -> bool:
        shape = getattr(item, "shape", None)
        return item is None or shape is None or shape[-1] == 0

    # One pass to find the empties, one lookup set built once, one pass to keep.
    dropped = [i for i, item in enumerate(rendered) if _is_empty(item)]
    empty_idx = set(dropped)
    kept = [item for i, item in enumerate(rendered) if i not in empty_idx]
    if trim_edges:
        kept = [trim_edge_silence(r, sample_rate) for r in kept]
    if not kept:
        if dropped:
            report_dropped_chunks(dropped, len(rendered), texts, sink)
        return None
    if len(kept) == 1:
        # concatenate_audio_chunks short-circuits a single chunk without
        # reporting, so the report has to happen here.
        if dropped:
            report_dropped_chunks(dropped, len(rendered), texts, sink)
        return kept[0]
    if dropped:
        report_dropped_chunks(dropped, len(rendered), texts, sink)
    return concatenate_audio_chunks(kept, sample_rate,
                                    crossfade_ms=crossfade_ms)


@_render_timed('join')
def concatenate_audio_chunks(chunks: list, sample_rate: int,
                             crossfade_ms: int = DEFAULT_CROSSFADE_MS,
                             texts=None, sink=None):
    """Join per-chunk waveforms with a linear crossfade on the sample axis.

    ``chunks`` are torch tensors as returned by the engine (1-D, or N-D with
    samples on the last axis — matching what ``_render_with_pauses`` handles).
    Mixed ranks / mono-vs-multichannel chunks are normalized to one shape
    first (#897), so no producer can crash the concat. Crossfade overlap is
    clamped to the shorter neighbor; ``crossfade_ms=0`` is a hard concat.

    **Empty chunks are dropped, and that is now said out loud (#1330).** A
    chunk arrives empty when the engine returned nothing for that slice of
    text; skipping it is still the right joining behaviour, because the
    alternative is a crash or a gap. What was wrong was doing it in silence:
    the audio came back clean and simply missing a sentence, so the only way a
    user could notice was by reading along — which is exactly how it was
    reported ("this app dosent generate me the last few sentences"). The count
    now reaches the log, and callers that know the text can pass ``texts`` to
    have the dropped slices named.
    """
    import torch

    kept, dropped = [], []
    for i, c in enumerate(chunks):
        if c is not None and c.shape[-1] > 0:
            kept.append(c)
        else:
            dropped.append(i)
    if dropped:
        report_dropped_chunks(dropped, len(chunks), texts, sink)
    chunks = kept
    if not chunks:
        return torch.zeros(1, dtype=torch.float32)
    if len(chunks) == 1:
        return chunks[0]
    chunks = _normalize_chunk_shapes(chunks)

    crossfade_samples = int(sample_rate * crossfade_ms / 1000)

    # Plan from lengths alone: how much each chunk overlaps what is already
    # joined, and where it starts in the output.
    overlaps = []
    starts = []
    joined = chunks[0].shape[-1]
    for chunk in chunks[1:]:
        n = chunk.shape[-1]
        ov = max(0, min(crossfade_samples, joined, n))
        overlaps.append(ov)
        starts.append(joined - ov)
        joined += n - ov

    first = chunks[0]
    out = torch.empty((*first.shape[:-1], joined), dtype=first.dtype, device=first.device)
    out[..., : first.shape[-1]] = first
    for chunk, ov, start in zip(chunks[1:], overlaps, starts):
        chunk = chunk.to(device=out.device, dtype=out.dtype)
        if ov > 0:
            fade_out = torch.linspace(1, 0, ov, device=out.device, dtype=out.dtype)
            fade_in = torch.linspace(0, 1, ov, device=out.device, dtype=out.dtype)
            out[..., start : start + ov] = (
                out[..., start : start + ov] * fade_out + chunk[..., :ov] * fade_in
            )
        out[..., start + ov : start + chunk.shape[-1]] = chunk[..., ov:]
    return out
