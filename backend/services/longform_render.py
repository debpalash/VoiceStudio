"""Shared long-form render core (Stories + Audiobook convergence).

Both the Audiobook tab and the Stories Editor produce the *same* artifact: a
chapter-marked audio file built from chapter WAVs. This module owns the pure,
engine-agnostic ffmpeg/metadata builders for that mux so neither feature has to
reimplement it:

  * ``build_ffmetadata`` — FFMETADATA1 doc: an optional ``[global]`` tag block
    (title / author / narrator / year / genre / description) followed by one
    ``[CHAPTER]`` per (title, duration_ms).
  * ``build_concat_list`` — ffmpeg concat-demuxer list of chapter WAVs.
  * ``build_loudnorm_filter`` — an ``-af loudnorm=…`` string for an ACX /
    podcast loudness preset (off by default — opt-in, so the default-behavior
    stays platform-identical).
  * ``validate_cover_image`` — guard a cover path (type + size) before it
    reaches ffmpeg.
  * ``build_render_cmd`` — pure argv for the mux: chapter WAVs + FFMETADATA
    (+ optional cover art, loudness filter), output as ``m4b`` or ``mp3``.
  * ``chapter_cache_key`` — deterministic content hash so a re-run reuses
    already-rendered chapters (resume) and re-renders only what changed.
  * ``segment_cache_key`` / ``SegmentCache`` — the inner cache layer: each
    spoken span's WAV is content-addressed under ``<cache_dir>/segments`` so
    editing one sentence re-renders one segment (not the chapter) and an
    interrupted chapter render resumes from its finished segments.

The builders are pure (string/argv in, string/argv out) so they're unit tested
without ffmpeg, torch, or a GPU; the cache helpers (``prune_cache_dir``,
``SegmentCache``) touch only local files and import torch lazily. The impure
ffmpeg run lives in the caller (the audiobook router today; the stories job
tomorrow).
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

_BITRATE_RE = re.compile(r"^\d{2,3}k$")
#: Default ceiling for the content-addressed chapter cache. Above this, the
#: oldest cached chapter WAVs are evicted (LRU by mtime). Override via
#: OMNIVOICE_LONGFORM_CACHE_MAX_GB.
_CACHE_MAX_BYTES = int(float(os.environ.get("OMNIVOICE_LONGFORM_CACHE_MAX_GB", "2")) * 1024 ** 3)
_COVER_EXTS = {".jpg", ".jpeg", ".png"}
_COVER_MAX_BYTES = 8 * 1024 * 1024  # 8 MB — a book cover, not a payload

#: Our metadata field → FFMETADATA tag key. Order is stable for deterministic
#: output (tested). ``author`` maps to ``artist`` and ``narrator`` to
#: ``composer`` — the tags audiobook players (Apple Books, Audible) read for
#: those roles.
_GLOBAL_TAG_KEYS: list[tuple[str, str]] = [
    ("title", "title"),
    ("author", "artist"),
    ("album", "album"),
    ("narrator", "composer"),
    ("year", "date"),
    ("genre", "genre"),
    ("description", "comment"),
]


def _escape_meta(value: str) -> str:
    """Escape an FFMETADATA value (``=``, ``;``, ``#``, ``\\``, newline)."""
    return re.sub(r"([=;#\\\n])", r"\\\1", value or "")


def prune_cache_dir(cache_dir: str, max_bytes: int = _CACHE_MAX_BYTES) -> tuple[int, int]:
    """Evict the oldest files in ``cache_dir`` until the total size is within
    ``max_bytes`` (LRU by mtime). The content-addressed render cache otherwise
    grows without bound — uncompressed WAVs accumulate across every render.

    Walks the whole tree, so chapter WAVs at the root and segment WAVs under
    ``segments/`` share ONE byte budget — the cap holds no matter which layer
    grew. Best-effort: returns ``(remaining_bytes, removed_count)`` and never
    raises (a missing dir / unstattable file is just skipped). Call it *before*
    writing a job's files so the fresh ones are never the eviction target.
    """
    entries: list[tuple[float, int, str]] = []
    total = 0
    for root, _dirs, names in os.walk(cache_dir):
        for name in names:
            p = os.path.join(root, name)
            try:
                if not os.path.isfile(p):
                    continue
                size = os.path.getsize(p)
                mtime = os.path.getmtime(p)
            except OSError:
                continue
            entries.append((mtime, size, p))
            total += size
    if total <= max_bytes:
        return (total, 0)
    entries.sort()  # oldest first
    removed = 0
    for _mtime, size, p in entries:
        if total <= max_bytes:
            break
        try:
            os.remove(p)
            total -= size
            removed += 1
        except OSError:
            continue
    return (total, removed)


# ── Chapter cache key (resume) ──────────────────────────────────────────────

def chapter_cache_key(
    spans: Iterable[tuple],
    *,
    sample_rate: int,
    engine_id: str,
    voice_sig: Optional[dict] = None,
) -> str:
    """Deterministic content hash for a chapter's rendered audio.

    ``spans`` is an ordered list of ``(voice_id, text, pause_ms_after[, speed])``
    (speed optional, defaults to None). Same inputs → same key → reuse the
    cached chapter WAV on a re-run (resume); any change (text, voice, order,
    pauses, speed, sample rate, engine, or a voice's resolved signature) → new
    key → re-render. ``voice_sig`` maps each voice id to a stable signature
    string (e.g. ``ref_audio|instruct|seed``) so editing the underlying profile
    also invalidates the cache.
    """
    payload = {
        "sr": int(sample_rate),
        "engine": engine_id or "",
        "spans": [[s[0], s[1], int(s[2]), (s[3] if len(s) > 3 else None)] for s in spans],
        "voices": {k: voice_sig[k] for k in sorted(voice_sig)} if voice_sig else {},
    }
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # Content-addressing only — not a security digest. usedforsecurity=False
    # keeps bandit's B324 (weak-hash) check quiet.
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


# ── Segment cache (sub-chapter granularity) ─────────────────────────────────

#: Segment WAVs live in a subdirectory of the chapter cache dir so both layers
#: share one root — and one byte cap (``prune_cache_dir`` walks the tree).
SEGMENT_SUBDIR = "segments"


def segment_cache_key(
    text: str,
    *,
    sample_rate: int,
    engine_id: str,
    voice_id: Optional[str] = None,
    voice_sig: str = "",
    speed: Optional[float] = None,
    extra_sig: str = "",
    nonce: int = 0,
) -> str:
    """Deterministic content hash for ONE rendered segment (a single spoken
    span). Same dimensions as :func:`chapter_cache_key` minus span order and
    pauses (pauses are synthesized silence — never cached): text, voice
    identity (id + resolved signature), speed, sample rate, engine, plus
    ``extra_sig`` for anything else that changes the rendered audio (the
    pronunciation lexicon + the #1208 expressive signature). Any change → new
    key → re-synthesize just this segment.

    ``nonce`` (default 0 — omitted from the key, so pre-#1208 caches keep
    hitting) is the per-occurrence disambiguator the cache opt-out feeds so a
    repeated identical line gets a distinct segment instead of replaying one.
    """
    payload = {
        "sr": int(sample_rate),
        "engine": engine_id or "",
        "voice": voice_id or "",
        "text": text or "",
        "speed": speed,
        "voice_sig": voice_sig or "",
        "extra": extra_sig or "",
    }
    if nonce:
        # Absent when 0 so the derivation is byte-identical to pre-#1208 for
        # every normal (non-vary_repeats) render.
        payload["nonce"] = int(nonce)
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    # Content-addressing only — not a security digest (see chapter_cache_key).
    return hashlib.sha1(raw.encode("utf-8"), usedforsecurity=False).hexdigest()[:20]


class SegmentCache:
    """Content-addressed per-segment WAV store under ``cache_dir/segments``.

    The chapter cache stays the fast outer layer — a fully-unchanged chapter
    hits at the chapter key and never touches segment files. This inner layer
    makes a *changed* chapter cheap: only the edited/new segments synthesize
    (the rest load from disk), and an interrupted chapter render resumes from
    the segments that already finished, because each segment is persisted the
    moment it renders.

    ``voice_sig`` maps ``voice_id or ""`` → resolved-profile signature (same
    strings the chapter key uses) so a profile edit invalidates segments too.
    Load/store are best-effort: a missing/corrupt/foreign-rate file is a clean
    cache miss (re-render), and a failed store never fails the render — so
    caches written by any app version degrade safely. torch/torchaudio import
    lazily to keep this module import-light for the pure-builder callers.
    """

    def __init__(
        self,
        cache_dir: str,
        *,
        sample_rate: int,
        engine_id: str,
        voice_sig: Optional[dict] = None,
        extra_sig: str = "",
        vary_repeats: bool = False,
    ) -> None:
        self.dir = os.path.join(cache_dir, SEGMENT_SUBDIR)
        self.sample_rate = int(sample_rate)
        self.engine_id = engine_id or ""
        self.voice_sig = dict(voice_sig or {})
        self.extra_sig = extra_sig or ""
        # Cache opt-out (#1208): when on, a per-occurrence nonce enters the key
        # so identical repeated lines no longer share one WAV. Off → the nonce
        # is dropped and keys are byte-identical to pre-#1208 (default render).
        self.vary_repeats = bool(vary_repeats)
        self.hits = 0
        self.misses = 0

    def _path(self, span, nonce: int = 0) -> str:
        key = segment_cache_key(
            span.text,
            sample_rate=self.sample_rate,
            engine_id=self.engine_id,
            voice_id=span.voice_id,
            voice_sig=self.voice_sig.get(span.voice_id or "", ""),
            speed=getattr(span, "speed", None),
            extra_sig=self.extra_sig,
            nonce=nonce if self.vary_repeats else 0,
        )
        return os.path.join(self.dir, f"{key}.wav")

    def load(self, span, nonce: int = 0):
        """Cached audio tensor for ``span``, or ``None`` (miss). A hit bumps
        the file's mtime so LRU eviction sees the segment as recently used.
        ``nonce`` disambiguates repeated identical lines under the cache
        opt-out (inert otherwise)."""
        path = self._path(span, nonce)
        if not os.path.isfile(path):
            self.misses += 1
            return None
        try:
            import torchaudio
            audio, sr = torchaudio.load(path)
        except Exception:
            self.misses += 1
            return None  # unreadable/corrupt entry — clean miss, re-render
        if int(sr) != self.sample_rate or audio.numel() == 0:
            self.misses += 1
            return None  # foreign-rate/empty entry — clean miss, re-render
        try:
            os.utime(path, None)
        except OSError:
            pass
        self.hits += 1
        return audio

    def store(self, span, audio, nonce: int = 0) -> None:
        """Persist a freshly rendered segment. Best-effort — a full disk or
        unwritable cache dir must never fail the chapter render. ``nonce``
        matches :meth:`load` so a varied repeat lands in its own slot."""
        try:
            from services.audio_io import atomic_save_wav
            os.makedirs(self.dir, exist_ok=True)
            atomic_save_wav(self._path(span, nonce), audio, self.sample_rate)
        except Exception:
            pass


# ── Loudness normalization ──────────────────────────────────────────────────

@dataclass(frozen=True)
class LoudnessPreset:
    """A loudnorm target. ``i`` = integrated LUFS, ``tp`` = true-peak ceiling
    (dBTP), ``lra`` = loudness range."""
    key: str
    i: float
    tp: float
    lra: float


#: ``acx`` targets Audible/ACX submission (≈ -19 LUFS integrated, ≤ -3 dBTP
#: peak — inside ACX's -23…-18 dB RMS / -3 dB peak window). ``podcast`` targets
#: the -16 LUFS streaming norm.
LOUDNESS_PRESETS: dict[str, LoudnessPreset] = {
    "acx": LoudnessPreset("acx", -19.0, -3.0, 11.0),
    "podcast": LoudnessPreset("podcast", -16.0, -1.5, 11.0),
}


def build_loudnorm_filter(preset: Optional[str]) -> Optional[str]:
    """Return an ``-af`` loudnorm filter string for ``preset``, or ``None`` for
    off / unknown (single-pass; two-pass measure→apply is a runner enhancement).
    """
    if not preset:
        return None
    p = LOUDNESS_PRESETS.get(preset.lower())
    if p is None:  # "off", "none", or anything unrecognized → no filter
        return None
    return f"loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}"


@dataclass(frozen=True)
class MeasuredLoudness:
    """The five loudnorm measure-pass values (FFmpeg JSON keys), all finite
    floats. Fed back into the second (apply) pass as ``measured_*`` + ``offset``."""
    input_i: float
    input_tp: float
    input_lra: float
    input_thresh: float
    target_offset: float


def build_loudnorm_measure_filter(preset: Optional[str]) -> Optional[str]:
    """First-pass loudnorm filter (``print_format=json``) for ``preset``, or
    ``None`` for off/unknown — mirrors :func:`build_loudnorm_filter`'s lookup
    (no whitespace stripping) so the same values count as 'no filter'."""
    if not preset:
        return None
    p = LOUDNESS_PRESETS.get(preset.lower())
    if p is None:
        return None
    return f"loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}:print_format=json"


def parse_loudnorm_measure(stderr_text: Optional[str]) -> Optional[MeasuredLoudness]:
    """Extract the loudnorm measure JSON from ffmpeg stderr → MeasuredLoudness,
    or ``None`` on ANY failure (caller falls back to single-pass). FFmpeg prints
    the JSON object amid other non-JSON lines (and possibly a config dump block),
    so we take the LAST balanced ``{...}`` via a linear brace-depth scan — no
    regex (CodeQL-safe), O(n), no backtracking — then json.loads + coerce/validate
    the five required keys to finite floats."""
    if not stderr_text:
        return None
    # Find the last balanced top-level {...} block via a single linear scan.
    start = -1
    depth = 0
    block = None
    for i, ch in enumerate(stderr_text):
        if ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start != -1:
                    block = stderr_text[start:i + 1]  # keep scanning → last wins
    if block is None:
        return None
    try:
        obj = json.loads(block)
    except (json.JSONDecodeError, ValueError):
        return None
    if not isinstance(obj, dict):
        return None
    keys = ("input_i", "input_tp", "input_lra", "input_thresh", "target_offset")
    vals = {}
    for k in keys:
        if k not in obj:
            return None
        try:
            v = float(obj[k])
        except (TypeError, ValueError):
            return None
        if not math.isfinite(v):  # rejects "-inf"/"inf"/"nan" (silent clip)
            return None
        vals[k] = v
    return MeasuredLoudness(**vals)


def build_loudnorm_apply_filter(
    preset: Optional[str], measured: Optional["MeasuredLoudness"],
) -> Optional[str]:
    """Second-pass (apply) loudnorm filter feeding the measured values back in.
    ``None`` for off/unknown preset OR when ``measured`` is None (so a caller
    that forgot to branch never emits ``measured_I=None``)."""
    if not preset or measured is None:
        return None
    p = LOUDNESS_PRESETS.get(preset.lower())
    if p is None:
        return None
    return (
        f"loudnorm=I={p.i}:TP={p.tp}:LRA={p.lra}"
        f":measured_I={measured.input_i}:measured_TP={measured.input_tp}"
        f":measured_LRA={measured.input_lra}:measured_thresh={measured.input_thresh}"
        f":offset={measured.target_offset}:linear=true:print_format=summary"
    )


def build_loudnorm_measure_cmd(ffmpeg: str, concat_list_path: str, filt: str) -> list[str]:
    """Pure argv for the measure pass: decode the concat list, run the
    print_format=json loudnorm filter, discard audio to the portable null muxer.
    Input segment is byte-identical to build_render_cmd so measured == muxed."""
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "info",
        "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
        "-af", filt, "-f", "null", "-",
    ]


