"""Single lifecycle surface for loaded models (MM2-04).

Before this, ``GET /model/loaded`` and ``POST /model/unload`` each hand-rolled
enumeration/dispatch across three worlds — the in-process TTS+ASR model
(``model_manager``), the diarization pipeline, and subprocess sidecars
(``subprocess_backend``). This module owns that logic so the routers are thin
delegations and there's one place to reason about model lifecycle.

Response shapes are preserved exactly — the frontend (hooks.ts model status +
the flush dropdown) depends on ``{models, count}`` and
``{unloaded, success, ...}``.
"""
from __future__ import annotations

import logging
import os
from typing import Optional

import services.model_manager as mm
from services.model_manager import get_best_device

logger = logging.getLogger("omnivoice.model_lifecycle")


def _tts_vram_mb() -> float:
    """Best-effort allocated VRAM for the in-process model. Accurate on CUDA,
    sparse on MPS, 0 elsewhere — degrade gracefully, never raise."""
    try:
        torch = mm._lazy_torch()
        if torch.cuda.is_available():
            return torch.cuda.memory_allocated() / (1024 ** 2)
        if hasattr(torch.backends, "mps") and torch.backends.mps.is_available():
            driver = getattr(torch.mps, "driver_allocated_memory", None)
            if driver:
                return driver() / (1024 ** 2)
    except Exception:
        pass
    return 0.0


def _asr_device() -> str:
    """Where the ASR pipe actually lives, rather than a hardcoded 'cpu'."""
    pipe = getattr(mm.model, "_asr_pipe", None)
    for attr in ("device",):
        dev = getattr(pipe, attr, None)
        if dev is not None:
            return str(dev)
    return "cpu"


def _backend_device(backend: object) -> str:
    """Report the device this backend can actually use.

    The host's best device is not evidence that a CPU-only runtime uses it.
    Prefer load-time facts, then constrain the fallback by the backend's
    declared compatibility contract.
    """
    for attr in ("_device", "device", "execution_device"):
        value = getattr(backend, attr, None)
        if value is not None and not callable(value):
            text = str(value).strip()
            if text:
                return text
    compat = tuple(getattr(type(backend), "gpu_compat", ("cpu",)))
    preferred = get_best_device()
    family = preferred.split(":", 1)[0]
    if family in compat:
        return preferred
    return "cpu" if "cpu" in compat else (compat[0] if compat else "unknown")


def _active_tts_id() -> Optional[str]:
    """Configured TTS engine id, or None if it can't be resolved. Attribution
    is advisory — a prefs/import hiccup must never break /model/loaded."""
    try:
        from services.tts_backend import active_backend_id
        return active_backend_id()
    except Exception:
        return None


def _tts_attribution(engine_id: str, active: Optional[str]) -> dict:
    """Per-entry engine attribution for TTS-family models. A model can stay
    resident in VRAM after the user switches engines (freed only by unload/
    idle-evict), so the panel needs to know which entry synthesis actually
    routes to. ``is_active_engine`` is None when the active id is unknown."""
    return {
        "engine_id": engine_id,
        "is_active_engine": (engine_id == active) if active is not None else None,
    }


