"""Audiobook creator endpoints (parity Wave 5).

``POST /audiobook/plan`` — pure preview: parse a chapter-delimited script
(Markdown ``# H1`` chapters, inline ``[voice:NAME]`` / ``[pause …]``) into the
chapter/span plan, no synthesis.

``POST /audiobook`` — the synth job: render each chapter through the active TTS
backend (reusing ``services.audiobook.synthesize_chapter`` + ``chunked_tts``),
then mux the chapter WAVs into a chapterized **m4b** (FFMETADATA1 chapters via
``build_m4b_cmd``). Progress streams as Server-Sent Events, mirroring the dub
pipeline. ffmpeg-gated — without ffmpeg the job reports an error event and
stops (the m4b is the only output format).

``GET /audiobook/jobs`` + ``POST /audiobook/resume/{job_id}`` — durable
crash-resume: an interrupted render persists its plan + params to a
``resume.json`` manifest in the job work dir, so it can be resumed later (the
content-addressed chapter cache makes finished chapters instant) even without
the original script. The resume UI affordance remains a follow-up.

epub/pdf ingest, ACX mastering shipped; the resume UI surface remains a follow-up.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import uuid

from collections.abc import Awaitable, Callable

from fastapi import APIRouter, File, HTTPException, Request, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from services.audiobook import (
    ExpressiveOptions,
    parse_audiobook_script,
    synthesize_chapter,
)
from services.longform_render import (
    LOUDNESS_PRESETS,
    build_concat_list,
    build_ffmetadata,
    build_render_cmd,
    prune_cache_dir,
)
from services import longform_resume  # pure (no torch) — durable resume manifest

logger = logging.getLogger("omnivoice.audiobook")
router = APIRouter()

# A cover filename as produced by /audiobook/cover: 12 hex chars + image ext.
# An exact-match allowlist is the strongest barrier (and the one CodeQL's
# path-injection query recognizes) — anything else is rejected outright.
_COVER_NAME_RE = re.compile(r"^[0-9a-f]{12}\.(?:jpg|jpeg|png)$")


def _safe_cover_path(cover_path: str | None) -> str | None:
    """Confine a user-supplied cover to the upload directory before it can flow
    into ffmpeg.

    Covers only ever come from ``/audiobook/cover``, which writes them to
    ``OUTPUTS_DIR/audiobook_covers`` with a generated name. We rebuild the path
    from the basename alone (``os.path.basename`` strips any directory component
    or ``..`` traversal) joined onto that fixed directory, so no caller-supplied
    path — absolute or relative — can escape it. Returns the path only if the
    file actually exists there, else None."""
    if not cover_path:
        return None
    from core.config import OUTPUTS_DIR
    name = os.path.basename(cover_path)
    if not _COVER_NAME_RE.match(name):
        return None  # not a name the upload endpoint could have produced
    cover_dir = os.path.realpath(os.path.join(OUTPUTS_DIR, "audiobook_covers"))
    real = os.path.realpath(os.path.join(cover_dir, name))
    # Containment check on the resolved path itself — it must live inside the
    # covers dir. Belt-and-suspenders over the regex+basename above; the
    # commonpath form is the path-injection barrier static analysis recognizes.
    if os.path.commonpath([real, cover_dir]) != cover_dir:
        return None
    return real if os.path.isfile(real) else None


class ExpressiveMixin(BaseModel):
    """Optional expressive/quality knobs shared by every longform front door
    (#1208). All optional — an omitted field reproduces today's exact render.

    * Sampling: ``num_step`` / ``guidance_scale`` / ``position_temperature`` /
      ``class_temperature`` / ``postprocess_output`` — the same surface the
      Voice page's Production Overrides expose. Unset → the documented longform
      preset (num_step 32, guidance 2.0, model-default temps, postprocess on).
    * ``seed`` — a book-level determinism override (else the profile's pinned
      seed, else fresh-render variety).
    * Emotion (IndexTTS2 only): ``emo_vector`` (8 floats) / ``emo_text`` /
      ``emo_alpha`` — reach engines that understand them via the generic synth
      closure; other engines ignore them.
    * ``vary_repeats`` — cache opt-out: give identical repeated lines distinct
      takes instead of replaying one recording (default off = today).
    """

    # Bounds so a loopback POST (reachable by a browser-tab CSRF) can't pin a
    # GPU-pool worker with an absurd step count or otherwise feed the sampler
    # nonsense. Ranges are generous supersets of the Voice-page controls; unset
    # (None) still means "use the longform default", unchanged. (#1208)
    num_step: int | None = Field(default=None, ge=1, le=512)
    guidance_scale: float | None = Field(default=None, ge=0.0, le=20.0)
    position_temperature: float | None = Field(default=None, ge=0.0, le=100.0)
    class_temperature: float | None = Field(default=None, ge=0.0, le=100.0)
    postprocess_output: bool | None = None
    seed: int | None = Field(default=None, ge=0, le=2**32 - 1)
    emo_vector: list[float] | None = Field(default=None, min_length=8, max_length=8)
    emo_text: str | None = Field(default=None, max_length=500)
    emo_alpha: float | None = Field(default=None, ge=0.0, le=1.0)
    vary_repeats: bool = False


def _expressive_opts(req: "ExpressiveMixin") -> ExpressiveOptions:
    """Lower a request's expressive fields into the typed engine-options object."""
    return ExpressiveOptions(
        num_step=req.num_step,
        guidance_scale=req.guidance_scale,
        position_temperature=req.position_temperature,
        class_temperature=req.class_temperature,
        postprocess_output=req.postprocess_output,
        seed=req.seed,
        emo_vector=tuple(req.emo_vector) if req.emo_vector else None,
        emo_text=(req.emo_text or None),
        emo_alpha=req.emo_alpha,
        vary_repeats=bool(req.vary_repeats),
    )


class AudiobookPlanRequest(BaseModel):
    text: str
    default_voice: str | None = None


@router.post("/audiobook/plan")
def audiobook_plan(req: AudiobookPlanRequest) -> dict:
    """Parse a script into a chapter/span plan (pure preview, no synthesis)."""
    plan = parse_audiobook_script(req.text, default_voice=req.default_voice)
    return plan.to_dict()


#: Cover size cap mirrors longform_render's guard (8 MB — a book cover, not a
#: payload). Kept in sync intentionally; the render builder re-validates too.
_COVER_MAX_BYTES = 8 * 1024 * 1024
#: Import upload cap — a generous ceiling for a .txt/.md/.epub manuscript that
#: still stops a memory-exhaustion upload (the whole file is read into RAM).
_IMPORT_MAX_BYTES = 64 * 1024 * 1024
#: Upper bound on chapters in a single /longform/render plan — far above any real
#: book, but stops a pathological request from allocating/holding the job forever.
_MAX_CHAPTERS = 10_000


@router.post("/audiobook/import")
async def audiobook_import(file: UploadFile = File(...)) -> dict:
    """Import a ``.txt``/``.md``/``.epub``/``.pdf`` into a chapter-delimited script.

    EPUB is parsed in spine order (stdlib only, local); PDF text is extracted
    with pypdf (pure-Python) then chapterized; plain text gets ``# `` headings
    inserted ahead of obvious chapter-title lines. Returns the script text (for
    the editor) + the resulting chapter count."""
    from services.longform_import import (
        chapterize_plaintext,
        epub_to_chapter_script,
        pdf_to_chapter_script,
    )

    name = (file.filename or "").lower()
    data = await file.read()
    if not data:
        raise HTTPException(status_code=400, detail="empty file")
    if len(data) > _IMPORT_MAX_BYTES:
        raise HTTPException(status_code=400, detail="file too large (max 64 MB)")
    if name.endswith(".epub"):
        try:
            script = epub_to_chapter_script(data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"couldn't parse EPUB: {e}")
    elif name.endswith(".pdf"):
        try:
            script = pdf_to_chapter_script(data)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=f"couldn't parse PDF: {e}")
    else:
        from services.text_upload import decode_text_upload
        script = chapterize_plaintext(decode_text_upload(data))
    if not script.strip():
        raise HTTPException(status_code=400, detail="no text found in the file")
    plan = parse_audiobook_script(script)
    return {"text": script, "chapters": plan.chapter_count}