# ── FFMETADATA ──────────────────────────────────────────────────────────────

def build_ffmetadata(
    chapters: Iterable[tuple[str, int]],
    global_meta: Optional[dict] = None,
) -> str:
    """Build an FFMETADATA1 doc: optional global tags + one ``[CHAPTER]`` per
    ``(title, duration_ms)``. START/END are cumulative millisecond offsets.
    """
    lines = [";FFMETADATA1"]
    if global_meta:
        for field_key, meta_key in _GLOBAL_TAG_KEYS:
            val = global_meta.get(field_key)
            if val is not None and str(val).strip():
                lines.append(f"{meta_key}={_escape_meta(str(val).strip())}")
    start = 0
    for title, dur_ms in chapters:
        end = start + max(0, int(dur_ms))
        lines += [
            "[CHAPTER]",
            "TIMEBASE=1/1000",
            f"START={start}",
            f"END={end}",
            f"title={_escape_meta(title)}",
        ]
        start = end
    return "\n".join(lines) + "\n"


def build_concat_list(wav_paths: Iterable[str]) -> str:
    """Build an ffmpeg concat-demuxer list. Single quotes in paths are escaped
    the ffmpeg way (``'`` → ``'\\''``) so paths can't break the list or inject
    arguments."""
    lines = []
    for p in wav_paths:
        safe = str(p).replace("'", "'\\''")
        lines.append(f"file '{safe}'")
    return "\n".join(lines) + "\n"


