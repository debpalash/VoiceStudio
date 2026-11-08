"""Audiobook creator — chapterized long-form narration (parity Wave 5).

Turns a chapter-delimited script into a chapterized audiobook. This module is
the engine-agnostic core:

  * ``parse_audiobook_script`` — pure parser: Markdown ``# H1`` headings become
    chapters; inline ``[voice:NAME]`` switches the narrator; ``[pause …]`` is
    delegated to the existing :func:`omnivoice.utils.text.parse_pause_markers`
    so audiobooks and single-shot synthesis share one pause dialect.
  * ``synthesize_chapter`` — orchestration: renders a chapter's spans through an
    injected ``synth(text, voice_id) -> tensor`` callable (reusing the
    ``chunked_tts`` splitter + crossfade), stitching the inter-span silences.
    Injecting the synth keeps this unit-testable with a stub backend (no torch
    model, no GPU).
  * ``build_chapter_ffmetadata`` / ``build_m4b_cmd`` — pure builders for the
    ffmpeg chapterized-m4b mux (FFMETADATA1 ``[CHAPTER]`` blocks + concat-demux
    argv). The actual ffmpeg run lives in the (impure) caller.

Scope (first cut): plain chapter-delimited text/Markdown input. epub/pdf
ingestion, the streaming synth job + UI are deferred follow-ups.
"""

from __future__ import annotations

import json
import zlib
from dataclasses import dataclass, field
from typing import Callable, ClassVar, Optional


#: Mix constant for the per-occurrence seed nonce (#1208) — a large odd
#: multiplier (Knuth) so occurrence 0/1/2 land in well-separated regions of the
#: 2**31 seed space instead of adjacent integers.
_NONCE_MIX = 2654435761


def segment_seed(base_seed: int, text: str, nonce: int = 0) -> int:
    """Deterministic RNG seed for one longform synthesis call (#1139).

    A voice profile's pinned ``seed`` (locked takes, design profiles) makes
    ``/generate`` reproducible, but the longform path used to fetch the seed
    and never apply it — book renders were unseeded, so a profile pinned for
    consistency still drifted between fresh renders. Deriving the per-call
    seed from ``base_seed`` + a CRC of the chunk text mirrors ``/generate``'s
    per-chunk decorrelation (``used_seed + i``) while staying order- and
    cache-independent: a partially cached chapter re-renders its missing
    segments with the exact seeds a full render would have used. Pure —
    torch-free — so the router's synth wrappers stay unit-testable.

    Text-keyed on purpose: identical repeated lines get identical takes.
    That is already the longform pipeline's shipped semantic — the
    content-addressed SegmentCache (longform_render.segment_cache_key hashes
    text + voice sig, not position) replays one WAV for every identical span
    — and it only applies when the user pinned a seed, i.e. asked for
    reproducibility. Position-based keys would break it: inserting one
    paragraph would shift every later span's seed, so a partial re-render
    after an edit would no longer match the original render.

    ``nonce`` (default 0 — the shipped text-keyed behaviour, byte-identical)
    is the cache opt-out lever (#1208): when the user asks to *vary repeated
    lines*, the synth wrapper feeds a per-occurrence nonce so each repeat of an
    identical pinned-seed line gets a distinct-but-deterministic seed instead
    of replaying one take.
    """
    return (int(base_seed) + zlib.crc32(text.encode("utf-8")) + int(nonce) * _NONCE_MIX) % (2**31)