@router.post("/audiobook/cover")
async def audiobook_cover(cover: UploadFile = File(...)) -> dict:
    """Upload a cover image; returns a server-side ``path`` to pass back as
    ``cover_path`` in the synth request. Validated here (jpg/png + size cap) and
    again at render time."""
    from core.config import OUTPUTS_DIR

    ext = os.path.splitext(cover.filename or "")[1].lower()
    if ext not in (".jpg", ".jpeg", ".png"):
        raise HTTPException(status_code=400, detail="cover must be a .jpg or .png")
    data = await cover.read()
    if not data or len(data) > _COVER_MAX_BYTES:
        raise HTTPException(status_code=400, detail="cover must be between 1 byte and 8 MB")
    cover_dir = os.path.join(OUTPUTS_DIR, "audiobook_covers")
    os.makedirs(cover_dir, exist_ok=True)
    path = os.path.join(cover_dir, f"{uuid.uuid4().hex[:12]}{ext}")
    with open(path, "wb") as f:
        f.write(data)
    return {"path": path}


class AudiobookRequest(ExpressiveMixin):
    text: str
    default_voice: str | None = None   # voice profile id; None = engine default
    language: str | None = None        # None/"Auto" → profile language, else autodetect (#505)
    bitrate: str = "128k"
    format: str = "m4b"                 # "m4b" | "mp3"
    loudness: str | None = None         # None/"off" | "acx" | "podcast" (opt-in)
    cover_path: str | None = None       # server-side path to a jpg/png cover
    # Global tags embedded in the output: {title, author, narrator, year,
    # genre, description}. Player-visible (Apple Books / Audible read these).
    metadata: dict | None = None
    # Optional pronunciation lexicon {word: respelling} applied before synthesis.
    lexicon: dict | None = None
    # Optional cast map {[voice:NAME] → profile id} for multi-voice books (#1217).
    # Absent/empty reproduces today's exact render + cache keys.
    voice_map: dict[str, str] | None = None


def _resolve_voice(profile_id: str | None) -> dict:
    """Map a voice-profile id to (ref_audio, ref_text, instruct, seed).

    Compact form of the resolver in generation.py — covers locked, design and
    clone profiles. Returns all-None for the engine default (no profile).
    """
    out = {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}
    if not profile_id:
        return out
    from core.config import VOICES_DIR
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (profile_id,)).fetchone()
    if not row:
        return out
    try:
        kind = row["kind"] or "clone"
    except (KeyError, IndexError):
        kind = "clone"
    if row["is_locked"] and row["locked_audio_path"]:
        out["ref_audio"] = os.path.join(VOICES_DIR, row["locked_audio_path"])
        out["ref_text"] = row["ref_text"]
        out["instruct"] = row["instruct"]
    elif kind == "design":
        out["ref_audio"] = os.path.join(VOICES_DIR, row["ref_audio_path"]) if row["ref_audio_path"] else None
        out["ref_text"] = row["ref_text"] if out["ref_audio"] else None
        out["instruct"] = row["instruct"]
    else:
        out["ref_audio"] = os.path.join(VOICES_DIR, row["ref_audio_path"]) if row["ref_audio_path"] else None
        out["ref_text"] = row["ref_text"]
        out["instruct"] = row["instruct"]
    try:
        if row["seed"] is not None:
            out["seed"] = row["seed"]
    except (KeyError, IndexError):
        pass
    return out


def _voice_profile_exists(profile_id: str | None) -> bool:
    """True iff ``profile_id`` names a real voice profile (#1217).

    Used to distinguish an exact profile id (a UUID someone passed as a span
    voice) from a bare ``[voice:NAME]`` name that has no cast mapping — the
    former resolves as-is, the latter falls back to the book default instead of
    silently missing and dropping to the engine default."""
    if not profile_id:
        return False
    from core.db import db_conn

    with db_conn() as conn:
        row = conn.execute(
            "SELECT 1 FROM voice_profiles WHERE id=? LIMIT 1", (profile_id,)
        ).fetchone()
    return row is not None


def _render_summary(chapters, default_voice, voice_map, language, fmt, opts) -> dict:
    """The finished render's summary: resolve the voices it used to profile names."""
    from core.db import db_conn
    from services.longform_render import render_summary
    from services.tts_backend import active_backend_id

    ids: list[str] = []
    for chapter in chapters:
        for span in chapter.spans:
            pid = _map_span_voice(span.voice_id, default_voice, voice_map)
            if pid and pid not in ids:
                ids.append(pid)
    names: dict[str, str] = {}
    if ids:
        marks = ",".join("?" * len(ids))
        with db_conn() as conn:
            rows = conn.execute(f"SELECT id, name FROM voice_profiles WHERE id IN ({marks})", ids).fetchall()  # nosec B608 — placeholders only
        names = {row["id"]: row["name"] for row in rows}
    # Options that differ from the defaults — by VALUE, so an explicit seed=0 or
    # postprocess_output=False is recorded, and an untouched default is not.
    defaults = ExpressiveOptions().to_manifest()
    chosen = (opts or ExpressiveOptions()).to_manifest()
    return render_summary(
        chapters,
        voices=[{"id": pid, "name": names.get(pid, "")} for pid in ids],
        engine_id=active_backend_id(), language=language, fmt=fmt,
        options={k: v for k, v in chosen.items() if v != defaults.get(k)},
    )


def _map_span_voice(
    voice_id: str | None, default_voice: str | None, voice_map: dict | None
) -> str | None:
    """Translate a span's voice token to the profile id to synthesize with (#1217).

    A span's ``voice_id`` is whatever the longform parser captured from
    ``[voice:NAME]`` — the raw human NAME, never a profile id. Resolve it:

      * ``None``/empty (a run with no ``[voice:]``) → ``default_voice``.
      * a NAME present in ``voice_map`` → its mapped profile id. THIS is the
        multi-voice cast fix: before it, a NAME was handed straight to
        ``_resolve_voice`` as if it were a profile id, always missed (profile
        ids are UUIDs), and every ``[voice:…]`` silently rendered in the engine
        default — so ``[voice:Mara]``/``[voice:Cole]`` sounded identical.
      * an unmapped token that IS a real profile id (someone passed an exact id)
        → itself, unchanged (exact-id back-compat, e.g. Stories spans).
      * an unmapped token that is NOT a real profile id (a NAME with no cast
        entry) → ``default_voice`` (fixes the silent-default bug for unmapped
        names: no longer treated as a literal id).
    """
    if not voice_id:
        return default_voice
    if voice_map:
        mapped = voice_map.get(voice_id)
        if mapped:
            return mapped
    if _voice_profile_exists(voice_id):
        return voice_id
    return default_voice