# ── Cover art ───────────────────────────────────────────────────────────────

def validate_cover_image(path: Optional[str]) -> bool:
    """True if ``path`` is a readable jpg/png within the size cap. Anything
    dubious (missing, wrong type, too big, unreadable) → False, and the caller
    simply omits the cover rather than failing the render."""
    if not path:
        return False
    try:
        p = Path(path)
        return (
            p.is_file()
            and p.suffix.lower() in _COVER_EXTS
            and 0 < p.stat().st_size <= _COVER_MAX_BYTES
        )
    except OSError:
        return False


# ── Render command ──────────────────────────────────────────────────────────

def build_render_cmd(
    ffmpeg: str,
    concat_list_path: str,
    metadata_path: str,
    out_path: str,
    *,
    fmt: str = "m4b",
    bitrate: str = "128k",
    cover_path: Optional[str] = None,
    loudness: Optional[str] = None,
    measured: Optional[MeasuredLoudness] = None,
) -> list[str]:
    """Pure argv for muxing chapter WAVs + FFMETADATA into a tagged,
    chapter-marked audio file.

    Inputs: 0 = concat-demuxer list of chapter WAVs, 1 = FFMETADATA (chapters +
    global tags), 2 = cover image (only when present + valid). ``fmt`` is
    ``m4b`` (AAC in mp4, faststart) or ``mp3`` (libmp3lame). A loudness preset
    adds an ``-af loudnorm`` pass; an invalid/oversized cover is silently
    dropped (see :func:`validate_cover_image`).
    """
    if not _BITRATE_RE.match(bitrate or ""):
        bitrate = "128k"
    is_mp3 = (fmt or "").lower() == "mp3"
    # Cover art is embedded for M4B only. The MP3 muxer rejects an
    # ``attached_pic`` video stream via ``-c:v copy`` (produces a corrupt file
    # across ffmpeg versions), and a reliable cross-version ID3 APIC path is
    # finicky — so for MP3 we skip the cover rather than ship a broken file.
    # M4B is the cover-bearing audiobook format anyway.
    embed_cover = validate_cover_image(cover_path) and not is_mp3

    cmd = [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-f", "concat", "-safe", "0", "-i", str(concat_list_path),
        "-i", str(metadata_path),
    ]
    if embed_cover:
        cmd += ["-i", str(cover_path)]

    cmd += ["-map", "0:a", "-map_metadata", "1"]
    if embed_cover:
        cmd += ["-map", "2:v", "-disposition:v", "attached_pic"]

    # Two-pass apply when measured values are present; else single-pass. Both
    # return None for a non-preset loudness, so the `if filt:` guard below
    # gives an off-render no -af (byte-identical to today).
    filt = build_loudnorm_apply_filter(loudness, measured) if measured is not None else build_loudnorm_filter(loudness)
    if filt:
        cmd += ["-af", filt]

    if is_mp3:
        cmd += ["-c:a", "libmp3lame", "-b:a", bitrate, "-f", "mp3", str(out_path)]
    else:  # m4b — AAC in an mp4 container
        cmd += ["-c:a", "aac", "-b:a", bitrate]
        if embed_cover:
            cmd += ["-c:v", "copy"]
        cmd += ["-movflags", "+faststart", "-f", "mp4", str(out_path)]
    return cmd


