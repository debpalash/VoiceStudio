"""Voice-gallery archetype API.

Serves the catalog of *designed* voice archetypes from ``core.archetypes`` and
renders previews and saves their recipes as voice profiles on demand.

Design notes
============
* All heavy imports (the TTS model, torch) are deferred into the render
  functions so this module imports cleanly in test/CI environments without
  model weights. The pure endpoints (categories / list / get) and the preview
  *cache-hit* path never touch the model.
* Rendering reuses generation.py's proven ``_run_inference`` / ``get_model`` /
  ``_safe_torchaudio_save`` rather than re-deriving the ``model.generate``
  signature — one inference code path, one place to keep correct.
* Previews are cached on disk keyed by a hash of (instruct, language), so two
  archetypes that resolve to the same voice share a cache file and the cold
  render only happens once per distinct voice.
* That same key names the pre-rendered clips in the opt-in voice gallery
  (``services.gallery``), which is consulted BEFORE the engine so a fresh
  install can hear voices before the 2.4 GB checkpoint finishes downloading.
  Gallery files win over a local render of the same key — but only for
  ``/preview``. ``/use`` stores the design recipe without rendering; synthesis
  loads the model only when the user asks to generate audio.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Awaitable, Callable, Optional

from fastapi import APIRouter, Body, HTTPException, Query, Depends
from fastapi.responses import FileResponse

from core import archetypes
from core.audio_validation import is_playable_wav, resolve_regular_file
from core.config import OUTPUTS_DIR, VOICES_DIR
from services import gallery
from services.model_acceptance import ModelLicenceNotAccepted
from core.browser_guard import reject_cross_site_get

logger = logging.getLogger("omnivoice.archetypes")

router = APIRouter()

_PREVIEW_DIR = Path(OUTPUTS_DIR) / "archetype_previews"
# Seed fixed so repeated renders of the same archetype are reproducible
# (mirrors scripts/render_demos_omnivoice.py).
_PREVIEW_SEED = 42
# Diffusion steps for previews. 16 under-converges: certain (script, seed)
# points — notably the "social" sample script at seed 42 — collapse to a
# degenerate tonal buzz (The Hype Host / Podcaster / Vlogger, issue follow-up).
# 32 reliably converges to speech across the gallery's instruct/script space
# at a one-time (cached) render cost.
_PREVIEW_NUM_STEP = 32
# Reject near-pure tonal artifacts using mean framed spectral flatness.
# Calibrated against the tracked speech demos exercised by
# test_archetype_preview_quality.py: the quietest (Mandarin dubbing, 44.1 kHz)
# measures ~7.7e-6, while the worst tested tonal buzz measures ~3.3e-9.
# 1e-7 leaves >10x margin on both sides without rejecting low-flatness speech.
_DEGENERATE_FLATNESS = 1e-7


def _preview_key(a: dict) -> str:
    # Deterministic cache key, not a security digest. SHA-256 (not SHA-1) so the
    # SAST scanners don't flag it as a weak hash.
    return hashlib.sha256(
        f"{a['instruct']}|{a['language']}".encode("utf-8")
    ).hexdigest()[:16]


def _design_profile_values(a: dict) -> tuple[str, str]:
    """Canonical instruct + complete picker state for a designed archetype."""
    return a["instruct"], json.dumps(a["attrs"], sort_keys=True)


def _profile_audio_path(ref_audio_path: object) -> Optional[Path]:
    """Resolve only a regular, non-symlinked file inside ``VOICES_DIR``."""
    return resolve_regular_file(VOICES_DIR, ref_audio_path)


def _materialized_audio_is_current(row, a: dict) -> bool:
    """Whether an existing row still has the sample described by its metadata."""
    expected_filename = _profile_audio_filename(row["id"])
    path = _profile_audio_path(row["ref_audio_path"])
    return bool(
        row["ref_audio_path"] == expected_filename
        and is_playable_wav(path)
        and row["instruct"] == a["instruct"]
        and row["language"] == a["language"]
        and row["ref_text"] == a["sample_script"]
        and row["seed"] == _PREVIEW_SEED
    )


def _profile_audio_filename(profile_id: str) -> str:
    safe_id = (
        profile_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id or "")
        else hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()[:16]
    )
    return f"{safe_id}.wav"


def _archetype_personality(a: dict) -> str:
    return f"archetype:{a['id']}"


def _legacy_archetype_profile(conn, a: dict):
    """Adopt only a row that an older archetype materializer could have made."""
    row = conn.execute(
        "SELECT * FROM voice_profiles WHERE personality=? LIMIT 1",
        (a["id"],),
    ).fetchone()
    if row is None:
        return None
    expected_audio = _profile_audio_filename(row["id"])
    try:
        states_match = (
            not row["vd_states"] or json.loads(row["vd_states"]) == a["attrs"]
        )
    except (TypeError, ValueError):
        states_match = False
    if (
        row["ref_audio_path"] == expected_audio
        and row["instruct"] == a["instruct"]
        and row["language"] == a["language"]
        and row["ref_text"] == a["sample_script"]
        and row["seed"] == _PREVIEW_SEED
        and row["kind"] in (None, "", "clone", "design")
        and not row["is_locked"]
        and not row["verified_own_voice"]
        and states_match
    ):
        return row
    return None


def _is_materialized_archetype_row(row, a: dict) -> bool:
    """Recognize deferred or previously rendered rows owned by this materializer."""
    try:
        states_match = json.loads(row["vd_states"]) == a["attrs"]
    except (TypeError, ValueError):
        return False
    return bool(
        row["personality"] == _archetype_personality(a)
        and row["kind"] == "design"
        and row["seed"] == _PREVIEW_SEED
        and row["ref_audio_path"] in (None, "", _profile_audio_filename(row["id"]))
        and row["instruct"] == a["instruct"]
        and row["language"] == a["language"]
        and row["ref_text"] == a["sample_script"]
        and states_match
        and not row["is_locked"]
        and not row["verified_own_voice"]
    )


def _existing_archetype_profile(conn, a: dict):
    rows = conn.execute(
        "SELECT * FROM voice_profiles WHERE personality=? ORDER BY created_at, id",
        (_archetype_personality(a),),
    ).fetchall()
    owned = next((row for row in rows if _is_materialized_archetype_row(row, a)), None)
    return owned if owned is not None else _legacy_archetype_profile(conn, a)


async def _render_wav_atomic(a: dict, out_path: Path, *, prefix: str = ".render-") -> Path:
    """Render and validate a WAV before atomically replacing *out_path*."""
    audio_path = Path(out_path)
    audio_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = audio_path.parent / f"{prefix}{uuid.uuid4().hex}.wav"
    try:
        await _render_archetype_wav(a, tmp_path)
        if not is_playable_wav(tmp_path):
            raise RuntimeError("the voice engine produced an invalid WAV")
        os.replace(tmp_path, audio_path)
    finally:
        with __import__("contextlib").suppress(OSError):
            tmp_path.unlink()
    return audio_path


# One render per output file. A media element may re-request, a user may
# re-click, and two views may preview the same voice at once — each used to
# queue its own render on the GPU pool. The shared task is shielded from the
# request, so a cancelled or disconnected client still lands the clip on disk
# and the next click serves it instead of starting over.
_renders: dict[str, asyncio.Task] = {}


def _forget_render(key: str, task: asyncio.Task) -> None:
    if _renders.get(key) is task:
        del _renders[key]
    if not task.cancelled():
        task.exception()  # retrieved: an orphaned failure must not warn at GC


async def _render_once(key: str, render: Callable[[], Awaitable[Any]]) -> Any:
    """Run *render* once per *key*; concurrent callers await the same result."""
    task = _renders.get(key)
    if task is None:
        task = asyncio.ensure_future(render())
        _renders[key] = task
        task.add_done_callback(lambda t: _forget_render(key, t))
    return await asyncio.shield(task)


def _heal_archetype_profile(conn, row, a: dict, audio_filename: Optional[str]) -> None:
    """Refresh an owned profile without starting synthesis."""
    instruct, vd_states = _design_profile_values(a)
    conn.execute(
        "UPDATE voice_profiles SET kind='design', instruct=?, vd_states=?, language=?, "
        "ref_text=?, seed=?, ref_audio_path=?, personality=? WHERE id=?",
        (
            instruct, vd_states, a["language"], a["sample_script"], _PREVIEW_SEED,
            audio_filename, _archetype_personality(a), row["id"],
        ),
    )


# A non-empty script is always required — synthesizing empty text yields
# silence. Every archetype carries a use-case script, but guard the render path
# too so a malformed archetype can never drive a blank render.
_FALLBACK_SCRIPT = "Here's a quick sample of this voice so you can hear how it sounds."


def _is_blank_audio(audio_tensor) -> bool:
    """True if a render came back effectively silent / empty / non-finite.

    After ``normalize_audio``'s silence-floor guard a dead render stays at the
    noise floor instead of being amplified to hiss, so a near-zero peak is a
    reliable "no audible speech" signal. A real, normalized clip peaks near
    -2 dBFS (~0.79), so the 0.02 threshold has a wide margin and won't flag
    legitimately quiet (e.g. whisper) voices.
    """
    try:
        import torch

        t = audio_tensor if isinstance(audio_tensor, torch.Tensor) else torch.as_tensor(audio_tensor)
        if t.numel() == 0:
            return True
        t = t.detach().to("cpu", dtype=torch.float32)
        if not torch.isfinite(t).all():
            return True
        return t.abs().max().item() < 0.02
    except Exception:  # never let the checker itself block a render
        return False


_WIN_LEN = 1024
_WIN_STEP = _WIN_LEN // 2
_LOG_FLOOR = 1e-12
_PAUSE_FRACTION = 1e-4  # frames below this share of the loudest one are pauses


def _spectral_flatness(audio_tensor) -> Optional[float]:
    """Average spectral flatness over speech-carrying windows, or ``None``.

    Flatness of one window is geometric mean / arithmetic mean of its power
    spectrum: close to 0 for a tone, towards 1 for noise. Windowing the clip
    (instead of one transform over all of it) keeps the number independent of
    duration; pauses are dropped so their noise floor doesn't pull it up.
    Too-short, non-finite or otherwise unmeasurable audio yields ``None``,
    and so does any error: this check must never stop a render.
    """
    try:
        import torch

        x = torch.as_tensor(audio_tensor).detach().to("cpu", dtype=torch.float32)
        if x.ndim > 1:
            x = x.mean(dim=0)
        x = x.flatten()
        if x.numel() < _WIN_LEN or not bool(torch.isfinite(x).all()):
            return None
        windows = x.unfold(0, _WIN_LEN, _WIN_STEP) * torch.hann_window(_WIN_LEN)
        spectra = torch.fft.rfft(windows, dim=1).abs().square() + _LOG_FLOOR
        loudness = spectra.sum(dim=1)
        spectra = spectra[loudness > loudness.max() * _PAUSE_FRACTION]
        if spectra.shape[0] == 0:
            return None
        flat = torch.exp(spectra.log().mean(dim=1)) / spectra.mean(dim=1)
        return float(flat.mean())
    except Exception:  # never let the checker itself block a render
        return None


def _is_unusable_audio(audio_tensor) -> bool:
    """True if a render is silent/non-finite OR a degenerate tonal buzz.

    The blank guard alone misses the tonal-collapse failure mode: a buzz is
    *loud* (peaks near -2 dBFS after normalize), so it sails past the silence
    floor and — without this — gets cached and served as the preview.
    """
    if _is_blank_audio(audio_tensor):
        return True
    flatness = _spectral_flatness(audio_tensor)
    return flatness is not None and flatness < _DEGENERATE_FLATNESS


async def _render_archetype_wav(a: dict, out_path: Path, *, allow_model_load: bool = True) -> None:
    """Render an archetype's sample script to ``out_path`` using the live engine.

    Reuses generation.py's inference primitives so there is exactly one TTS code
    path. Heavy deps are imported here, never at module load. If the engine
    returns a blank/silent clip we retry once with a different seed, then fail
    loudly — a blank preview or voice profile must never be cached or saved.
    """
    from api.routers.generation import (  # noqa: WPS433 — intentional lazy import
        get_model,
        _run_inference,
        run_on_gpu_pool_guarded,
        _safe_torchaudio_save,
    )

    # Save-time samples are optional: an unload after the residency probe must
    # not turn persistence into a cold load. Explicit previews retain loading.
    model = await get_model() if allow_model_load else await get_model(allow_load=False)
    language = a["language"]
    if language in (None, "", "Auto"):
        language = None
    text = (a.get("sample_script") or "").strip() or _FALLBACK_SCRIPT

    def _infer(seed: int):
        return _run_inference(
            model,          # _model
            text,           # text
            language,       # language
            None,           # ref_audio_path (design mode — no reference)
            None,           # ref_text
            a["instruct"],  # instruct
            None,           # duration
            _PREVIEW_NUM_STEP,  # num_step
            2.0,            # guidance_scale
            1.0,            # speed
            None,           # t_shift
            True,           # denoise
            True,           # postprocess_output
            None,           # layer_penalty_factor
            None,           # position_temperature
            None,           # class_temperature
            seed,           # seed
            "broadcast",    # effect_preset
        )

    # Bounded + pool-reset on hang so a wedged preview render can't starve the
    # GPU pool and brick the backend (#730 class). Budget comes from the shared
    # length-scaled helper (#1190) instead of the flat 300s default.
    from services.model_manager import generate_timeout_s
    _budget = generate_timeout_s(text, engine=model)
    # A design profile's sample uses the seed the user saved it with (#3254);
    # archetype previews keep the fixed preview seed.
    seed = a.get("seed")
    seed = _PREVIEW_SEED if seed is None else int(seed)
    audio_tensor = await run_on_gpu_pool_guarded(
        lambda: _infer(seed), what="Archetype preview generate",
        timeout=_budget)
    if _is_unusable_audio(audio_tensor):
        # Blank OR a degenerate tonal buzz — retry once on a different seed to
        # step off the bad diffusion trajectory. Static message only: the
        # archetype id is request-derived (CodeQL log-injection); the seed is an
        # int, safe to log.
        logger.warning("Archetype rendered unusable at seed %d — retrying once", seed)
        audio_tensor = await run_on_gpu_pool_guarded(
            lambda: _infer(seed + 1), what="Archetype preview generate",
            timeout=_budget)
    if _is_unusable_audio(audio_tensor):
        raise RuntimeError("the voice engine returned no audible audio for this archetype")

    # Invisible provenance mark (#1169), tensor stage, before the WAV is
    # persisted: this one site covers BOTH archetype outputs — the served
    # preview clip (GET /archetypes/{id}/preview) and the synthetic reference
    # WAV a materialized profile keeps in VOICES_DIR (played back via the
    # profile preview route). Runs in the GPU pool like generate's finalize;
    # never raises (degrades to unmarked on failure). User-uploaded/recorded
    # reference audio is human speech and is never marked — this only touches
    # audio the engine synthesized.
    # Runs on the dedicated watermark pool (#1190): AudioSeal embedding is CPU
    # work that holds no VRAM, so it must not occupy a GPU worker ahead of the
    # next generate on 1-worker hosts.
    from services.watermark import mark_synthetic_async
    audio_tensor = await mark_synthetic_async(
        audio_tensor, model.sampling_rate,
        context="archetypes.render",
        timeout=generate_timeout_s(""),
    )

    out_path.parent.mkdir(parents=True, exist_ok=True)
    _safe_torchaudio_save(str(out_path), audio_tensor, model.sampling_rate)


def _no_voice_model_downloaded() -> bool:
    """True only on a *positive* "no TTS weights on this machine" answer.

    Fails open on purpose: the cache probes are best-effort (a user-managed
    clone outside the HF layout is invisible to them), and telling someone with
    a working engine to go download a model is worse than saying nothing. Only
    a catalog we could read, with not one TTS repo cached, earns the offline
    message.
    """
    try:
        from api.routers.setup.models import get_model_catalog, is_cached

        tts = [m for m in get_model_catalog().all if m.get("role") == "TTS"]
        return bool(tts) and not any(is_cached(m["repo_id"]) for m in tts)
    except Exception:
        return False


def _preview_source(a: dict) -> tuple[str, str]:
    """Which path ``/preview`` will take for *a*, and what to tell the user.

    Replaces the old "see Settings → Logs → Backend" advice, which asked a user
    who wanted to hear a voice to go read a log file. The three states that
    actually differ are: we already have the audio (gallery), we can make it
    (render — say so, it takes a moment), and we can neither fetch nor make it
    (no model — the one state with an action attached).
    """
    key = _preview_key(a)
    if gallery.cached_preview(key) is not None:
        return "gallery", (
            "Pre-rendered preview from the voice gallery — a fixed reference "
            "rendering, not a render from your current engine."
        )
    if is_playable_wav(_PREVIEW_DIR / f"{key}.wav"):
        return "cached", ""
    if _no_voice_model_downloaded():
        return "no_model", (
            "You're offline and no voice model is downloaded yet — download "
            "one from the engine's Weights list in Model Catalogue."
        )
    return "rendering", "Rendering this preview on your machine — it may take a moment."


# ── Read endpoints (no model) ─────────────────────────────────────────────────
# NOTE: declare the literal `/archetypes/categories` before `/archetypes/{id}`
# so it isn't swallowed by the path-parameter route.
@router.get("/archetypes/categories")
def list_categories():
    """The seven use-case categories the gallery is organized by."""
    return archetypes.categories()


# ── Voice-gallery (pre-rendered previews) ─────────────────────────────────────
# Declared above `/archetypes/{archetype_id}` for the same reason as
# `/categories`: keep literal paths out of the path-parameter route's reach.
@router.get("/archetypes/previews/status")
def preview_gallery_status():
    """Consent state, coverage and freshness for the Settings line."""
    return gallery.status()


@router.put("/archetypes/previews")
async def set_preview_gallery(enabled: bool = Body(..., embed=True)):
    """Turn pre-rendered previews on or off.

    Turning it ON is the user's explicit yes to an outbound call, and is the
    only thing that ever starts one — there is no on-install background fetch.
    The featured set is pulled right here so the yes has a visible effect;
    failures are silent by design (``fetch_featured`` swallows them) and leave
    previews rendering locally.
    """
    state = gallery.set_enabled(enabled)
    if enabled:
        state = await gallery.fetch_featured()
    return state


@router.post("/archetypes/previews/check")
async def check_preview_gallery():
    """Manual "check now" — bypasses the 24 h throttle, never the signature."""
    return await gallery.check_for_updates(force=True)


@router.get("/archetypes")
def list_archetypes_endpoint(
    q: Optional[str] = None,
    use_case: Optional[str] = None,
    gender: Optional[str] = None,
    age: Optional[str] = None,
    pitch: Optional[str] = None,
    accent: Optional[str] = None,
    whisper: Optional[bool] = None,
    lang: Optional[str] = None,
    featured: Optional[bool] = None,
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
):
    """Filtered, paginated view over the archetype catalog.

    ``q`` is a free-text substring match over the archetype name/instruct so a
    voice picker can search the *entire* several-hundred-voice catalog by typing
    (the facet filters alone can't reach a specific voice by name). Content-free
    and local — it just narrows the in-memory catalog.
    """
    items = archetypes.list_archetypes(
        q=q, use_case=use_case, gender=gender, age=age, pitch=pitch,
        accent=accent, whisper=whisper, lang=lang, featured=featured,
    )
    total = len(items)
    page = items[offset:offset + limit]
    return {"total": total, "limit": limit, "offset": offset, "items": page}


@router.get("/archetypes/{archetype_id}")
def get_archetype_endpoint(archetype_id: str):
    a = archetypes.get_archetype(archetype_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Archetype not found")
    return a


# ── Render endpoints (model-gated) ────────────────────────────────────────────
@router.get("/archetypes/{archetype_id}/preview/state")
def preview_archetype_state(archetype_id: str):
    """Where the next ``/preview`` for this archetype would come from.

    Touches neither the model nor the network, so a picker can label a voice
    ("may take a moment", "download a model first") *before* it commits to a
    request that may take 40 seconds or fail.
    """
    a = archetypes.get_archetype(archetype_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Archetype not found")
    source, message = _preview_source(a)
    return {"source": source, "message": message}


@router.get("/archetypes/{archetype_id}/preview", dependencies=[Depends(reject_cross_site_get)])
async def preview_archetype(
    archetype_id: str,
    local: bool = Query(False, description="Bypass gallery audio after a client decode failure"),
):
    """Serve a short preview clip — from the gallery, the cache, or the engine."""
    a = archetypes.get_archetype(archetype_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Archetype not found")

    key = _preview_key(a)
    # Gallery first, and only for /preview: these bytes are audio we can prove
    # the provenance of, so they beat a local render of the same key. A miss
    # (offline, disabled, key not published) is silent — we just render.
    gallery_path = None if local else gallery.cached_preview(key)
    if gallery_path is None and not local:
        gallery_path = await gallery.fetch_preview(key)
    if gallery_path is not None:
        # Nothing else in the app polls, so the daily refresh hangs off the
        # request that proves previews are being used. Fire-and-forget.
        gallery.maybe_refresh_in_background()
        return FileResponse(
            str(gallery_path),
            media_type="audio/mpeg",
            headers={"Cache-Control": "no-cache",
                     "X-OmniVoice-Preview-Source": "gallery"},
        )

    cache_path = _PREVIEW_DIR / f"{key}.wav"
    if not is_playable_wav(cache_path):
        try:
            async def _render_preview() -> None:
                if not is_playable_wav(cache_path):  # landed while we queued
                    await _render_wav_atomic(a, cache_path, prefix=".preview-")

            await _render_once(str(cache_path), _render_preview)
        except ModelLicenceNotAccepted:
            raise  # the structured 403 that opens the acceptance dialog
        except Exception as e:  # model missing / OOM / inference failure
            logger.error("Archetype preview render failed", exc_info=True)
            # Two different failures, two different answers. Without a model
            # there is nothing to read in a log — there is something to do.
            if _no_voice_model_downloaded():
                detail = (
                    "You're offline and no voice model is downloaded yet — "
                    "download one from the engine's Weights list in Model Catalogue. (Or turn "
                    "on pre-rendered voice previews in Settings → Storage.)"
                )
            else:
                detail = (
                    "Couldn't render a preview right now — the voice engine "
                    f"reported: {e}"
                )
            raise HTTPException(status_code=503, detail=detail)
    # no-cache (not no-store): the URL is stable but its bytes change when an
    # archetype's preview is re-rendered, so force the client to revalidate
    # against the ETag instead of serving a stale cached clip indefinitely.
    return FileResponse(
        str(cache_path),
        media_type="audio/wav",
        headers={"Cache-Control": "no-cache",
                 "X-OmniVoice-Preview-Source": "local"},
    )


@router.post("/archetypes/{archetype_id}/use")
def use_archetype(archetype_id: str, name: Optional[str] = Query(None)):
    """Save an archetype recipe as a reusable design profile without rendering."""
    a = archetypes.get_archetype(archetype_id)
    if a is None:
        raise HTTPException(status_code=404, detail="Archetype not found")

    from core import event_bus
    from core.db import db_conn

    profile_name = (name or a["name"]).strip() or a["name"]
    with db_conn() as conn:
        # Serialize deduplication: personality is intentionally not globally
        # unique because other import formats may contain arbitrary values.
        conn.execute("BEGIN IMMEDIATE")
        existing = _existing_archetype_profile(conn, a)
        if existing is not None:
            audio_filename = (
                existing["ref_audio_path"]
                if _materialized_audio_is_current(existing, a)
                else None
            )
            _heal_archetype_profile(conn, existing, a, audio_filename)
            result = {"profile_id": existing["id"], "name": existing["name"]}
            action = "updated"
        else:
            profile_id = str(uuid.uuid4())[:8]
            instruct, vd_states = _design_profile_values(a)
            conn.execute(
                "INSERT INTO voice_profiles "
                "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
                "created_at, kind, vd_states) VALUES (?, ?, NULL, ?, ?, ?, ?, ?, ?, 'design', ?)",
                (
                    profile_id, profile_name, a["sample_script"], instruct,
                    a["language"], _PREVIEW_SEED, _archetype_personality(a),
                    time.time(), vd_states,
                ),
            )
            result = {"profile_id": profile_id, "name": profile_name}
            action = "created"

    event_bus.emit("profiles", {"action": action, "id": result["profile_id"]})
    return result