def _resolve_default_language(language: str | None, default_voice: str | None) -> str | None:
    """Pick the language to thread into the longform synth callable.

    Priority (mirrors the single-shot /generate path, #533): an explicit
    non-Auto request ``language`` wins; otherwise the selected profile's stored
    language drives it; otherwise ``None`` (genuine Auto — the engine
    autodetects, exactly as before). Hardcoding ``None`` here (#505 B2) let the
    engine re-autodetect per chunk, so a non-English clone flipped to the wrong
    language on short/ambiguous chapters.
    """
    if language and language != "Auto":
        return language
    if default_voice:
        from core.db import db_conn
        with db_conn() as conn:
            row = conn.execute(
                "SELECT language FROM voice_profiles WHERE id=?", (default_voice,)
            ).fetchone()
        if row:
            try:
                prof_lang = row["language"]
            except (KeyError, IndexError):
                prof_lang = None
            if prof_lang and prof_lang != "Auto":
                return prof_lang
    return None


#: Longform renders run at the model's documented quality preset (#1139).
#: This used to be an accident of omission — the synth wrappers below passed
#: no num_step/guidance_scale, silently inheriting OmniVoiceGenerationConfig's
#: defaults (32 / 2.0) while interactive /generate defaults to num_step=16 —
#: and users correctly heard audiobooks as more stable than the Voice page.
#: Named constants make the divergence a documented decision (a book is a
#: cached batch job: quality beats latency) and pin book quality against any
#: upstream config-default drift.
LONGFORM_NUM_STEP = 32
LONGFORM_GUIDANCE_SCALE = 2.0


def _seed_segment_rng(base_seed, text: str, nonce: int = 0) -> int | None:
    """Apply a profile's pinned seed to this synth call (#1139).

    ``_resolve_voice`` has always fetched the profile ``seed`` — but only the
    cache signature ever used it; generation itself ran unseeded, so a locked
    take's pinned seed silently did nothing here while /generate honored it.
    No pinned seed → no-op (fresh-render variety unchanged).

    Concurrency contract: this seeds the process-global torch RNG, exactly
    like /generate's #526 seeding (generation.py's ``torch.manual_seed`` in
    ``_run_inference``/``_run_backend_inference``, same GPU pool). Both are
    strictly deterministic wherever the pool has one worker — the default on
    MPS/CPU and small-VRAM CUDA (model_manager._pick_gpu_workers) — and
    best-effort when a >1-worker CUDA pool runs another seeded job in the
    same window. Making that window race-free requires threading a per-call
    torch.Generator through the model's samplers app-wide; if that lands, it
    must cover /generate and here together, not one path.
    """
    if base_seed is None:
        return None
    import torch

    from services.audiobook import segment_seed
    seed = segment_seed(base_seed, text, nonce)
    torch.manual_seed(seed)
    return seed


def _base_seed(opts: ExpressiveOptions, voice: dict):
    """The seed that drives this render's determinism: an explicit book-level
    ``seed`` override wins, else the selected profile's pinned seed, else None
    (fresh-render variety, unchanged)."""
    return opts.seed if opts.seed is not None else voice.get("seed")


def _make_occ_counter(opts: ExpressiveOptions):
    """Per-closure occurrence counter for the cache opt-out (#1208).

    When ``vary_repeats`` is on, every synth call gets a monotonically rising
    nonce so a pinned-seed line that repeats is seeded distinctly per take (the
    segment cache is defeated per-occurrence in parallel). Off → always 0, so
    the seed derivation is byte-identical to pre-#1208."""
    state = {"n": 0}

    def next_nonce() -> int:
        if not opts.vary_repeats:
            return 0
        n = state["n"]
        state["n"] = n + 1
        return n

    return next_nonce


def _omnivoice_sampling_kwargs(opts: ExpressiveOptions) -> dict:
    """VoiceStudio-model generate kwargs for the sampling knobs. UNSET reproduces
    today exactly: num_step 32, guidance 2.0, and NO temperature/postprocess
    kwargs (the model keeps its own defaults). Emotion is never forwarded —
    the VoiceStudio config rejects unknown kwargs."""
    from services.performance_profiles import tts_defaults

    defaults = tts_defaults()
    kw = {
        "num_step": opts.num_step if opts.num_step is not None else defaults.get("num_step", LONGFORM_NUM_STEP),
        "guidance_scale": (
            opts.guidance_scale if opts.guidance_scale is not None else LONGFORM_GUIDANCE_SCALE
        ),
    }
    if opts.position_temperature is not None:
        kw["position_temperature"] = opts.position_temperature
    if opts.class_temperature is not None:
        kw["class_temperature"] = opts.class_temperature
    if opts.postprocess_output is not None:
        kw["postprocess_output"] = opts.postprocess_output
    elif "postprocess_output" in defaults:
        kw["postprocess_output"] = defaults["postprocess_output"]
    return kw


def _generic_extra_kwargs(opts: ExpressiveOptions) -> dict:
    """Extra generate kwargs for a non-VoiceStudio engine. UNSET → empty dict →
    byte-identical to the pre-#1208 generic call. Only present knobs are added,
    and every shipped backend's ``generate(self, text, **kw)`` ignores the ones
    it doesn't understand (never TypeError) — the engine-options contract. The
    emotion trio reaches IndexTTS2's arbitration; other engines drop it."""
    kw: dict = {}
    if opts.num_step is not None:
        kw["num_step"] = opts.num_step
    if opts.guidance_scale is not None:
        kw["guidance_scale"] = opts.guidance_scale
    if opts.position_temperature is not None:
        kw["position_temperature"] = opts.position_temperature
    if opts.class_temperature is not None:
        kw["class_temperature"] = opts.class_temperature
    if opts.postprocess_output is not None:
        kw["postprocess_output"] = opts.postprocess_output
    if opts.emo_vector:
        kw["emo_vector"] = list(opts.emo_vector)
    if opts.emo_text:
        kw["emo_text"] = opts.emo_text
        kw["use_emo_text"] = True
    if opts.emo_alpha is not None:
        kw["emo_alpha"] = opts.emo_alpha
    return kw