@dataclass(frozen=True)
class ExpressiveOptions:
    """Optional expressive/quality knobs for a longform render (#1208).

    Every field is ``None``/``False`` by default, and a default instance means
    *reproduce today's bytes exactly*: the audiobook/longform path renders at
    its documented quality preset (num_step 32, guidance 2.0, model-default
    temperatures, postprocess on) with no emotion and the shipped
    content-addressed caching. Any non-default field is folded into every cache
    signature via :meth:`cache_signature` (chapter cache, segment cache, and the
    preview cache all consume it) so a changed setting can never silently replay
    stale audio — the whole point of the CRITICAL TRAP guard.

    ``emo_*`` reach only engines that understand them (IndexTTS2) through the
    generic synth closure; the VoiceStudio model rejects unknown config kwargs, so
    the omnivoice path forwards only the sampling knobs. ``vary_repeats`` is the
    cache opt-out: identical lines get distinct takes.
    """

    num_step: Optional[int] = None
    guidance_scale: Optional[float] = None
    position_temperature: Optional[float] = None
    class_temperature: Optional[float] = None
    postprocess_output: Optional[bool] = None
    seed: Optional[int] = None
    emo_vector: Optional[tuple] = None
    emo_text: Optional[str] = None
    emo_alpha: Optional[float] = None
    vary_repeats: bool = False
    line_gap_ms: int = 0
    paragraph_gap_ms: int = 0
    trim_edges: bool = False

    JOIN_KEYS: ClassVar[tuple[str, str, str]] = (
        "line_gap_ms",
        "paragraph_gap_ms",
        "trim_edges",
    )

    def join_kwargs(self) -> dict:
        """Return the chapter-stitching options as keyword arguments."""
        return {name: getattr(self, name) for name in self.JOIN_KEYS}

    @property
    def is_default(self) -> bool:
        """True when every knob is untouched → today's exact render + caching."""
        return self == ExpressiveOptions()

    def cache_signature(self) -> str:
        """Deterministic content string folded into every cache key. Empty for a
        default instance (so unset → byte-identical keys to pre-#1208). Includes
        EVERY field, so a future forgotten knob still perturbs the key (the
        regression test loops over the fields asserting each changes this)."""
        if self.is_default:
            return ""
        payload = {
            "num_step": self.num_step,
            "guidance_scale": self.guidance_scale,
            "position_temperature": self.position_temperature,
            "class_temperature": self.class_temperature,
            "postprocess_output": self.postprocess_output,
            "seed": self.seed,
            "emo_vector": list(self.emo_vector) if self.emo_vector else None,
            "emo_text": self.emo_text,
            "emo_alpha": self.emo_alpha,
            "vary_repeats": self.vary_repeats,
        }
        # Added only when active, so an all-zero instance keeps the old key.
        joins = self.join_kwargs()
        if any(joins.values()):
            payload.update(joins)
        return json.dumps(payload, sort_keys=True, ensure_ascii=False)

    def to_manifest(self) -> dict:
        """JSON-safe dict for the durable resume manifest (emo_vector → list)."""
        return {
            "num_step": self.num_step,
            "guidance_scale": self.guidance_scale,
            "position_temperature": self.position_temperature,
            "class_temperature": self.class_temperature,
            "postprocess_output": self.postprocess_output,
            "seed": self.seed,
            "emo_vector": list(self.emo_vector) if self.emo_vector else None,
            "emo_text": self.emo_text,
            "emo_alpha": self.emo_alpha,
            "vary_repeats": self.vary_repeats,
            **self.join_kwargs(),
        }

    @classmethod
    def from_manifest(cls, data: Optional[dict]) -> "ExpressiveOptions":
        """Rebuild options saved by :meth:`to_manifest`; empty input gives defaults.

        Keys the manifest does not know about are ignored, so older and newer
        manifests both load.
        """
        if not data:
            return cls()
        vector = data.get("emo_vector")
        return cls(
            num_step=data.get("num_step"),
            guidance_scale=data.get("guidance_scale"),
            position_temperature=data.get("position_temperature"),
            class_temperature=data.get("class_temperature"),
            postprocess_output=data.get("postprocess_output"),
            seed=data.get("seed"),
            emo_vector=tuple(vector) if vector else None,
            emo_text=data.get("emo_text"),
            emo_alpha=data.get("emo_alpha"),
            vary_repeats=bool(data.get("vary_repeats", False)),
            line_gap_ms=int(data.get("line_gap_ms") or 0),
            paragraph_gap_ms=int(data.get("paragraph_gap_ms") or 0),
            trim_edges=bool(data.get("trim_edges", False)),
        )