def list_loaded() -> dict:
    """Enumerate every currently-loaded model. Shape: ``{"models": [...],
    "count": n}`` with per-model id/name/checkpoint/device/vram_mb/unloadable
    (+ optional ``note``)."""
    models: list[dict] = []
    degraded_sources: list[str] = []
    active_tts = _active_tts_id()

    # 1. In-process TTS model (VoiceStudio)
    if mm.model is not None:
        try:
            device = str(next(mm.model.parameters()).device) if hasattr(mm.model, "parameters") else get_best_device()
        except Exception:
            device = get_best_device()
        models.append({
            "id": "tts",
            "name": "VoiceStudio TTS",
            "checkpoint": mm.resolve_omnivoice_checkpoint(),  # #693: effective checkpoint, not a leaked raw value
            "device": device,
            "vram_mb": round(_tts_vram_mb(), 1),
            "unloadable": True,
            **_tts_attribution("omnivoice", active_tts),
        })

    # 2. ASR (WhisperX) — co-loaded with and released alongside the TTS model.
    #    Honest reporting (MM2-03): the device is read from the pipe, and the
    #    dead "unload" button is explained by a note rather than left silent.
    if mm.model is not None and getattr(mm.model, "_asr_pipe", None) is not None:
        models.append({
            "id": "asr",
            "name": "WhisperX ASR",
            "checkpoint": os.environ.get("ASR_MODEL", "Systran/faster-whisper-large-v3"),
            "device": _asr_device(),
            "vram_mb": 0,
            "unloadable": False,
            "note": "released with the TTS model",
        })

    # 3. Diarization pipeline
    if mm._diar_pipeline is not None:
        models.append({
            "id": "diarization",
            "name": "Pyannote Diarization",
            "checkpoint": "pyannote/speaker-diarization-3.1",
            "device": get_best_device(),
            "vram_mb": 0,
            "unloadable": True,
        })

    # 4. Subprocess engine sidecars — each holds a process (and on GPU, VRAM)
    #    until idle-reaped. VRAM is reported by the child itself when available
    #    (MM2-08); 0 means CPU-only or not-yet-measured. Enumeration must never
    #    break the panel.
    try:
        from services.subprocess_backend import list_live_sidecars
        for s in list_live_sidecars():
            models.append({
                "id": f"sidecar:{s['id']}",
                "name": f"{s['id']} (sidecar)",
                "checkpoint": s["id"],
                "device": get_best_device(),
                "vram_mb": round(float(s.get("vram_mb") or 0), 1),
                "unloadable": True,
                **_tts_attribution(s["id"], active_tts),
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for subprocess sidecars")
        degraded_sources.append("sidecars")

    # 5. In-process engine instances that hold a model (mlx-audio, cosyvoice,
    #    voxcpm2, kittentts, …). These live in the generate path's instance
    #    cache, separate from the VoiceStudio core above — and were INVISIBLE here
    #    until now, so a resident non-VoiceStudio engine (up to a few GB) didn't
    #    show in the panel at all. Report each that currently holds a model.
    #    VRAM isn't self-reported by these engines → 0 (unmeasured), same
    #    convention as a CPU/uninstrumented sidecar. Enumeration is best-effort.
    try:
        from api.routers.engines import _ENGINE_INSTANCES
        from services.tts_backend import OmniVoiceBackend

        for cls, inst in list(_ENGINE_INSTANCES.items()):
            if cls is OmniVoiceBackend:
                continue  # the shared core is already section 1 (mm.model)
            if not any(getattr(inst, a, None) is not None
                       for a in getattr(inst, "_MODEL_ATTRS", ("_model", "_tts"))):
                continue  # instance exists but hasn't loaded its weights
            eid = getattr(cls, "id", cls.__name__)
            models.append({
                "id": f"engine:{eid}",
                "name": getattr(inst, "display_name", None) or f"{eid} (engine)",
                "checkpoint": eid,
                "device": get_best_device(),
                "vram_mb": 0,  # not self-reported by in-process engines
                "unloadable": True,
                **_tts_attribution(eid, active_tts),
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for in-process engines")
        degraded_sources.append("engines")

    # The local generation path keeps its selected backend in a separate
    # active-instance slot. Non-OmniVoice models held there must be visible as
    # well; otherwise Model Settings can report an empty runtime while an
    # alternate TTS model still occupies memory.
    try:
        import services.tts_backend as tb
        from services.subprocess_backend import SubprocessBackend

        inst = getattr(tb, "_active_instance", None)
        eid = getattr(tb, "_active_instance_id", None)
        if (
            inst is not None
            and eid
            and eid != "omnivoice"
            and not isinstance(inst, SubprocessBackend)
            and any(
                getattr(inst, attr, None) is not None
                for attr in getattr(inst, "_MODEL_ATTRS", ("_model", "_tts"))
            )
        ):
            identity = getattr(inst, "model_identity", None)
            models.append({
                "id": f"active-engine:{eid}",
                "name": getattr(inst, "display_name", None) or f"{eid} (engine)",
                "checkpoint": identity() if callable(identity) else eid,
                "device": _backend_device(inst),
                "vram_mb": 0,
                "unloadable": True,
                **_tts_attribution(eid, active_tts),
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for active TTS engine")
        degraded_sources.append("active-engine")

    # 6. The warm capture/dictation ASR singleton — resident until idle-released
    #    (#1101 class). Held separately from the co-loaded WhisperX ASR above.
    try:
        import services.asr_backend as ab

        cap = getattr(ab, "_capture_backend", None)
        if cap is not None:
            model_label = getattr(getattr(cap, "spec", None), "label", None)
            models.append({
                "id": "capture-asr",
                "name": f"{model_label or getattr(cap, 'display_name', type(cap).__name__)} (dictation)",
                "checkpoint": getattr(ab, "_capture_backend_key", None) or type(cap).__name__,
                "device": _backend_device(cap),
                "vram_mb": 0,
                "unloadable": True,
                "note": "released after the idle timeout",
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for dictation")
        degraded_sources.append("dictation")

    # 6b. The warm transcription ASR (accurate / reference / OpenAI route /
    #     voice convert), released after the idle timeout or before TTS loads.
    try:
        import services.asr_backend as ab

        warm = ab.warm_transcription_backend()
        if warm is not None:
            name = getattr(warm, "_model_name", None)
            models.append({
                "id": "transcription-asr",
                "name": f"{getattr(warm, 'display_name', type(warm).__name__)} (transcription)",
                "checkpoint": name if isinstance(name, str) and name else getattr(warm, "id", type(warm).__name__),
                "device": _backend_device(warm),
                "vram_mb": 0,
                "unloadable": True,
                "note": "released after the idle timeout",
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for transcription")
        degraded_sources.append("transcription")

    # 7. Offline translation can remain resident when the user opts out of the
    # default post-job release. Keep it visible and manually unloadable.
    try:
        from api.routers import dub_translate as dt

        if getattr(dt, "_nllb_model", None) is not None:
            models.append({
                "id": "translation:nllb",
                "name": "NLLB-200 Translation",
                "checkpoint": dt._NLLB_REPO_ID,
                "device": str(getattr(dt, "_nllb_device", None) or "cpu"),
                "vram_mb": 0,
                "unloadable": True,
                "note": "released after translation by default",
            })
    except Exception:
        logger.warning("Loaded-model inventory unavailable for translation")
        degraded_sources.append("translation")

    # System memory snapshot — free/total RAM (and VRAM on a dedicated GPU) plus
    # a low-memory advisory, so the panel can show pressure instead of leaving
    # the 16 GB-Mac OOM class invisible until the backend dies.
    system: dict = {}
    try:
        from services.memory_budget import available_memory, low_memory_warning

        system = available_memory()
        warn = low_memory_warning()
        if warn:
            system["warning"] = warn
    except Exception:
        pass

    return {"models": models, "count": len(models), "system": system,
            "degraded_sources": degraded_sources}


async def unload(model_id: str) -> dict:
    """Unload one model by id. Preserves the original per-id response shapes.

    ``tts`` | ``diarization`` | ``sidecar:<id>`` | ``sidecars``. Raises
    ValueError for an unknown id (router maps to HTTP 400).

    Releasing device memory blocks (gc over the weights, cudaFree waiting on
    the device, sidecar shutdown), so it runs on a worker thread: on the event
    loop it stalled every other request, health checks included (#3558)."""
    if model_id == "tts":
        async with mm._model_lock:
            if await mm.run_release(mm.unload_shared_model):
                return {"unloaded": "tts", "success": True}
        return {"unloaded": "tts", "success": False, "reason": "not loaded"}
    return await mm.run_release(_unload_blocking, model_id)


def _unload_blocking(model_id: str) -> dict:
    """Every non-``tts`` branch of :func:`unload`; runs off the event loop."""
    if model_id == "sidecars" or model_id.startswith("sidecar:"):
        from services.subprocess_backend import unload_all_sidecars, unload_sidecar
        n = unload_all_sidecars() if model_id == "sidecars" else unload_sidecar(model_id.split(":", 1)[1])
        return {"unloaded": model_id, "success": n > 0, "count": n,
                **({} if n > 0 else {"reason": "not running or busy"})}

    if model_id == "diarization":
        if mm._diar_pipeline is not None:
            mm._diar_pipeline = None
            mm.free_vram()
            return {"unloaded": "diarization", "success": True}
        return {"unloaded": "diarization", "success": False, "reason": "not loaded"}

    # The warm dictation ASR (#1247, same defect). It is listed with
    # ``"unloadable": True`` and had no branch either — found by the contract
    # test written for the engine case, which is the whole reason that test
    # enumerates the listing instead of hard-coding ids.
    if model_id == "capture-asr":
        import services.asr_backend as ab

        if getattr(ab, "_capture_backend", None) is None:
            return {"unloaded": model_id, "success": False, "reason": "not loaded"}
        # idle_s=0 → release now. Still declines while a dictation stream holds
        # a lease; yanking the model out from under an open session is exactly
        # what the lease exists to prevent.
        if ab.release_idle_capture_backend(0.0):
            return {"unloaded": model_id, "success": True}
        return {"unloaded": model_id, "success": False, "reason": "in use by dictation"}

    if model_id == "transcription-asr":
        import services.asr_backend as ab

        if not ab.release_transcription_backend(0.0):
            return {"unloaded": model_id, "success": False, "reason": "not loaded"}
        mm.free_vram()
        return {"unloaded": model_id, "success": True}

    if model_id == "translation:nllb":
        from api.routers import dub_translate as dt

        if getattr(dt, "_nllb_model", None) is None:
            return {"unloaded": model_id, "success": False, "reason": "not loaded"}
        dt._unload_nllb()
        return {"unloaded": model_id, "success": True}

    # In-process engines (#1247). `list_loaded_models` has advertised these as
    # `engine:<id>` with `"unloadable": True` since they were made visible in
    # the panel — but this dispatcher never grew a branch for them, so pressing
    # Unload on any of those rows answered `400 Unknown model id:
    # engine:kittentts`. The engines already implement `unload()`; only the
    # routing was missing.
    if model_id.startswith("engine:"):
        engine_id = model_id.split(":", 1)[1]
        from api.routers.engines import _ENGINE_INSTANCES

        for cls, inst in list(_ENGINE_INSTANCES.items()):
            if (getattr(cls, "id", cls.__name__)) != engine_id:
                continue
            held = any(
                getattr(inst, attr, None) is not None
                for attr in getattr(inst, "_MODEL_ATTRS", ("_model", "_tts"))
            )
            if not held:
                return {"unloaded": model_id, "success": False, "reason": "not loaded"}
            inst.unload()  # idempotent by contract; frees device caches itself
            return {"unloaded": model_id, "success": True}
        return {"unloaded": model_id, "success": False, "reason": "not loaded"}

    if model_id.startswith("active-engine:"):
        engine_id = model_id.split(":", 1)[1]
        import services.tts_backend as tb

        if (
            getattr(tb, "_active_instance", None) is None
            or getattr(tb, "_active_instance_id", None) != engine_id
        ):
            return {"unloaded": model_id, "success": False, "reason": "not loaded"}
        tb.reset_active_backend()
        return {"unloaded": model_id, "success": True}

    raise ValueError(f"Unknown model id: {model_id}")


async def unload_all() -> dict:
    """Release shared/alternate TTS, diarisation, sidecars, dictation and translation."""
    results = {}
    model_ids = [
        "tts", "diarization", "sidecars", "capture-asr", "transcription-asr", "translation:nllb",
    ]
    try:
        model_ids.extend(
            entry["id"]
            for entry in list_loaded()["models"]
            if entry["id"].startswith(("engine:", "active-engine:"))
        )
    except Exception:
        logger.warning("Could not enumerate optional engines during global unload")
    for mid in dict.fromkeys(model_ids):
        try:
            results[mid] = await unload(mid)
        except Exception as exc:  # noqa: BLE001
            results[mid] = {"unloaded": mid, "success": False, "reason": str(exc)}
    return {"unloaded_all": True, "results": results}


def free_vram() -> None:
    """One import surface for callers that just want to drop GPU caches."""
    mm.free_vram()