def _build_synth(
    default_voice: str | None,
    language: str | None = None,
    opts: ExpressiveOptions | None = None,
    voice_map: dict | None = None,
) -> dict:
    """Describe how to synthesize for the active TTS engine.

    Returns a dict with ``mode``, ``resolve`` (voice-id → resolved refs, cached
    per id) and ``engine_id``. For VoiceStudio it also carries the async
    ``get_model``; other engines carry a ready ``synth`` + ``sample_rate``.
    :func:`_prepare_synth` turns this into a uniform ``(synth, sr, resolve,
    engine_id)`` once the (async) model is in hand.

    ``language`` (already resolved by :func:`_resolve_default_language`) is
    threaded into every chunk's ``generate`` so a non-English clone stays in its
    language instead of re-autodetecting per chunk (#505 B2). ``None`` keeps the
    engine's autodetect behavior unchanged.

    ``opts`` (#1208) carries the expressive/quality knobs + cache opt-out. A
    default instance reproduces today's exact synth call and caching.
    """
    from services.tts_backend import OmniVoiceBackend, active_backend_id, get_backend_class

    opts = opts or ExpressiveOptions()
    cache: dict = {}
    token_cache: dict = {}

    def resolve(voice_id):
        # Translate the span token ([voice:NAME] / exact id / None) to a profile
        # id first (#1217) — the cast fix lives here, not in the parser, so the
        # parser stays a pure text→plan and exact ids keep working. Cache the
        # translation so a book of hundreds of same-name spans does one DB check.
        if voice_id not in token_cache:
            token_cache[voice_id] = _map_span_voice(voice_id, default_voice, voice_map)
        key = token_cache[voice_id]
        if key not in cache:
            cache[key] = _resolve_voice(key)
        return cache[key]

    engine_id = active_backend_id()
    cls = get_backend_class(engine_id)
    if cls is OmniVoiceBackend:
        from services.model_manager import get_model
        return {"mode": "omnivoice", "resolve": resolve, "engine_id": engine_id,
                "get_model": get_model, "language": language, "opts": opts}

    backend = cls()
    native_proxy = bool(getattr(cls, "supports_native_omnivoice_controls", False))
    extra = (_omnivoice_sampling_kwargs(opts) if native_proxy
             else _generic_extra_kwargs(opts))
    next_nonce = _make_occ_counter(opts)

    def synth(text, voice_id, speed=None):
        v = resolve(voice_id)
        seed = _seed_segment_rng(_base_seed(opts, v), text, next_nonce())
        call_extra = dict(extra)
        if native_proxy and seed is not None:
            call_extra["seed"] = seed
        return backend.generate(
            text, language=language, ref_audio=v["ref_audio"],
            ref_text=v["ref_text"], instruct=v["instruct"], duration=None,
            speed=float(speed) if speed else 1.0, **call_extra,
        )
    return {"mode": "generic", "resolve": resolve, "engine_id": engine_id,
            "synth": synth, "sample_rate": backend.sample_rate}


async def _prepare_synth(
    default_voice: str | None,
    language: str | None = None,
    opts: ExpressiveOptions | None = None,
    voice_map: dict | None = None,
):
    """Resolve :func:`_build_synth` into ``(synth, sample_rate, resolve,
    engine_id)`` — awaiting the VoiceStudio model load when needed. Shared by the
    full job and the per-chapter preview. ``language`` is threaded into every
    chunk so a non-English clone holds its language (#505 B2). ``opts`` (#1208)
    carries the expressive knobs; a default instance reproduces today exactly."""
    opts = opts or ExpressiveOptions()
    info = _build_synth(default_voice, language=language, opts=opts, voice_map=voice_map)
    resolve, engine_id = info["resolve"], info["engine_id"]
    if info["mode"] == "omnivoice":
        lang = info["language"]
        model = await info["get_model"]()
        sr = getattr(model, "sampling_rate", 24000)

        from services.tts_backend import generate_with_cached_ref

        sampling = _omnivoice_sampling_kwargs(opts)
        next_nonce = _make_occ_counter(opts)

        def synth(text, voice_id, speed=None):
            v = resolve(voice_id)
            _seed_segment_rng(_base_seed(opts, v), text, next_nonce())
            # A book is the worst case for the re-encode this avoids: hundreds of
            # segments, one voice. The reference is encoded on the first segment
            # and reused for every one after it.
            return generate_with_cached_ref(
                model, ref_audio=v["ref_audio"], ref_text=v["ref_text"],
                text=text, language=lang, instruct=v["instruct"], duration=None,
                speed=float(speed) if speed else 1.0, **sampling,
            )[0]
        return synth, sr, resolve, engine_id
    return info["synth"], info["sample_rate"], resolve, engine_id


def _render_chapter_cached(chapter, synth, sr, engine_id, resolve, cache_dir, lexicon=None,
                           language=None, opts=None, voice_map=None):
    """Render one chapter, content-addressed so a re-run reuses it (resume).

    Returns ``(wav_path, duration_s, was_cached, seg_stats)``. Two cache
    layers:

    * Outer — the WAV at ``cache_dir/<key>.wav`` where ``key`` is
      :func:`chapter_cache_key` over the chapter's spans + sample rate +
      engine + each voice's resolved signature (+ the lexicon, so a lexicon
      edit re-renders). A fully-unchanged chapter hits here and never touches
      segment files. With invisible watermarking active the key also carries a
      watermark tag (#1169) — pre-#1169 chapter caches (unmarked audio)
      deliberately miss once and re-render marked; with watermarking off the
      derivation is unchanged and released-version caches keep hitting.
      ``seg_stats`` is ``None``.
    * Inner — on a chapter miss, each spoken span goes through the
      :class:`services.longform_render.SegmentCache` under
      ``cache_dir/segments``: cached segments load from disk, only the
      edited/missing ones synthesize, and each fresh segment persists the
      moment it renders (an interrupted chapter resumes from them).
      ``seg_stats`` is ``{"total": spoken_spans, "cached": reused}``.

    Span text is normalized (``services.text_normalization``) up front — BEFORE
    either cache key and BEFORE ``synthesize_chapter``'s lexicon pass, so the
    per-project dictionary operates on normalized text and toggling / changing
    normalization output naturally invalidates cached chapters and segments.

    Runs in the GPU-pool executor.
    """
    import json
    import wave

    from services.audio_io import atomic_save_wav
    from services.audiobook import ExpressiveOptions, Span, voice_map_signature
    from services.longform_render import SegmentCache, chapter_cache_key
    from services.pronunciation import normalize_lexicon
    from services.text_normalization import normalize_for_tts
    from services.watermark import mark_synthetic, will_mark

    opts = opts or ExpressiveOptions()

    spans = [Span(voice_id=s.voice_id, text=normalize_for_tts(s.text, language),
                  pause_ms_after=s.pause_ms_after, speed=getattr(s, "speed", None))
             for s in chapter.spans]
    spans_tuples = [(s.voice_id, s.text, s.pause_ms_after, getattr(s, "speed", None))
                    for s in spans]
    voice_sigs: dict = {}
    for s in spans:
        k = s.voice_id or ""
        if k not in voice_sigs:
            v = resolve(s.voice_id)
            voice_sigs[k] = f"{v.get('ref_audio')}|{v.get('ref_text')}|{v.get('instruct')}|{v.get('seed')}"
    sig: dict = dict(voice_sigs)
    lex_sig = ""
    if lexicon:
        # Fold the lexicon into the cache key so editing pronunciations
        # invalidates cached chapters (reserved key can't collide with a voice id).
        lex_sig = json.dumps(normalize_lexicon(lexicon), sort_keys=True)
        sig["\x00lexicon"] = lex_sig
    # Fold the #1208 expressive signature into BOTH cache layers so changing any
    # new knob (sampling, emotion, seed, cache opt-out) re-renders instead of
    # replaying stale audio (the CRITICAL TRAP). Empty for a default render, so
    # the derivation stays byte-identical to pre-#1208 and released caches hit.
    expr_sig = opts.cache_signature()
    if expr_sig:
        sig["\x00expressive"] = expr_sig
    # Fold the #1217 voice map into BOTH cache layers, exactly like the
    # expressive signature: remapping a [voice:NAME] must re-render, while an
    # empty/absent map keeps the key byte-identical to pre-#1217 (existing books
    # never re-render). The resolved voice_sigs above already reflect a mapping
    # when synthesis actually resolves it, but folding the raw map in makes the
    # invalidation robust even where resolution is short-circuited/stubbed.
    vmap_sig = voice_map_signature(voice_map)
    if vmap_sig:
        sig["\x00voicemap"] = vmap_sig
    seg_extra_sig = f"{lex_sig}\x00{expr_sig}" if expr_sig else lex_sig
    if vmap_sig:
        seg_extra_sig = f"{seg_extra_sig}\x00{vmap_sig}"
    if will_mark():
        # Provenance-marked chapters cache under their own key (#1169): a
        # chapter WAV rendered while watermarking was off/unavailable —
        # including every cache entry written before marking existed — must
        # never satisfy a request made while it's on. Deliberately one-time
        # invalidates pre-#1169 chapter caches (the SEGMENT cache underneath
        # is untouched, so re-rendering is assembly + one embed, not re-TTS);
        # with marking off the key is byte-identical to the released
        # derivation, so those caches keep hitting.
        sig["\x00watermark"] = "1"
    key = chapter_cache_key(spans_tuples, sample_rate=sr, engine_id=engine_id, voice_sig=sig)
    wav_path = os.path.join(cache_dir, f"{key}.wav")

    if os.path.exists(wav_path):
        try:
            with wave.open(wav_path, "rb") as w:
                dur = w.getnframes() / float(w.getframerate() or sr)
            return wav_path, dur, True, None
        except Exception:
            pass  # corrupt cache entry — fall through and re-render

    seg_cache = SegmentCache(cache_dir, sample_rate=sr, engine_id=engine_id,
                             voice_sig=voice_sigs, extra_sig=seg_extra_sig,
                             vary_repeats=opts.vary_repeats)
    audio, dur = synthesize_chapter(spans, synth, sr, lexicon=lexicon,
                                    segment_cache=seg_cache)
    # Invisible provenance mark on the assembled chapter (#1169), tensor stage,
    # before the WAV lands in the cache — this single site covers every
    # longform front door (/audiobook, /longform/render [Stories],
    # /audiobook/preview, /audiobook/resume/{id}): the m4b/mp3 mux only
    # concatenates these WAVs, and AudioSeal survives the lossy encode.
    # Segments in the segment cache stay unmarked by design — they're
    # intermediate assembly inputs, re-marked here on every chapter render.
    # Already runs in the GPU-pool executor; never raises (degrades to
    # unmarked on failure).
    audio = mark_synthetic(audio, sr, context="longform.chapter")
    atomic_save_wav(wav_path, audio, sr)
    return wav_path, dur, False, {"total": seg_cache.hits + seg_cache.misses,
                                  "cached": seg_cache.hits}