def voice_map_signature(voice_map: Optional[dict]) -> str:
    """Deterministic cache-key fragment for a name→profile voice map (#1217).

    A longform render can carry a ``voice_map`` (``[voice:NAME]`` → profile id);
    remapping a name must re-render, so the map is folded into every cache key
    exactly like :meth:`ExpressiveOptions.cache_signature`. Empty for
    ``None``/``{}`` (so an absent map keeps today's byte-identical keys and
    existing books never re-render); else canonical JSON over string keys."""
    if not voice_map:
        return ""
    return json.dumps({str(k): v for k, v in voice_map.items()},
                      sort_keys=True, ensure_ascii=False)


@dataclass
class Span:
    """One contiguous run of text in a single voice, plus trailing silence.

    ``speed`` (when set) is the per-span rate passed to the engine — Stories'
    per-line speed slider rides through here so the shared server render honours
    it the way the old client export did.
    """
    voice_id: Optional[str]
    text: str
    pause_ms_after: int = 0
    speed: Optional[float] = None
    # How this span meets the next one when markup split a single line:
    # "continue" (no gap) or "paragraph" (blank line on the boundary).
    join: Optional[str] = None
    # Free-text delivery direction from ``[emotion:]`` (#3422); applied only by
    # engines whose ``expression_caps`` take emotion, never spoken.
    emotion: Optional[str] = None

    def to_dict(self) -> dict:
        out = {"voice_id": self.voice_id, "text": self.text,
               "pause_ms_after": self.pause_ms_after, "speed": self.speed}
        if self.join:
            out["join"] = self.join
        if self.emotion:
            out["emotion"] = self.emotion
        return out


@dataclass
class Chapter:
    title: str
    spans: list[Span] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(len(s.text) for s in self.spans)

    def to_dict(self) -> dict:
        return {"title": self.title, "char_count": self.char_count,
                "spans": [s.to_dict() for s in self.spans]}


@dataclass
class AudiobookPlan:
    chapters: list[Chapter] = field(default_factory=list)

    @property
    def char_count(self) -> int:
        return sum(c.char_count for c in self.chapters)

    @property
    def chapter_count(self) -> int:
        return len(self.chapters)

    def to_dict(self) -> dict:
        return {
            "chapters": [c.to_dict() for c in self.chapters],
            "chapter_count": self.chapter_count,
            "char_count": self.char_count,
        }


def parse_audiobook_script(text: str, *, default_voice: Optional[str] = None) -> AudiobookPlan:
    """Parse a chapter-delimited script into an :class:`AudiobookPlan`.

    Thin wrapper over the canonical :func:`services.longform_parser.
    parse_script_to_spans` (the single grammar source of truth, #27); wraps its
    span dicts in the ``Span``/``Chapter``/``AudiobookPlan`` dataclasses so the
    four router call sites and ``.to_dict()`` shape are unchanged.
    """
    from services.longform_parser import parse_script_to_spans

    chapters = [
        Chapter(title=c["title"], spans=[Span(**s) for s in c["spans"]])
        for c in parse_script_to_spans(text, default_voice=default_voice)
    ]
    return AudiobookPlan(chapters=chapters)


# Most silence the join stage may add to one chapter (15 minutes). Read at call
# time; explicit [pause] silence is not counted against it.
MAX_JOIN_SILENCE_MS = 15 * 60 * 1000


class ChapterRenderCancelled(RuntimeError):
    """The chapter's GPU job was abandoned; stopped between chunks (#2287)."""


def _stop_if_abandoned() -> None:
    """Stop a chapter whose caller gave up, before it starts another chunk.

    A whole chapter runs as ONE GPU-pool job. When its budget runs out or the
    client disconnects, the pool guard stops waiting but cannot kill the
    thread, which then renders the rest of the chapter while holding the device
    (#2287). The guard cancels the job's scope, so check it between chunks.
    Finished spans are already in the segment cache, so a retry or resume picks
    up from there.
    """
    from services.inference_cancellation import current_cancellation

    scope = current_cancellation()
    if scope is not None and scope.cancelled.is_set():
        raise ChapterRenderCancelled(
            "Audiobook chapter stopped: its GPU job was abandoned (timed out or "
            "the client disconnected). Finished segments are cached."
        )


