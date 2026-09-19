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


#: Edge-silence trim defaults: engines pad each render with their own lead-in
#: and tail (GPT-SoVITS ~70 ms / ~300 ms, others similar). Left in, every
#: chunk or line boundary becomes a hole; trimmed, the join is decided by the
#: deliberate gaps the caller asks for. -40 dBFS is well below speech and above
#: the noise floor of a clean render; 40 ms keeps a natural onset/decay.
DEFAULT_TRIM_THRESHOLD_DB = -40.0
DEFAULT_TRIM_KEEP_MS = 40


def trim_edge_silence(audio, sample_rate: int, *, threshold_db: float = DEFAULT_TRIM_THRESHOLD_DB,
                      keep_ms: int = DEFAULT_TRIM_KEEP_MS):
    """Strip near-silent lead-in and tail from one rendered chunk.

    Works on the last axis of a 1-D or (channels, samples) tensor. Keeps
    ``keep_ms`` of the quiet edge on both sides so onsets and decays are not
    clipped. A chunk that is silent throughout is returned unchanged (its
    caller decides what an empty render means).
    """
    import torch

    if audio is None or audio.shape[-1] == 0:
        return audio
    level = audio.abs()
    if level.dim() > 1:
        level = level.reshape(-1, level.shape[-1]).amax(dim=0)
    threshold = 10.0 ** (threshold_db / 20.0)
    loud = torch.nonzero(level > threshold).flatten()
    if loud.numel() == 0:
        return audio
    keep = int(sample_rate * keep_ms / 1000)
    start = max(0, int(loud[0]) - keep)
    end = min(audio.shape[-1], int(loud[-1]) + 1 + keep)
    if start == 0 and end == audio.shape[-1]:
        return audio
    return audio[..., start:end]


_PARAGRAPH_BREAK = re.compile(r"\n[ \t]*\n+")


def split_paragraphs(text: str) -> List[str]:
    """Paragraphs of *text* (blank-line separated), each stripped, empties dropped."""
    return [p.strip() for p in _PARAGRAPH_BREAK.split(text or "") if p and p.strip()]


def split_text_into_chunks(text: str, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> List[str]:
    """Split *text* at natural boundaries into chunks of at most *max_chars*.

    Priority: sentence-end (``.!?`` not after an abbreviation/decimal and not
    inside brackets, plus fullwidth equivalents) -> clause boundary
    (``;:,`` / em dash) -> whitespace -> hard cut that avoids splitting a
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

        split_pos = _find_last_sentence_end(segment)
        if split_pos == -1:
            split_pos = _find_last_clause_boundary(segment)
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


def _find_last_sentence_end(text: str) -> int:
    """Index of the last sentence-ending punctuation, or -1.

    Skips periods after common abbreviations and decimals, anything inside
    a bracket tag, and also recognizes fullwidth sentence punctuation
    (ideographic full stop / fullwidth ! and ?) for no-space scripts.
    """
    best = -1
    for m in re.finditer(r"[.!?](?:\s|$)", text):
        pos = m.start()
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
    # Fullwidth sentence enders (ideographic full stop, fullwidth !, ?)
    # written as escapes to keep the repo's no-literal-CJK gate clean.
    for m in re.finditer("[\u3002\uff01\uff1f]", text):
        if m.start() > best:
            best = m.start()
    return best


def _find_last_clause_boundary(text: str) -> int:
    best = -1
    for m in re.finditer(r"[;:,—](?:\s|$)", text):
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
            detail = " — no audio for: " + "; ".join(repr(t[:80]) for t in named)
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
    dropped = [i for i, r in enumerate(rendered)
               if r is None or getattr(r, "shape", (0,))[-1] == 0]
    kept = [r for i, r in enumerate(rendered) if i not in set(dropped)]
    if trim_edges:
        # The engine's own lead-in/tail would otherwise become a hole at every
        # chunk boundary; the caller adds the gaps it actually wants.
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
    return concatenate_audio_chunks(rendered, sample_rate,
                                    crossfade_ms=crossfade_ms, texts=texts,
                                    sink=sink)


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
    result = chunks[0]

    for chunk in chunks[1:]:
        chunk = chunk.to(device=result.device, dtype=result.dtype)
        overlap = min(crossfade_samples, result.shape[-1], chunk.shape[-1])
        if overlap > 0:
            fade_out = torch.linspace(1.0, 0.0, overlap, dtype=result.dtype, device=result.device)
            fade_in = torch.linspace(0.0, 1.0, overlap, dtype=result.dtype, device=result.device)
            blended = result[..., -overlap:] * fade_out + chunk[..., :overlap] * fade_in
            result = torch.cat([result[..., :-overlap], blended, chunk[..., overlap:]], dim=-1)
        else:
            result = torch.cat([result, chunk], dim=-1)

    return result