def _remote_chapter_call(chapter, *, engine_id, default_voice, voice_map,
                         language, lexicon, opts, cache_dir):
    """Build one opaque remote chapter task without loading a local TTS model."""
    import hashlib

    from services import gpu_gateway
    from services.text_normalization import normalize_for_tts
    from services.watermark import is_enabled as watermark_enabled

    rows, voices, refs = [], [], []
    for span in chapter.spans:
        profile_id = _map_span_voice(span.voice_id, default_voice, voice_map)
        voice = _resolve_voice(profile_id)
        rows.append({
            "text": normalize_for_tts(span.text, language),
            "pause_ms_after": span.pause_ms_after,
            "speed": getattr(span, "speed", None),
        })
        refs.append(voice.get("ref_audio"))
        voices.append({
            "ref_text": voice.get("ref_text"), "instruct": voice.get("instruct"),
            "seed": voice.get("seed"),
        })
    params = {
        "spans": rows, "voices": voices, "ref_audio": refs,
        "language": language, "lexicon": lexicon,
        "expressive": opts.to_manifest(), "watermark": bool(watermark_enabled()),
    }
    signature = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    # The worker synthesizes from ``spans``, but the gateway and scheduler read
    # top-level ``text`` to scale the remote execution deadline. Add this after
    # the signature so existing content-addressed remote cache keys still hit.
    params["text"] = "\n".join(row["text"] for row in rows)
    wav_path = os.path.join(cache_dir, f"remote-{signature}.wav")

    def decode(result):
        import soundfile as sf
        if not os.path.exists(wav_path):
            partial = f"{wav_path}.part"
            shutil.copyfile(result.path, partial)
            os.replace(partial, wav_path)
        info = sf.info(wav_path)
        return wav_path, float(info.duration), False, None

    return gpu_gateway.RemoteCall(
        engine=engine_id, operation="audiobook", params=params,
        idempotency_key=f"audiobook:{signature}", decode=decode,
    ), wav_path


async def _run_chapter(chapter, *, operation="audiobook", decision, job, default_voice, language, opts,
                       voice_map, lexicon, cache_dir):
    """Run one chapter through the gateway; local preparation stays lazy."""
    from services import gpu_gateway
    from services.tts_backend import active_backend_id, get_backend_class

    engine_id = active_backend_id()
    remote, remote_cache = _remote_chapter_call(
        chapter, engine_id=engine_id, default_voice=default_voice,
        voice_map=voice_map, language=language, lexicon=lexicon,
        opts=opts, cache_dir=cache_dir,
    )
    if decision.remote and os.path.exists(remote_cache):
        import soundfile as sf
        info = sf.info(remote_cache)
        return remote_cache, float(info.duration), True, None

    async def prepare_local():
        from services.model_manager import generate_timeout_s

        synth, sr, resolve, local_engine = await _prepare_synth(
            default_voice, language=language, opts=opts, voice_map=voice_map
        )
        try:
            timeout_engine = get_backend_class(local_engine)
        except ValueError:
            # Tests and third-party integrations may inject a synth under a
            # non-catalogue id. Keep the canonical host/text policy available;
            # registered production engines still add their routing metadata.
            timeout_engine = None
        return gpu_gateway.LocalCall(
            fn=lambda: _render_chapter_cached(
                chapter, synth, sr, local_engine, resolve, cache_dir, lexicon,
                language, opts, voice_map,
            ),
            what="Audiobook chapter",
            timeout=generate_timeout_s(
                remote.params["text"], engine=timeout_engine
            ),
        )

    return await gpu_gateway.run(
        operation, local=gpu_gateway.LocalCall(prepare=prepare_local),
        remote=remote, decision=decision, job=job,
    )


class AudiobookPreviewRequest(ExpressiveMixin):
    text: str
    chapter_index: int = 0
    default_voice: str | None = None
    language: str | None = None   # None/"Auto" → profile language, else autodetect
    lexicon: dict | None = None
    # Cast map {[voice:NAME] → profile id} — MUST match the full render's so a
    # preview warms exactly the cache slot the render reuses (#1217).
    voice_map: dict[str, str] | None = None


@router.post("/audiobook/preview")
async def audiobook_preview(req: AudiobookPreviewRequest) -> dict:
    """Render a single chapter so the user can audition it before the full run.

    Reuses the same content-addressed cache as the job, so a preview warms the
    cache (the later full render reuses it) and a re-preview is instant.
    """
    from core.config import OUTPUTS_DIR
    from services import gpu_gateway

    plan = parse_audiobook_script(req.text, default_voice=req.default_voice)
    if not plan.chapters:
        raise HTTPException(status_code=400, detail="no chapters parsed from the script")
    n = len(plan.chapters)
    if not (0 <= req.chapter_index < n):
        raise HTTPException(status_code=400, detail=f"chapter_index out of range (0..{n - 1})")

    chapter = plan.chapters[req.chapter_index]
    cache_dir = os.path.join(OUTPUTS_DIR, "longform_cache")  # shared with _render_longform_sse
    os.makedirs(cache_dir, exist_ok=True)
    resolved_lang = _resolve_default_language(req.language, req.default_voice)
    opts = _expressive_opts(req)
    decision = gpu_gateway.decide("audiobook")
    wav_path, dur, was_cached, _seg_stats = await _run_chapter(
        chapter, decision=decision, job=None, default_voice=req.default_voice,
        language=resolved_lang, opts=opts, voice_map=req.voice_map,
        lexicon=req.lexicon, cache_dir=cache_dir,
    )
    return {
        "output": os.path.relpath(wav_path, OUTPUTS_DIR),  # served via /audio
        "duration_s": round(dur, 2),
        "cached": was_cached,
        "title": chapter.title,
    }