def _note_chunk_done() -> None:
    """Tell the pool guard that this chapter just finished a chunk (#2287).

    Without this signal, a long chapter that was still rendering chunk after
    chunk timed out as "too heavy for the available compute" when it reached
    its text-scaled budget. Same signal as /generate's (#1391). Never raises.
    """
    try:
        from services.model_manager import report_generate_progress

        report_generate_progress()
    except Exception:  # noqa: BLE001 — a liveness signal must not break a render
        pass


class _JoinPolicy:
    """How a chapter's spoken spans are stitched: the two gap lengths (ms)."""

    def __init__(self, line_ms: int, paragraph_ms: int, pause_fill: str = "silence") -> None:
        self.line_ms = line_ms
        self.paragraph_ms = paragraph_ms
        self.pause_fill = pause_fill

    def silence_after(self, span: Span) -> int:
        """Gap (ms) owed once *span* has been spoken and before any following audio."""
        if span.pause_ms_after > 0:
            return 0  # the explicit pause stands in for the gap
        marker = span.join
        if marker == "continue":
            return 0
        if marker == "paragraph":
            return self.paragraph_ms or self.line_ms
        return self.line_ms

    def pieces(self, text: str) -> list[str]:
        """Texts rendered separately for one span (paragraphs when a paragraph gap is set)."""
        if self.paragraph_ms <= 0:
            return [text]
        from services.chunked_tts import split_paragraphs

        return split_paragraphs(text) or [text]

    def enforce_budget(self, spans: list[Span], texts: list[str]) -> None:
        """Raise ValueError when the silence this policy would add exceeds the limit."""
        added = 0
        carried = 0
        for span, text in zip(spans, texts):
            if span.text:
                inner = (len(self.pieces(text)) - 1) * self.paragraph_ms
                added += carried + inner
                carried = self.silence_after(span)
            if span.pause_ms_after > 0:
                carried = 0
        if added > MAX_JOIN_SILENCE_MS:
            minutes = MAX_JOIN_SILENCE_MS // 60000
            raise ValueError(
                f"The join silence requested for this chapter is over the {minutes}-minute "
                f"limit. Lower the line and paragraph gaps, or split the chapter."
            )

    def stitch(self, renders: list, sample_rate: int):
        """Hard-join the paragraph renders of one span with the paragraph gap between them."""
        import torch
        from services.chunked_tts import concatenate_audio_chunks

        present = [r for r in renders if r is not None]
        if len(present) < 2:
            return present[0] if present else None
        from services.audio_dsp import fill_gaps

        width = int(sample_rate * self.paragraph_ms / 1000.0)
        items: list = []
        for r in present[:-1]:
            items.append(("a", r))
            if width > 0:
                items.append(("s", width))
        items.append(("a", present[-1]))
        # Digital silence or room tone, per Settings (#3611).
        return concatenate_audio_chunks(
            fill_gaps(items, sample_rate, self.pause_fill), sample_rate, crossfade_ms=0)


