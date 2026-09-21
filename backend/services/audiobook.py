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
from typing import Callable, Optional


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
    #: Seamless joins (#2216): trim each render's own lead-in/tail, then add
    #: deliberate silence — ``line_gap_ms`` between consecutive lines that carry
    #: no explicit ``[pause]``, ``paragraph_gap_ms`` at a blank line inside one
    #: line. Zero/False = today's bytes (hard joins with engine padding kept).
    line_gap_ms: int = 0
    paragraph_gap_ms: int = 0
    trim_edges: bool = False

    #: Manifest keys that shape the join, not the engine call — never forward
    #: these as synth kwargs.
    JOIN_KEYS = ("line_gap_ms", "paragraph_gap_ms", "trim_edges")

    def join_kwargs(self) -> dict:
        """The subset of options :func:`synthesize_chapter` takes directly."""
        return {k: getattr(self, k) for k in self.JOIN_KEYS}

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
        # Keep pre-join-control keys for every legacy render, including ones
        # with a seed or emotion override (not only the all-default instance).
        if self.line_gap_ms or self.paragraph_gap_ms or self.trim_edges:
            payload.update(self.join_kwargs())
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
            "line_gap_ms": self.line_gap_ms,
            "paragraph_gap_ms": self.paragraph_gap_ms,
            "trim_edges": self.trim_edges,
        }

    @classmethod
    def from_manifest(cls, data: Optional[dict]) -> "ExpressiveOptions":
        """Rebuild from a resume manifest dict (unknown keys ignored)."""
        if not data:
            return cls()
        ev = data.get("emo_vector")
        return cls(
            num_step=data.get("num_step"),
            guidance_scale=data.get("guidance_scale"),
            position_temperature=data.get("position_temperature"),
            class_temperature=data.get("class_temperature"),
            postprocess_output=data.get("postprocess_output"),
            seed=data.get("seed"),
            emo_vector=tuple(ev) if ev else None,
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
    #: How this span joins the NEXT one when inline markup ([slow], [emphasis],
    #: [spell]…) split one run of text into several spans. ``"continue"``: the
    #: sentence runs straight on — no gap. ``"paragraph"``: a blank line sat on
    #: the markup boundary — paragraph gap. ``None`` (every ordinary span): the
    #: line ends here — line gap. Emitted only when set, so every existing plan,
    #: manifest and cache key is byte-identical.
    join: Optional[str] = None

    def to_dict(self) -> dict:
        d = {"voice_id": self.voice_id, "text": self.text,
             "pause_ms_after": self.pause_ms_after, "speed": self.speed}
        if self.join:
            d["join"] = self.join
        return d


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


#: Ceiling on the silence the join stage may ADD to one chapter. A real chapter
#: needs a few minutes at most (hundreds of paragraphs x 0.6 s); without a
#: ceiling a body of thousands of one-word paragraphs with a maxed-out gap
#: allocates gigabytes of zeros. Explicit [pause] markers are the script's own
#: and are not drawn from this budget.
MAX_JOIN_SILENCE_MS = 15 * 60 * 1000


def _gap_after_span(span: Span, line_gap_ms: int, paragraph_gap_ms: int) -> int:
    if span.pause_ms_after > 0 or span.join == "continue":
        return 0
    if span.join == "paragraph":
        return paragraph_gap_ms or line_gap_ms
    return line_gap_ms


def _join_with_gap(parts: list, sample_rate: int, gap_ms: int):
    """Hard-concat rendered paragraphs with ``gap_ms`` of silence between them."""
    import torch

    parts = [p for p in parts if p is not None and p.shape[-1] > 0]
    if not parts:
        return None
    if len(parts) == 1:
        return parts[0]
    from services.chunked_tts import concatenate_audio_chunks

    n = int(sample_rate * gap_ms / 1000.0)
    ref = parts[0]
    out: list = []
    for i, part in enumerate(parts):
        if i and n > 0:
            out.append(torch.zeros(*ref.shape[:-1], n, dtype=ref.dtype, device=ref.device))
        out.append(part)
    return concatenate_audio_chunks(out, sample_rate, crossfade_ms=0)


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
):
    """Render a chapter's spans to one waveform via an injected ``synth``.

    ``synth(text, voice_id, speed)`` returns a float32 audio tensor — 1-D
    ``(samples,)`` or ``(channels, samples)``; real engines emit ``(1, samples)``
    per the ``TTSBackend`` contract (#897) — for a span of text in the given
    voice (``speed`` may be ``None`` for the engine default). Long spans are split with the ``chunked_tts`` splitter and
    crossfaded; inter-span ``pause_ms_after`` becomes silence. ``lexicon`` (when
    given) respells each span's text before chunking so the engine pronounces
    tricky words correctly; a ``None``/empty lexicon is a no-op pass-through.
    ``segment_cache`` (when given — a :class:`services.longform_render.
    SegmentCache`) is consulted per spoken span: a cached segment WAV is reused
    instead of synthesizing, and every freshly rendered span is stored the
    moment it finishes — so a one-sentence edit re-renders one segment and an
    interrupted chapter resumes from its finished segments. Pauses are
    synthesized silence and never touch the cache.

    Returns ``(audio_tensor, duration_seconds)``. torch + chunked_tts are
    imported lazily so this module stays import-light for the pure parser path.
    """
    import torch
    from core.render_trace import call as trace_call
    from services.chunked_tts import (concatenate_audio_chunks,
                                      join_rendered_chunks,
                                      split_text_into_chunks)
    from services.pronunciation import apply_lexicon

    from services.chunked_tts import split_paragraphs

    items: list = []  # ("a", tensor) for audio, ("s", n_samples) for silence
    pending_gap_ms = 0  # join silence owed before the next spoken span
    # Validate the complete requested silence before touching synthesis/cache.
    # Incremental shortening would bake context-dependent gaps into reusable
    # segment audio, and cache hits could evade the chapter's silence budget.
    paragraphs_by_span = []
    planned_gap_ms = 0
    total_join_ms = 0
    for span in spans:
        text = apply_lexicon(span.text, lexicon) if span.text else ""
        paragraphs = (split_paragraphs(text) if paragraph_gap_ms > 0 else []) or [text]
        if not span.text:
            paragraphs = []
        paragraphs_by_span.append(paragraphs)
        if span.text:
            total_join_ms += planned_gap_ms + max(0, len(paragraphs) - 1) * paragraph_gap_ms
            if total_join_ms > MAX_JOIN_SILENCE_MS:
                raise ValueError(
                    "Requested join silence exceeds 15 minutes in one chapter; "
                    "reduce the line/paragraph gaps or split the chapter."
                )
            planned_gap_ms = _gap_after_span(span, line_gap_ms, paragraph_gap_ms)
        if span.pause_ms_after > 0:
            planned_gap_ms = 0
    # Per-occurrence index for identical spans (#1208 cache opt-out). The
    # segment cache folds it into its key ONLY when vary_repeats is on (else
    # the key is byte-identical to pre-#1208), so a repeated identical line
    # gets a distinct cache slot — and therefore a distinct take — instead of
    # replaying one WAV. Always computed (cheap); inert when the cache ignores it.
    occ_counts: dict = {}
    for span, paragraphs in zip(spans, paragraphs_by_span):
        if span.text:
            occ_key = (span.voice_id, span.text, getattr(span, "speed", None))
            occ = occ_counts.get(occ_key, 0)
            occ_counts[occ_key] = occ + 1
            audio = trace_call("cache", segment_cache.load, span, nonce=occ) if segment_cache is not None else None
            if audio is None:
                # A blank line inside one span is a paragraph break: render
                # each paragraph on its own so the join can put a deliberate
                # gap there instead of running the paragraphs together.
                # With no paragraph gap asked for, the span stays ONE engine
                # call — the pre-existing bytes, seeds and prosody.
                rendered_paragraphs = []
                for paragraph in paragraphs:
                    chunks = split_text_into_chunks(paragraph)
                    rendered = [trace_call("synthesis", synth, c, span.voice_id, span.speed) for c in chunks]
                    # Deliberately NOT pre-filtered (#1330). Dropping the empties
                    # here both hid them — a chapter would come back short with
                    # nothing said about it — and misaligned `rendered` from
                    # `chunks`, so the concat could not name which text was lost.
                    joined = join_rendered_chunks(rendered, sample_rate,
                                                  crossfade_ms=crossfade_ms,
                                                  texts=chunks, trim_edges=trim_edges)
                    if joined is not None:
                        rendered_paragraphs.append(joined)
                audio = _join_with_gap(
                    rendered_paragraphs, sample_rate,
                    paragraph_gap_ms)
                if audio is not None and segment_cache is not None:
                    trace_call("cache", segment_cache.store, span, audio, nonce=occ)
            if audio is not None:
                if pending_gap_ms > 0:
                    n = int(sample_rate * pending_gap_ms / 1000.0)
                    if n > 0:
                        items.append(("s", n))
                items.append(("a", audio))
                # What follows this span: nothing in the middle of a line that
                # inline markup split, the paragraph gap where a blank line sat
                # on that split (line gap if no paragraph gap is set), else the
                # line gap. An explicit [pause] below replaces any of them.
                pending_gap_ms = _gap_after_span(span, line_gap_ms, paragraph_gap_ms)
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
    ref = next((t for kind, t in items if kind == "a"), None)
    parts: list = [
        val if kind == "a"
        else (torch.zeros(val, dtype=torch.float32) if ref is None
              else torch.zeros(*ref.shape[:-1], val, dtype=ref.dtype, device=ref.device))
        for kind, val in items
    ]
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