async def _render_longform_sse(
    plan,
    *,
    default_voice: str | None,
    language: str | None = None,
    fmt: str = "m4b",
    bitrate: str = "128k",
    loudness: str | None = None,
    cover_path: str | None = None,
    metadata: dict | None = None,
    lexicon: dict | None = None,
    opts: ExpressiveOptions | None = None,
    voice_map: dict | None = None,
    job_type: str = "audiobook",
    job_id: str | None = None,
    resume: bool = False,
    is_disconnected: Callable[[], Awaitable[bool]] | None = None,
):
    """Shared chapterized-render SSE generator for Audiobook *and* Stories.

    Takes a ready ``plan`` (``.chapters`` → ``.title`` + ``.spans``) — Audiobook
    parses it from a script, Stories compiles it from cast/lines — and renders
    each chapter (content-addressed cache → resume), isolating per-chapter
    failures, then muxes the successful chapters into a tagged file. This is the
    convergence point: one renderer, two front doors.
    """
    from core.config import OUTPUTS_DIR
    from core.failure import build_failure, build_failure_event
    from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg
    from services import gpu_gateway

    opts = opts or ExpressiveOptions()

    # Resume reuses the original job_id (continuing the same job row + cached
    # chapters); a fresh render generates a new one. The id may arrive from the
    # /resume/{job_id} path param, so strip it to a safe token (no path
    # separators, no CR/LF) before it ever reaches a filesystem path or a log
    # line — CodeQL py/path-injection + py/log-injection. Empty after the strip
    # → a fresh id.
    job_id = re.sub(r"[^A-Za-z0-9_-]", "", job_id or "")[:64] or uuid.uuid4().hex[:16]
    try:
        from core import job_store
        if not resume:
            job_store.create(job_id, type=job_type)
        job_store.mark_running(job_id)
    except Exception:
        job_store = None  # job history is best-effort; never block synthesis

    # Persist a durable resume manifest (plan + params) so an interrupted render
    # can be resumed later even without the original script. Best-effort.
    title = (metadata or {}).get("title") or (plan.chapters[0].title if plan.chapters else "")
    try:
        longform_resume.write_manifest(longform_resume.build_manifest(
            job_id=job_id, job_type=job_type, title=title,
            plan_chapters=[
                {"title": c.title, "spans": [s.to_dict() for s in c.spans]}
                for c in plan.chapters
            ],
            params={
                "default_voice": default_voice, "language": language,
                "fmt": fmt, "bitrate": bitrate,
                "loudness": loudness, "cover_path": cover_path,
                "metadata": metadata, "lexicon": lexicon,
                # #1208: persist the expressive knobs so a resumed render is
                # byte-consistent with the interrupted one (same cache keys).
                "expressive": opts.to_manifest(),
                # #1217: persist the cast map so a resumed render resolves and
                # caches every [voice:NAME] identically to the interrupted one.
                "voice_map": voice_map,
            },
        ))
    except Exception:  # resume durability is an enhancement; never block the render
        logger.debug("[%s] resume manifest write skipped", job_id, exc_info=True)

    def _emit(payload: dict) -> str:
        if job_store is not None:
            try:
                job_store.append_event(job_id, json.dumps(payload))
            except Exception:
                pass  # best-effort job history; never block the stream
        return f"data: {json.dumps(payload)}\n\n"

    if not plan.chapters:
        yield _emit({"type": "error", "error": "nothing to render (no chapters)"})
        return
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        yield _emit({"type": "error", "error": "ffmpeg not available; the output needs it"})
        return

    # Confined work dir (job_id is already token-sanitized above; work_dir adds
    # the basename + realpath barrier so CodeQL sees a clean path).
    work = longform_resume.work_dir(job_type, job_id)
    if work is None:
        yield _emit({"type": "error", "error": "invalid job id"})
        return
    os.makedirs(work, exist_ok=True)
    # Chapter WAVs are content-addressed in a shared cache so a re-run (after a
    # failure or interruption) reuses what already rendered — only the
    # missing/changed chapters synthesize again (resume). Shared across both
    # front doors: an identical chapter renders once.
    cache_dir = os.path.join(OUTPUTS_DIR, "longform_cache")
    os.makedirs(cache_dir, exist_ok=True)
    prune_cache_dir(cache_dir)  # bound disk before this job adds its chapters
    try:
        resolved_lang = _resolve_default_language(language, default_voice)
        operation = "audiobook" if job_type == "audiobook" else "longform"
        decision = gpu_gateway.decide(operation)
        chapter_run = gpu_gateway.JobRun(operation)

        total = len(plan.chapters)
        chapter_files: list[str] = []
        chapters_meta: list[tuple[str, int]] = []
        cached_n = 0
        failed: list[int] = []
        # Kept so the terminal "all chapters failed" event can name the cause
        # instead of restating the symptom (#1321).
        last_chapter_exc: Exception | None = None
        interrupted = False
        yield _emit({"type": "started", "job_id": job_id, "chapters": total})

        for i, chapter in enumerate(plan.chapters):
            # Client-disconnect cancellation (#1216): if the browser aborted the
            # request (the user hit Stop), stop scheduling further chapters
            # instead of rendering the whole book into a stream nobody reads.
            # Checked at the chapter boundary so a stop is clean and the finished
            # chapters — content-addressed in the shared cache — plus the resume
            # manifest are left in place, so a later Create/resume finishes the
            # rest cheaply. (Starlette also cancels this task on disconnect; the
            # explicit poll makes the stop deterministic and lets us emit a clean
            # terminal `stopped` event. This render parks no model on CPU the way
            # the dub transcribe does — #1191 — so there is no restore debt to
            # pay on exit; stopping is simply "schedule no more chapters".)
            if is_disconnected is not None:
                try:
                    gone = await is_disconnected()
                except Exception:
                    gone = False
                if gone:
                    interrupted = True
                    break
            try:
                wav_path, dur, was_cached, seg_stats = await _run_chapter(
                    chapter, operation=operation, decision=decision, job=chapter_run,
                    default_voice=default_voice, language=resolved_lang,
                    opts=opts, voice_map=voice_map, lexicon=lexicon,
                    cache_dir=cache_dir,
                )
            except Exception as e:  # isolate a bad chapter — keep going
                logger.warning("[%s] chapter %d (%s) failed to render",
                               job_id, i, chapter.title, exc_info=True)
                failed.append(i)
                # Carry the real reason (#1321). The old event said only
                # "chapter failed to render", so a failed chapter was a red row
                # and nothing else — the cause existed solely in the backend log,
                # which is why the report for this arrived as a bare traceback.
                # build_failure guarantees a non-empty reason even for exceptions
                # whose str() is empty (a generator-based engine that yields
                # nothing raises a bare StopIteration), sanitizes paths/tokens,
                # and adds the docs deeplink + hint. `error` stays populated —
                # build_failure mirrors reason into it — so older frontends and
                # the Stories exporter keep working.
                last_chapter_exc = e
                yield _emit({"type": "chapter_error", "index": i, "total": total,
                             "title": chapter.title,
                             # No env diagnostic per chapter: a book can fail
                             # hundreds of times and it is identical every time.
                             # The terminal error below carries one.
                             **build_failure(e, stage="audiobook_chapter",
                                             include_diagnostic=False)})
                continue
            chapter_files.append(wav_path)
            chapters_meta.append((chapter.title, int(round(dur * 1000))))
            cached_n += 1 if was_cached else 0
            ev = {"type": "chapter", "index": i, "total": total,
                  "title": chapter.title, "duration_s": round(dur, 2),
                  "cached": was_cached}
            if seg_stats is not None:
                # Additive fields (old clients ignore them): segment-level
                # reuse inside a re-rendered chapter.
                ev["segments"] = seg_stats["total"]
                ev["cached_segments"] = seg_stats["cached"]
            yield _emit(ev)

        route_notice = chapter_run.notice()
        if route_notice is not None:
            yield _emit({"type": "routing_notice", "status": route_notice[0],
                         "reason": route_notice[1]})

        if interrupted:
            logger.info("[%s] client disconnected — stopped after %d/%d chapters",
                        job_id, len(chapter_files), total)
            if job_store is not None:
                try:
                    # A client disconnect here is a user-initiated Stop, not a
                    # failure — record it as cancelled so job history reads right
                    # and the resumable state isn't mistaken for a broken render.
                    job_store.mark_cancelled(job_id)
                except Exception:
                    pass  # best-effort job history
            # Deliberately DO NOT clear the resume manifest: the rendered chapters
            # are cached, so Create-again / resume picks up where this left off.
            # Emit a terminal `stopped` event (a fully-disconnected client won't
            # receive it, but a same-origin proxy or a partial read still gets a
            # clean close instead of a dangling stream).
            yield _emit({"type": "stopped", "rendered": len(chapter_files),
                         "total": total, "cached_chapters": cached_n,
                         "failed_chapters": failed})
            return

        if not chapter_files:
            # Every chapter failed, so the render is over — this is the event the
            # UI turns into a toast, and it used to carry only the symptom
            # (#1321). Lead with the summary, then the cause; docs_topic/hint are
            # classified from the raw exception text, so prefixing the reason
            # afterwards cannot mis-route the deeplink.
            if last_chapter_exc is not None:
                ev = build_failure_event(last_chapter_exc, stage="audiobook_render")
                ev["reason"] = f"all {total} chapters failed to render — {ev['reason']}"
                ev["error"] = ev["reason"]
            else:
                ev = {"type": "error", "error": "all chapters failed to render",
                      "reason": "all chapters failed to render"}
            # Terminal failure — record it. This branch used to return without
            # touching job history, so the row stayed `running` forever: the next
            # startup read it as an interrupted job, and the retained manifest
            # offered a render that had already failed every chapter as
            # resumable (Greptile P1 on #1321). The manifest IS kept on purpose —
            # a failure whose cause the user can now see (a missing voice, an
            # engine that can't read the script) is worth retrying once fixed,
            # and the chapter cache is empty here so a retry costs nothing extra.
            if job_store is not None:
                try:
                    job_store.mark_failed(job_id, ev["reason"])
                except Exception:
                    pass  # best-effort job history; never block the stream
            yield _emit(ev)
            return

        yield _emit({"type": "assembling"})
        meta_path = os.path.join(work, "chapters.ffmeta")
        with open(meta_path, "w", encoding="utf-8") as f:
            f.write(build_ffmetadata(chapters_meta, global_meta=metadata))
        concat_path = os.path.join(work, "concat.txt")
        with open(concat_path, "w", encoding="utf-8") as f:
            f.write(build_concat_list(chapter_files))
        ext = "mp3" if (fmt or "").lower() == "mp3" else "m4b"
        out_name = f"{job_type}_{job_id}.{ext}"
        out_path = os.path.join(OUTPUTS_DIR, out_name)

        # Two-pass loudness master (#28): for a known preset, measure the
        # concatenated program first, then feed the measured values back into the
        # single mux encode. `measured is None` (skip OR any failure) → the mux
        # falls back to single-pass. Gated identically to the pure builders
        # (.lower(), no strip), so off/None/unknown/whitespace skip cleanly.
        measured = None
        norm = (loudness or "").lower()
        if norm in LOUDNESS_PRESETS:
            yield _emit({"type": "mastering", "preset": norm})
            from services.loudness import measure_loudness
            measured = await measure_loudness(ffmpeg, concat_path, norm, job_id=job_id)

        await run_ffmpeg(
            build_render_cmd(
                ffmpeg, concat_path, meta_path, out_path,
                fmt=ext, bitrate=bitrate, cover_path=_safe_cover_path(cover_path),
                loudness=loudness, measured=measured,
            ),
            job_id=job_id,
        )

        if job_store is not None:
            try:
                job_store.mark_done(job_id)
            except Exception:
                pass  # best-effort job history
        # The render finished — drop the resume manifest so this job is no longer
        # offered for resume.
        longform_resume.clear_manifest(job_type, job_id)
        total_s = sum(d for _, d in chapters_meta) / 1000.0
        done = {"type": "done", "output": out_name,
                "chapters": len(chapter_files), "duration_s": round(total_s, 2),
                "cached_chapters": cached_n, "failed_chapters": failed}
        # Say what this render IS, so the library can show more than a filename
        # (#2233). Additive keys; best-effort — a summary never fails a render.
        if title:
            done["title"] = str(title)[:200]
        try:
            # Only what is IN the file: chapters that failed are not summarised,
            # and the language is the one synthesis actually used.
            rendered = [c for i, c in enumerate(plan.chapters) if i not in set(failed)]
            done["summary"] = _render_summary(
                rendered, default_voice, voice_map, resolved_lang, fmt, opts)
        except Exception:
            logger.warning("longform: could not build the render summary", exc_info=True)
        # Loudness verdict only when a preset was requested — off/None paths keep
        # the exact legacy `done` shape (additive, old clients unaffected).
        if norm in LOUDNESS_PRESETS:
            p = LOUDNESS_PRESETS[norm]
            done["loudness"] = {
                "preset": norm, "target_i": p.i, "target_tp": p.tp,
                "two_pass": measured is not None,
                "measured_i": measured.input_i if measured else None,
            }
        yield _emit(done)
    except Exception as e:  # surface, don't 500 the stream
        logger.exception("[%s] longform render failed", job_id)
        if job_store is not None:
            try:
                job_store.mark_failed(job_id, str(e))
            except Exception:
                pass  # best-effort job history
        # Generic message only — don't leak the stack/exception text to the client.
        yield _emit({"type": "error", "error": "render failed (see backend log)"})