def synthesize_chapter(
    spans: list[Span],
    synth: Callable[[str, Optional[str], Optional[float]], "object"],
    sample_rate: int,
    *,
    crossfade_ms: int = 50,
    line_gap_ms: int = 0,
    paragraph_gap_ms: int = 0,
    trim_edges: bool = False,
    lexicon: Optional[dict] = None,
    segment_cache: Optional["object"] = None,
    on_progress: Optional[Callable[..., None]] = None,
    pause_fill: Optional[str] = None,
):
    """Render a chapter's spans to one waveform via an injected ``synth``.

    ``synth(text, voice_id, speed[, emotion=])`` returns a float32 audio tensor — 1-D
    ``(samples,)`` or ``(channels, samples)``; real engines emit ``(1, samples)``
    per the ``TTSBackend`` contract (#897) — for a span of text in the given
    voice (``speed`` may be ``None`` for the engine default; ``emotion`` is
    passed only for a span with an ``[emotion:]`` direction). Long spans are split with the ``chunked_tts`` splitter and
    crossfaded; inter-span ``pause_ms_after`` becomes silence. ``lexicon`` (when
    given) respells each span's text before chunking so the engine pronounces
    tricky words correctly; a ``None``/empty lexicon is a no-op pass-through.
    ``segment_cache`` (when given — a :class:`services.longform_render.
    SegmentCache`) is consulted per spoken span: a cached segment WAV is reused
    instead of synthesizing, and every freshly rendered span is stored the
    moment it finishes — so a one-sentence edit re-renders one segment and an
    interrupted chapter resumes from its finished segments. Pauses are
    synthesized silence and never touch the cache.

    ``on_progress(done, total, audio_s=..., cached=...)`` (when given) is called
    after every synthesized chunk and every span served from the segment cache,
    with ``done``/``total`` in characters of this chapter's spoken text and
    ``audio_s`` the seconds of audio that step produced (#3417). It runs on the
    rendering thread; a failing callback never breaks the render.

    Join settings (all default to the old hard-join behaviour): ``line_gap_ms``
    / ``paragraph_gap_ms`` put silence between consecutive spoken spans (see
    ``Span.join`` for markup splits), ``trim_edges`` strips each chunk's
    engine-added lead-in/tail first, and a positive ``paragraph_gap_ms`` also
    renders the paragraphs inside one span separately. An explicit
    ``pause_ms_after`` replaces the gap after its span. The total added silence
    is capped by ``MAX_JOIN_SILENCE_MS`` and checked before any work starts.

    Returns ``(audio_tensor, duration_seconds)``. torch + chunked_tts are
    imported lazily so this module stays import-light for the pure parser path.
    """
    import torch
    from core.render_trace import call as trace_call
    from services.chunked_tts import (concatenate_audio_chunks,
                                      join_rendered_chunks,
                                      split_text_into_chunks)
    from services.pronunciation import apply_lexicon

    items: list = []  # ("a", tensor) for audio, ("s", n_samples) for silence
    texts_by_span = [apply_lexicon(span.text, lexicon) if span.text else "" for span in spans]
    # Read the gap fill once (#3611): the caller keys its caches on the same
    # value, so a switch flipped mid-render cannot mix fills under one key.
    if pause_fill is None:
        from services.audio_dsp import pause_fill_mode

        pause_fill = pause_fill_mode()
    policy = _JoinPolicy(line_gap_ms, paragraph_gap_ms, pause_fill)
    paragraphs_by_span = [policy.pieces(t) if t else [] for t in texts_by_span]
    policy.enforce_budget(spans, texts_by_span)
    pending_gap_ms = 0  # silence owed before the next piece of audio
    # Per-occurrence index for identical spans (#1208 cache opt-out). The
    # segment cache folds it into its key ONLY when vary_repeats is on (else
    # the key is byte-identical to pre-#1208), so a repeated identical line
    # gets a distinct cache slot — and therefore a distinct take — instead of
    # replaying one WAV. Always computed (cheap); inert when the cache ignores it.
    occ_counts: dict = {}
    progress_total = sum(len(p) for paras in paragraphs_by_span for p in paras)
    progress_done = 0

    def report(done: int, audio_s: float, cached: bool) -> None:
        if on_progress is None:
            return
        try:
            on_progress(min(done, progress_total), progress_total,
                        audio_s=audio_s, cached=cached)
        except Exception:  # noqa: BLE001 — progress is advisory
            pass

    for span, paragraphs in zip(spans, paragraphs_by_span):
        span_end = progress_done + sum(len(p) for p in paragraphs)
        if span.text:
            occ_key = (span.voice_id, span.text, getattr(span, "speed", None),
                       getattr(span, "emotion", None))
            occ = occ_counts.get(occ_key, 0)
            occ_counts[occ_key] = occ + 1
            audio = trace_call("cache", segment_cache.load, span, nonce=occ) if segment_cache is not None else None
            if audio is None:
                # Emotion is passed only when set, so a synth without emotion
                # support (and every untagged book) is called exactly as before.
                emotion = getattr(span, "emotion", None)
                direction = {"emotion": emotion} if emotion else {}
                paragraph_renders = []
                for paragraph in paragraphs:
                    chunks = split_text_into_chunks(paragraph)
                    rendered = []
                    for c in chunks:
                        _stop_if_abandoned()
                        out = trace_call("synthesis", synth, c, span.voice_id, span.speed,
                                         **direction)
                        rendered.append(out)
                        _note_chunk_done()
                        progress_done = min(span_end, progress_done + len(c))
                        shape = getattr(out, "shape", None)
                        report(progress_done,
                               (shape[-1] / float(sample_rate)) if shape else 0.0, False)
                    # Deliberately NOT pre-filtered (#1330). Dropping the empties
                    # here both hid them — a chapter would come back short with
                    # nothing said about it — and misaligned `rendered` from
                    # `chunks`, so the concat could not name which text was lost.
                    paragraph_renders.append(
                        join_rendered_chunks(rendered, sample_rate,
                                             crossfade_ms=crossfade_ms,
                                             texts=chunks, trim_edges=trim_edges))
                audio = policy.stitch(paragraph_renders, sample_rate)
                if audio is not None and segment_cache is not None:
                    trace_call("cache", segment_cache.store, span, audio, nonce=occ)
                if span_end > progress_done:
                    # Whitespace the splitter dropped: settle the span without
                    # feeding a zero-length "chunk" into the rate.
                    progress_done = span_end
                    report(progress_done, 0.0, True)
            elif span_end > progress_done:
                progress_done = span_end
                report(progress_done, 0.0, True)
            if audio is not None:
                gap_samples = int(sample_rate * pending_gap_ms / 1000.0)
                if gap_samples > 0:
                    items.append(("s", gap_samples))
                items.append(("a", audio))
                pending_gap_ms = policy.silence_after(span)
        progress_done = span_end
        if span.pause_ms_after > 0:
            pending_gap_ms = 0
            n = int(sample_rate * span.pause_ms_after / 1000.0)
            if n > 0:
                items.append(("s", n))

    if not items:
        return torch.zeros(0, dtype=torch.float32), 0.0
    # Engines return (1, samples) per the TTSBackend contract while a bare
    # zeros(n) is 1-D — mixing the two crashed the final concat (#897). So
    # materialize inter-span silence AFTER the loop, matching the rendered
    # audio's channel dims / dtype / device (same pattern as generation.py's
    # _render_with_pauses). A silence-only chapter stays 1-D float32 as before.
    # Gaps are digital silence or room tone, per Settings (#3611).
    from services.audio_dsp import fill_gaps

    parts: list = fill_gaps(items, sample_rate, pause_fill)
    # Hard-concat spans + silences (crossfading silence would bleed the gap).
    audio = parts[0] if len(parts) == 1 else concatenate_audio_chunks(parts, sample_rate, crossfade_ms=0)
    return audio, audio.shape[-1] / float(sample_rate)


# ── ffmpeg / metadata builders ──────────────────────────────────────────────
#
# These now live in the shared ``longform_render`` core (Stories + Audiobook
# converge on one mux). The thin wrappers below preserve the original
# audiobook-only call sites/signatures; new callers should use
# ``longform_render`` directly to reach global metadata, cover art, loudness,
# and mp3 output.
from services.longform_render import (  # noqa: E402
    build_concat_list,
    build_ffmetadata,
    build_render_cmd,
)


def build_chapter_ffmetadata(chapters: list[tuple[str, int]]) -> str:
    """Backward-compatible alias: chapters-only FFMETADATA (no global tags)."""
    return build_ffmetadata(chapters)


def build_m4b_cmd(
    ffmpeg: str,
    concat_list_path: str,
    metadata_path: str,
    out_path: str,
    *,
    bitrate: str = "128k",
) -> list[str]:
    """Backward-compatible alias: a chapterized faststart m4b, no cover/loudness."""
    return build_render_cmd(
        ffmpeg, concat_list_path, metadata_path, out_path,
        fmt="m4b", bitrate=bitrate,
    )