# ── Render summary (what a finished render WAS) ─────────────────────────────

#: Cap on chapter titles kept in a summary — the library is a list, not a TOC.
_SUMMARY_MAX_TITLES = 60


def render_summary(
    chapters: list,
    *,
    voices: list[dict],
    engine_id: str = "",
    language: Optional[str] = None,
    fmt: str = "",
    options: Optional[dict] = None,
) -> dict:
    """How a render was made, small enough to ride on its ``done`` event.

    A finished file in a library is only useful if it says what it is: which
    voice, how fast, which engine, how it was joined. ``chapters`` is the plan
    (objects with ``title`` and ``spans`` carrying ``text``/``speed``);
    ``voices`` is the already-resolved ``[{"id", "name"}]`` actually used;
    ``options`` is the render's non-default expressive options, already filtered
    by the caller (any new knob — join gaps included — shows up here without
    touching this function).
    Content-free by design: counts and settings, never the script text.
    """
    spans = [s for c in chapters for s in getattr(c, "spans", [])]
    spoken = [s for s in spans if (getattr(s, "text", "") or "").strip()]
    speeds = sorted({round(float(getattr(s, "speed", None) or 1.0), 2) for s in spoken})
    titles = [str(getattr(c, "title", "") or "") for c in chapters][:_SUMMARY_MAX_TITLES]
    return {
        "engine": engine_id or "",
        "voices": [{"id": str(v.get("id") or ""), "name": str(v.get("name") or "")} for v in voices],
        "language": language or "",
        "format": fmt or "",
        "lines": len(spoken),
        "words": sum(len(s.text.split()) for s in spoken),
        "speeds": speeds,
        # The caller passes only non-default options; keep explicit falsy values
        # (seed 0, postprocess off) — they are settings, not absences.
        "options": {str(k): v for k, v in (options or {}).items() if v is not None},
        "chapter_titles": titles,
    }