async def _public_longform_stream(plan, **render_kwargs):
    """Keep generator diagnostics local if setup fails before its own guard."""
    try:
        async for event in _render_longform_sse(plan, **render_kwargs):
            yield event
    except asyncio.CancelledError:
        raise
    except Exception as exc:
        from core.public_errors import public_failure

        error = public_failure(
            logger,
            "Longform response stream failed",
            exc,
            response="Render failed; check the backend log for details.",
        )
        yield f"data: {json.dumps({'type': 'error', 'error': error})}\n\n"


@router.post("/audiobook")
async def audiobook_synthesize(req: AudiobookRequest, request: Request = None):
    """Synthesize a chapterized audiobook from a script, streaming SSE progress."""
    plan = parse_audiobook_script(req.text, default_voice=req.default_voice)
    # `request` is injected by FastAPI on the HTTP path (the default only applies
    # to a direct in-process call, e.g. a unit test); its disconnect poll is what
    # lets Stop cancel the render mid-book (#1216).
    return StreamingResponse(
        _public_longform_stream(
            plan, default_voice=req.default_voice, language=req.language,
            fmt=req.format, bitrate=req.bitrate,
            loudness=req.loudness, cover_path=req.cover_path, metadata=req.metadata,
            lexicon=req.lexicon, opts=_expressive_opts(req), voice_map=req.voice_map,
            job_type="audiobook",
            is_disconnected=request.is_disconnected if request is not None else None,
        ),
        media_type="text/event-stream",
    )


