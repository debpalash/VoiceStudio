"""Single-active-TTS-engine memory discipline.

Only one TTS engine's model stays resident at a time. When the generate path
resolves an engine, every *other* resident engine is unloaded first — so the
previous engine's model is handed back instead of stacking in memory until GC.

Why this matters (measured on a 16 GB M2): a generate on ``omnivoice`` leaves
its ~2.8 GB core model resident; a subsequent generate on ``mlx-audio`` loaded
that engine's model **on top** (footprint 3.9 GB → 4.3 GB, both resident),
because the two live in different caches with no coordination — the core in
``model_manager.model``, the rest in ``engines._ENGINE_INSTANCES`` (which was
never unloaded). That accumulation is the baseline that pushes a 16 GB machine
into the memory pressure behind the "Can't reach the local backend" OOM deaths.

Default on. Opt out with ``OMNIVOICE_SINGLE_ENGINE_RESIDENT=0`` on machines with
RAM to spare (keeping several engines warm avoids the reload latency on an A/B
switch — ~8 s for the VoiceStudio core, ~1–2 s for the lighter engines).
"""
from __future__ import annotations

import logging
from core.logging_utils import log_safe
import os

logger = logging.getLogger("omnivoice.engine_memory")

_OFF = {"0", "false", "no", "off"}


def single_engine_resident() -> bool:
    """Whether the one-engine-at-a-time policy is active (default True)."""
    return (os.environ.get("OMNIVOICE_SINGLE_ENGINE_RESIDENT", "1").strip().lower()
            not in _OFF)


def _evict_instance_cache(keep_cls) -> list[str]:
    """Unload + drop every cached engine instance except ``keep_cls``.

    Operates on the per-request instance cache the generate path shares with the
    engine health route (``engines._ENGINE_INSTANCES``). Each engine's
    ``unload()`` frees its heavy model (the ABC default clears ``_MODEL_ATTRS``
    and empties the device cache; subprocess engines reap their sidecar). Never
    raises — a stuck unload must not block the generation that triggered it."""
    evicted: list[str] = []
    try:
        from api.routers.engines import _ENGINE_INSTANCES
    except Exception:  # pragma: no cover — router import should always succeed
        return evicted
    from services import tts_backend

    with tts_backend._ENGINE_CACHE_LOCK:
        for cls, inst in list(_ENGINE_INSTANCES.items()):
            if cls is keep_cls or tts_backend._ENGINE_IN_USE.get(cls):
                continue
            try:
                inst.unload()
            except Exception:  # noqa: BLE001
                logger.warning("evict: %s.unload() failed", getattr(cls, "id", cls.__name__),
                               exc_info=True)
            _ENGINE_INSTANCES.pop(cls, None)
            evicted.append(getattr(cls, "id", cls.__name__))
    return evicted


async def evict_other_tts_engines(keep_id: str) -> list[str]:
    """Unload every resident TTS engine except ``keep_id`` and return their ids.

    Spans both stores a TTS model can live in: the VoiceStudio core singleton
    (``model_manager.model``, freed under its async lock when we're switching
    *away* from it) and the generic engine instance cache. A no-op when the
    policy is off or nothing else is resident, so steady-state single-engine use
    pays nothing — only an actual switch evicts. Never raises."""
    if not single_engine_resident():
        return []

    from services import tts_backend

    try:
        keep_cls = tts_backend.get_backend_class(keep_id)
    except ValueError:
        return []  # Reject invalid overrides before changing any resident model.
    evicted: list[str] = []

    # The VoiceStudio core singleton — only when the incoming engine isn't it.
    if keep_id != "omnivoice":
        try:
            import services.model_manager as mm

            async with mm._model_lock:
                with tts_backend._ENGINE_CACHE_LOCK:
                    core_busy = tts_backend._ENGINE_IN_USE.get(tts_backend.OmniVoiceBackend)
                    if not core_busy and mm.unload_shared_model():
                        evicted.append("omnivoice")
        except Exception:  # noqa: BLE001
            logger.warning("evict: VoiceStudio core unload failed", exc_info=True)

    # Every other in-process / sidecar engine instance.
    evicted.extend(_evict_instance_cache(keep_cls))

    with tts_backend._ENGINE_CACHE_LOCK:
        active = tts_backend._active_instance
        if (active is not None and type(active) is not keep_cls
                and not tts_backend._ENGINE_IN_USE.get(type(active))):
            active_id = tts_backend._active_instance_id
            tts_backend.reset_active_backend()
            if active_id and active_id not in evicted:
                evicted.append(active_id)

    if evicted:
        logger.info("single-engine eviction: freed %s (keeping %s)", log_safe(evicted), log_safe(keep_id))
    return evicted