# ── Shared longform render: Stories (and any future front door) post a plan ──

class LongformSpan(BaseModel):
    voice_id: str | None = None
    text: str
    pause_ms_after: int = 0
    speed: float | None = None


class LongformChapter(BaseModel):
    title: str = ""
    spans: list[LongformSpan] = []


class LongformRenderRequest(ExpressiveMixin):
    chapters: list[LongformChapter] = []
    default_voice: str | None = None
    language: str | None = None        # None/"Auto" → profile language, else autodetect (#505)
    bitrate: str = "128k"
    format: str = "m4b"
    loudness: str | None = None
    cover_path: str | None = None
    metadata: dict | None = None
    lexicon: dict | None = None
    # Cast map {[voice:NAME] → profile id} (#1217); absent/empty = today's render.
    voice_map: dict[str, str] | None = None


@router.post("/longform/render")
async def longform_render(req: LongformRenderRequest, request: Request = None):
    """Render a pre-built chapter/span plan (the Stories Editor's compiled
    cast+lines) through the shared chapterized renderer — same resume, loudness,
    cover, metadata, and output formats as the Audiobook job."""
    from services.audiobook import AudiobookPlan, Chapter, Span

    if len(req.chapters) > _MAX_CHAPTERS:
        raise HTTPException(status_code=422, detail=f"too many chapters (max {_MAX_CHAPTERS})")

    chapters = []
    for i, c in enumerate(req.chapters):
        # Keep a span if it has text to speak OR a pause to render (pause-only
        # spans carry inter-line silence with empty text).
        spans = [Span(voice_id=s.voice_id, text=(s.text or "").strip(),
                      pause_ms_after=max(0, int(s.pause_ms_after)), speed=s.speed)
                 for s in c.spans if ((s.text and s.text.strip()) or s.pause_ms_after > 0)]
        if spans:
            chapters.append(Chapter(title=c.title or f"Chapter {i + 1}", spans=spans))
    plan = AudiobookPlan(chapters=chapters)
    return StreamingResponse(
        _public_longform_stream(
            plan, default_voice=req.default_voice, language=req.language,
            fmt=req.format, bitrate=req.bitrate,
            loudness=req.loudness, cover_path=req.cover_path, metadata=req.metadata,
            lexicon=req.lexicon, opts=_expressive_opts(req), voice_map=req.voice_map,
            job_type="story",
            is_disconnected=request.is_disconnected if request is not None else None,
        ),
        media_type="text/event-stream",
    )


# ── Durable resume: interrupted longform renders ────────────────────────────


def _chapters_done(job_id: str) -> int:
    """Count chapters that finished rendering, from the job's persisted events.
    Best-effort (0 if unavailable) — used only to show resume progress."""
    try:
        from core import job_store
        n = 0
        for ev in job_store.events_since(job_id, 0, limit=100_000):
            try:
                if json.loads(ev["payload"]).get("type") == "chapter":
                    n += 1
            except (ValueError, KeyError, TypeError):
                continue
        return n
    except Exception:
        return 0


@router.get("/audiobook/jobs")
def list_resumable_jobs() -> dict:
    """List interrupted longform renders that can be resumed — a work dir that
    still holds a resume manifest (a job left mid-render by a crash/quit). The
    ids come from scanning the filesystem, so the UI can offer one-click resume."""
    from core import job_store

    out = []
    for e in longform_resume.scan_resumable():
        jid = e["job_id"]
        manifest = longform_resume.load_manifest_file(e["manifest_path"]) or {}
        job = job_store.get(jid) or {}
        out.append({
            "job_id": jid,
            "type": e["job_type"],
            "status": job.get("status", "interrupted"),
            "title": manifest.get("title", ""),
            "total_chapters": manifest.get("total_chapters", 0),
            "chapters_done": _chapters_done(jid),
            "created_at": job.get("created_at"),
        })
    return {"jobs": out}


@router.post("/audiobook/resume/{job_id}")
async def resume_longform(job_id: str, request: Request = None):
    """Resume an interrupted longform render from its persisted manifest. The
    already-rendered chapters are content-addressed in the shared cache, so they
    return instantly — only the unrendered chapters synthesize again. Streams the
    same SSE event shape as the original render, under the original job_id."""
    from services.audiobook import AudiobookPlan, Chapter, Span

    # Find the requested job among the trusted filesystem scan (every path there
    # is os.listdir-sourced, never request input) and read its manifest via the
    # scan's own trusted path — the request job_id is used ONLY to *select* an
    # entry, never to build a path. No request-controlled value reaches a file
    # operation (CodeQL py/path-injection-safe).
    entry = next((e for e in longform_resume.scan_resumable()
                  if e["job_id"] == job_id), None)
    if entry is None:
        raise HTTPException(status_code=404, detail="No resumable job for that id")
    manifest = longform_resume.load_manifest_file(entry["manifest_path"])
    if not manifest:
        raise HTTPException(status_code=404, detail="No resume manifest for that job")

    chapters = [
        Chapter(title=c.get("title", ""),
                spans=[Span(**s) for s in c.get("spans", [])])
        for c in manifest["plan"]
    ]
    plan = AudiobookPlan(chapters=chapters)
    p = manifest.get("params", {})
    # Retire the interrupted job's manifest (trusted scan path) so it stops
    # showing as resumable once we've kicked off the fresh-id resume.
    longform_resume.discard_manifest_file(entry["manifest_path"])
    # Resume under a FRESH job id (job_id=None → a server uuid in the renderer).
    # The chapter cache is content-addressed (keyed by chapter content, not the
    # job id), so the already-rendered chapters still hit instantly — only the
    # unrendered ones synthesize. Using a fresh id means the request's job_id
    # never names a work dir / output file (defence-in-depth path-injection).
    return StreamingResponse(
        _public_longform_stream(
            plan, default_voice=p.get("default_voice"), language=p.get("language"),
            fmt=p.get("fmt", "m4b"), bitrate=p.get("bitrate", "128k"),
            loudness=p.get("loudness"), cover_path=p.get("cover_path"),
            metadata=p.get("metadata"), lexicon=p.get("lexicon"),
            opts=ExpressiveOptions.from_manifest(p.get("expressive")),
            voice_map=p.get("voice_map"),
            job_type=entry["job_type"],
            is_disconnected=request.is_disconnected if request is not None else None,
        ),
        media_type="text/event-stream",
    )
