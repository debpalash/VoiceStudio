"""
Engines router — Phase 3 wiring.

Exposes the three adapter registries (TTS, ASR, LLM) so the Settings UI can
render an engine picker + availability reasons.

    GET  /engines                       → { tts, asr, llm }
    GET  /engines/{family}              → list of backends
    POST /engines/select                → persist a backend choice in prefs.json
    GET  /engines/{engine_id}/health    → spawn-or-ping for SubprocessBackend
                                          subclasses; ``is_available()`` for
                                          in-process backends (Plan 02-04)

Environment variables (`OMNIVOICE_TTS_BACKEND`, `OMNIVOICE_ASR_BACKEND`,
`OMNIVOICE_LLM_BACKEND`) still win over the UI choice so power-users can pin
a backend without Settings silently undoing it.
"""
import asyncio
import logging
import os
import threading
from time import perf_counter

from fastapi import APIRouter, Depends, HTTPException, Request
from huggingface_hub import utils as hf_utils
from huggingface_hub.errors import HFValidationError
from pydantic import BaseModel, Field

from api.dependencies import require_admin, require_admin_action, require_desktop, is_loopback
from core import prefs
from core.engine_licenses import LICENSE_GATED_ENGINES
from services import tts_backend, asr_backend, llm_backend, translation_engines
from services.audio_dsp import list_effect_presets
from api.schemas import EffectPresetsResponse
from api.public_engine_metadata import public_backends, public_unavailability

router = APIRouter()
logger = logging.getLogger("omnivoice.engines_api")

_FAMILIES = {
    "tts": (tts_backend, "tts_backend"),
    "asr": (asr_backend, "asr_backend"),
    "llm": (llm_backend, "llm_backend"),
}


def _catalogue_active_id(family: str, module) -> str:
    """Return the active id represented by the public engine catalogue."""
    active = module.active_backend_id()
    if family != "tts" or active != "omnivoice-subprocess":
        return active

    from core.device_caps import detect_host_caps

    try:
        return "omnivoice" if detect_host_caps().family == "mps" else active
    except Exception:
        return active


def _family_payload(family: str, module):
    """Public inventory plus whether an environment pin owns this family."""
    active = _catalogue_active_id(family, module)
    model = None
    if family == "asr":
        model = asr_backend._offline_asr_repo(active)
    elif family == "llm" and active != "off":
        model = llm_backend.get_active_llm_backend().model_name
    elif family == "tts":
        if active in {"omnivoice", "omnivoice-subprocess"}:
            from services.model_manager import resolve_omnivoice_checkpoint
            model = resolve_omnivoice_checkpoint()
        elif active == "mlx-audio":
            from core import prefs
            cls = tts_backend.MLXAudioBackend
            key = prefs.resolve("mlx_audio_model_id", env="OMNIVOICE_MLX_AUDIO_MODEL", default=cls.DEFAULT_MODEL_KEY)
            model = cls.CURATED_MODELS.get(key, key)
        else:
            instance = getattr(tts_backend, "_active_instance", None)
            if instance is not None and getattr(tts_backend, "_active_instance_id", None) == active:
                model = instance.model_identity()
    backends = public_backends(module.list_backends())
    if family == "tts":
        from services import settings_store

        for backend in backends:
            engine_id = backend.get("id")
            if engine_id == active:
                backend["supported_language_names"] = tts_backend.language_options(active)
            if engine_id == active == "mlx-audio":
                # Constructor resolves model preferences only; never loads weights.
                backend["supports_cloning"] = tts_backend.MLXAudioBackend().supports_cloning
            if engine_id in LICENSE_GATED_ENGINES:
                backend["license_required"] = True
                try:
                    backend["license_accepted"] = settings_store.get_license_accepted(engine_id)
                except Exception:
                    logger.warning(
                        "Could not read license acceptance for %s",
                        engine_id,
                        exc_info=True,
                    )
                    backend["license_accepted"] = False
    return {
        # MPS hides the explicit compatibility row, so legacy configs report
        # the visible canonical equivalent as active to picker consumers.
        "active": active,
        "active_model": model,
        "env_override": bool(os.environ.get(f"OMNIVOICE_{family.upper()}_BACKEND")),
        "backends": backends,
    }

def _is_hf_repo_id(value: str) -> bool:
    """Validate the route's ``owner/repo`` contract in bounded time."""
    if not isinstance(value, str) or len(value) > 96 or value.count("/") != 1:
        return False
    try:
        hf_utils.validate_repo_id(value)
    except (HFValidationError, TypeError):
        return False
    return True


def _request_install_capability(payload, request):
    allowed = bool(request and request.client and is_loopback(request.client.host))
    result = dict(payload)
    result["backends"] = [dict(entry) for entry in payload["backends"]]
    for entry in result["backends"]:
        if entry.get("one_click_install") and not allowed:
            entry["one_click_install"] = False
            entry["local_install_required"] = True
    return result


@router.get("/engines")
def list_all_engines(request: Request):
    return {
        "tts": _request_install_capability(_family_payload("tts", tts_backend), request),
        "asr": _family_payload("asr", asr_backend),
        "llm": _family_payload("llm", llm_backend),
    }


@router.get("/engines/tts")
def list_tts_backends(request: Request):
    return _request_install_capability(_family_payload("tts", tts_backend), request)


@router.get(
    "/engines/{engine_id}/disk-usage",
    dependencies=[Depends(require_admin_action)],
)
def engine_disk_usage(engine_id: str):
    """Measure owned engine bytes only when a catalogue row is opened."""
    try:
        tts_backend.get_backend_class(engine_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Unknown TTS engine")
    from services.engine_disk_usage import disk_usage_for

    return disk_usage_for(engine_id)


@router.get("/engines/asr")
def list_asr_backends():
    return _family_payload("asr", asr_backend)


@router.get("/engines/llm")
def list_llm_backends():
    return _family_payload("llm", llm_backend)


@router.get("/engines/effects/presets", response_model=EffectPresetsResponse)
def list_effects_presets():
    """Return available DSP effect presets for the dub pipeline.

    Each preset is a named chain of audio effects (EQ, compressor, reverb, etc.)
    that can be applied to generated TTS audio on a per-segment basis.
    """
    return {"presets": list_effect_presets()}


@router.get("/engines/diarisation")
def diarisation_status():
    """Describe the selected local diarisation runtime without loading weights."""
    from services.diarization_runtime import (
        PYANNOTE,
        SORTFORMER,
        selected_backend,
        sortformer_status,
    )

    selected = selected_backend()
    native = selected == SORTFORMER
    options = []
    from api.routers.setup.models import KNOWN_MODELS, cache_is_complete, is_cached
    pyannote_repo = "pyannote/speaker-diarization-3.1"
    spec = next(model for model in KNOWN_MODELS if model["repo_id"] == pyannote_repo)
    pyannote_installed = is_cached(pyannote_repo) and cache_is_complete(spec)
    pyannote_reason = None if pyannote_installed else "Install the pyannote model bundle"
    options.append({
        "id": PYANNOTE,
        "label": "pyannote 3.1",
        "model": pyannote_repo,
        "installed": pyannote_installed,
        "reason": pyannote_reason,
    })

    native_status = sortformer_status()
    native_installed = native_status["installed"]
    native_model = native_status["model"]
    native_reason = native_status["reason"]
    options.append({
        "id": SORTFORMER,
        "label": "Sortformer v1 (audio.cpp)",
        "model": native_model,
        "model_installed": native_status["model_installed"],
        "runtime_installed": native_status["runtime_installed"],
        "installed": native_installed,
        "reason": native_reason,
    })

    if native:
        from services.diarization_native import is_running
        return {"active": SORTFORMER, "label": "Sortformer v1 (audio.cpp)",
                "model": native_model, "installed": native_installed, "loaded": False,
                "model_installed": native_status["model_installed"],
                "runtime_installed": native_status["runtime_installed"],
                "busy": is_running(), "reason": native_reason, "options": options}
    from services import model_manager
    return {"active": PYANNOTE, "label": "pyannote 3.1", "model": pyannote_repo,
            "installed": pyannote_installed,
            "loaded": model_manager._diar_pipeline is not None, "reason": pyannote_reason,
            "options": options}


class DiarisationSelection(BaseModel):
    engine_id: str


@router.post("/engines/diarisation/select", dependencies=[Depends(require_admin)])
def select_diarisation_engine(request: DiarisationSelection):
    """Persist an installed diarisation runtime; environment overrides still win."""
    from services.diarization_runtime import SORTFORMER, select_backend, selected_backend

    status = diarisation_status()
    option = next(
        (item for item in status["options"] if item["id"] == request.engine_id),
        None,
    )
    if option is None:
        raise HTTPException(404, "Unknown diarisation engine")
    if not option["installed"]:
        raise HTTPException(409, option.get("reason") or "Install this diarisation engine first")
    select_backend(request.engine_id)
    if request.engine_id == SORTFORMER:
        # Native Sortformer is stateless. Release a previously loaded pyannote
        # pipeline so Engine Ready cannot hide stale accelerator memory.
        from services import model_manager
        model_manager.unload_diarization_pipeline()
    return {
        "active": selected_backend(),
        "env_override": bool(os.environ.get("OMNIVOICE_DIARIZATION_BACKEND")),
    }


@router.get("/engines/translation")
def list_translation_engines():
    """Translation engines with per-engine pip-package availability.

    Separate from the tts/asr/llm "family" endpoints because these are
    pip-installable on demand rather than select-from-what's-available.
    The UI uses this to show a one-click Install chip when the user picks
    an engine whose Python dependency isn't importable yet.
    """
    return {
        "active": prefs.get("translation_backend", "argos"),
        "engines": [
            {**entry, "availability_reason": public_unavailability(entry.get("availability_reason"))}
            for entry in translation_engines.list_engines()
        ],
        "sandboxed": translation_engines.is_frozen(),
    }


class TranslationSelection(BaseModel):
    engine_id: str


class ArgosPackRequest(BaseModel):
    source_lang: str | None = None
    target_langs: list[str] = Field(min_length=1, max_length=32)
    job_id: str | None = None


def _argos_pack_request(request: ArgosPackRequest) -> tuple[str, list[str]]:
    source = request.source_lang
    if not source and request.job_id:
        from api.routers.dub_core import _get_job

        job = _get_job(request.job_id)
        source = job.get("source_lang") if job else None
    if not source:
        raise HTTPException(422, "Transcribe the source before installing its language pack")
    return source, request.target_langs


@router.post(
    "/engines/translation/argos/packs/status",
    dependencies=[Depends(require_admin)],
)
def argos_pack_status(request: ArgosPackRequest):
    source, targets = _argos_pack_request(request)
    try:
        return translation_engines.argos_pack_status(source, targets)
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post(
    "/engines/translation/argos/packs/install",
    dependencies=[Depends(require_admin)],
)
async def install_argos_packs(request: ArgosPackRequest):
    source, targets = _argos_pack_request(request)
    try:
        return await asyncio.to_thread(
            translation_engines.install_argos_packs,
            source,
            targets,
        )
    except (ImportError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.post("/engines/translation/select", dependencies=[Depends(require_admin)])
def select_translation_engine(request: TranslationSelection):
    entry = translation_engines.get_engine(request.engine_id)
    if not entry:
        raise HTTPException(404, "Unknown translation engine")
    if not translation_engines.is_installed(request.engine_id):
        raise HTTPException(409, "Install this translation engine before selecting it")
    if not translation_engines.is_ready(request.engine_id):
        raise HTTPException(409, "Configure this translation provider before selecting it")
    prefs.set_("translation_backend", request.engine_id)
    return {"active": request.engine_id}


@router.post(
    "/engines/translation/{engine_id}/install",
    dependencies=[Depends(require_admin)],
)
async def install_translation_engine(engine_id: str):
    entry = translation_engines.get_engine(engine_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown translation engine: {engine_id!r}")
    if translation_engines.is_frozen():
        raise HTTPException(
            status_code=400,
            detail=(
                "Engine install is disabled in the packaged build — the "
                "bundled Python environment is read-only and signed. Run the "
                "source/dev install (`uv sync`) if you need to add an engine."
            ),
        )
    pkg = entry.get("pip_package")
    if not pkg:
        return {"status": "already_installed", "engine": engine_id, "reason": "no pip package required"}
    if translation_engines.is_installed(engine_id):
        return {"status": "already_installed", "engine": engine_id}
    rc, out = await translation_engines.run_pip(["install", pkg])
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"pip install {pkg} failed ({rc}): {out[-1000:]}")
    # Probe again so the response reflects post-install reality; site-packages
    # is visible immediately but importlib may have cached a failure.
    import importlib
    importlib.invalidate_caches()
    ok = translation_engines.is_installed(engine_id)
    return {
        "status": "installed" if ok else "installed_but_probe_failed",
        "engine": engine_id,
        "package": pkg,
        "log_tail": out[-800:],
        "restart_required": not ok,
    }


@router.delete(
    "/engines/translation/{engine_id}",
    dependencies=[Depends(require_admin)],
)
async def uninstall_translation_engine(engine_id: str):
    entry = translation_engines.get_engine(engine_id)
    if not entry:
        raise HTTPException(status_code=404, detail=f"Unknown translation engine: {engine_id!r}")
    if entry.get("builtin"):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{entry['display_name']} is built-in and cannot be uninstalled. "
                "It shares its Python dependency with core features."
            ),
        )
    if translation_engines.is_frozen():
        raise HTTPException(status_code=400, detail="Engine uninstall is disabled in packaged builds.")
    pkg = entry.get("pip_package")
    if not pkg:
        return {"status": "no_op", "engine": engine_id}
    # The builtin flag is a promise someone has to remember to make; this
    # check does not depend on it (#2019).
    blocked = translation_engines.uninstall_blocker(engine_id)
    if blocked:
        raise HTTPException(status_code=blocked[0], detail=blocked[1])
    rc, out = await translation_engines.run_pip(["uninstall", "-y", pkg])
    if rc != 0:
        raise HTTPException(status_code=500, detail=f"pip uninstall {pkg} failed ({rc}): {out[-1000:]}")
    return {"status": "uninstalled", "engine": engine_id, "package": pkg, "log_tail": out[-800:]}


# ── Checksummed native audio.cpp runtime install ───────────────────────────


@router.get(
    "/engines/audiocpp/runtime/install/status",
    dependencies=[Depends(require_admin)],
)
def audiocpp_runtime_install_status(request: Request):
    from services import audiocpp_runtime_install

    return {**audiocpp_runtime_install.status(), "install_allowed": bool(request.client and is_loopback(request.client.host))}


@router.post(
    "/engines/audiocpp/runtime/install",
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)
def install_audiocpp_runtime():
    from services import audiocpp_runtime_install

    try:
        return audiocpp_runtime_install.start_install()
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


# ── One-click sidecar-engine install (IndexTTS-2 & friends) ────────────────
#
# Sidecar engines (dedicated venv + source checkout + weights, isolated from
# the parent's transformers>=5.3) used to require four manual terminal steps.
# These routes drive services.sidecar_install: POST starts a resumable
# background job, GET polls its step-by-step status (the Model Catalogue
# Install button polls this), DELETE removes an app-managed install.
#
# Path namespace: /engines/sidecar/{engine_id}/… — NOT /engines/{engine_id}/…
# — because a dynamic segment there would shadow pre-existing literal routes
# (this router registers before sonitranslate's, so a dynamic
# POST /engines/{engine_id}/install would swallow
# POST /engines/sonitranslate/install). Mirrors the
# /engines/translation/{engine_id}/install namespace pattern.
#
# Desktop-only: installing spawns git/uv against mutable source and writes an
# editable environment. An API key does not make that supply-chain path safe to
# trigger remotely. The job runs fine in packaged builds: the venv lives under
# the user data dir, not inside the signed app bundle, and uv resolves via
# OMNIVOICE_BUNDLED_UV/PATH.


@router.post(
    "/engines/sidecar/{engine_id}/install",
    dependencies=[Depends(require_admin), Depends(require_desktop)],
)
def install_sidecar_engine(engine_id: str):
    """Start (or report) the one-click install for a sidecar engine.

    Returns ``{status: "started"|"already_running"|"already_installed"}``.
    404 for engines that have no sidecar installer — the response names the
    translation-engine route so a mis-aimed client can self-correct.
    """
    from services import sidecar_install
    try:
        return sidecar_install.start_install(engine_id)
    except sidecar_install.HostUnsupported as exc:
        # The engine has an installer, but not one that can work on this
        # machine. 409, not 404: the route is right, the host is the problem,
        # and the message (a VoiceStudio-owned sentence) says what to do.
        raise HTTPException(status_code=409, detail=str(exc))
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=(
                f"No one-click installer for engine {engine_id!r}. Sidecar "
                f"installers exist for: {sorted(sidecar_install.SPECS)}. "
                "(Translation engines install via POST "
                "/engines/translation/{id}/install.)"
            ),
        )


@router.get(
    "/engines/sidecar/{engine_id}/install/status",
    dependencies=[Depends(require_admin)],
)
def sidecar_install_status(engine_id: str, request: Request = None):
    """Step-by-step status of the sidecar install job (poll while running).

    Shape: ``{engine_id, installed, managed, install_dir, job}`` where job is
    null before the first run, else ``{state, steps[], log[], error,
    remediation, weights_progress, started_at, finished_at}``.
    """
    from services import sidecar_install
    try:
        return {**sidecar_install.get_status(engine_id), "install_allowed": bool(request and request.client and is_loopback(request.client.host))}
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"No one-click installer for engine {engine_id!r}.",
        )


@router.delete(
    "/engines/sidecar/{engine_id}/install",
    dependencies=[Depends(require_admin)],
)
def uninstall_sidecar_engine(engine_id: str):
    """Remove an app-managed sidecar install (checkout + venv + weights) and
    clear the persisted path. Refuses user-managed installs (a clone the user
    made themselves) and installs with a job still running."""
    from services import sidecar_install
    try:
        res = sidecar_install.uninstall(engine_id)
    except KeyError:
        raise HTTPException(
            status_code=404,
            detail=f"No one-click installer for engine {engine_id!r}.",
        )
    if res["status"] == "install_in_progress":
        raise HTTPException(status_code=409, detail="Install is still running — wait for it to finish.")
    if res["status"] == "not_managed":
        raise HTTPException(status_code=400, detail=res["detail"])
    return res


# ── Engine health-check (Plan 02-04 / ENGINE-06) ───────────────────────────
#
# The Compat Matrix UI's "Test engine" button calls into this endpoint so
# that users can verify a SubprocessBackend engine is alive without
# kicking off a full synthesize. For an in-process backend the check is a
# cheap ``is_available()`` round-trip; for a SubprocessBackend subclass
# the call spawns the sidecar (if not already up) and round-trips a ping
# frame. Result includes wall-clock latency so the UI can render
# "1234 ms — pong" inline next to the button.
#
# Admin-gated (T-02-13): only the local desktop frontend or an authenticated
# server-mode administrator may trigger a sidecar spawn through this endpoint.

# Engine instances cached for the lifetime of the FastAPI process so that
# repeated health checks don't spawn a new SubprocessBackend (each spawn
# allocates a sidecar venv probe + atexit hook). The cache is keyed by
# class to survive registry-sandbox tests that rebind ids transiently.
#
# It now lives in services.tts_backend — the worker executor needs the same
# warm instances and cannot import an API router without inverting the
# layering. This name is the SAME dict object, kept so the existing consumers
# (engine_memory eviction, model_lifecycle inventory/unload) go on working
# unchanged; rebinding it here would fork the cache in two.
_ENGINE_INSTANCES: dict[type, object] = tts_backend._ENGINE_INSTANCES

_get_engine_instance = tts_backend.get_engine_instance


def _resolve_engine_class(engine_id: str):
    """Look up ``engine_id`` across the tts/asr/llm registries.

    Returns the class or ``None`` if no family knows the id. Order is
    tts → asr → llm so the most-common case (TTS engine matrix) wins
    early. No collision risk today — all current ids are family-unique.
    """
    if engine_id in tts_backend._REGISTRY:
        # Managed installs keep the public id but run from a private venv.
        # Probe the same effective class that generation and the catalogue use,
        # not the unavailable in-process adapter stored in the raw registry.
        return tts_backend.get_backend_class(engine_id)
    for registry in (asr_backend._REGISTRY, llm_backend._REGISTRY):
        if engine_id in registry:
            return registry[engine_id]
    return None


@router.get(
    "/engines/{engine_id}/health",
    dependencies=[Depends(require_admin_action)],
)
def engine_health(engine_id: str):
    """Spawn-and-ping a SubprocessBackend; ``is_available()`` for the rest.

    Returns:
        { id, ok, message, latency_ms }

        Never raises through to a 500: backend diagnostics stay in the local
        log and the response carries a fixed failure message, so the UI can
        render a per-row failure without exposing private data. Unknown engine
        ids return 404.
    """
    cls = _resolve_engine_class(engine_id)
    if cls is None:
        raise HTTPException(
            status_code=404,
            detail=f"unknown engine id: {engine_id!r}",
        )

    t0 = perf_counter()
    # Stable exception class when the probe itself raised, None when it merely
    # returned not-available. Never the exception text — see the log line below.
    raised_class: str | None = None
    if hasattr(cls, "health_check"):
        # SubprocessBackend path — spawn sidecar (if not running) and ping.
        # ``health_check`` already swallows its own exceptions per Plan
        # 02-01's contract; we still wrap in a defensive try so a custom
        # subclass that violates the contract can't 500 the endpoint.
        try:
            instance = _get_engine_instance(cls)
            ok, msg = instance.health_check()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
            raised_class = type(exc).__name__
    else:
        # In-process backend — `is_available()` is the classmethod-level
        # liveness check. Cheap and side-effect-free for every shipping
        # backend (it imports the engine package, no model load).
        try:
            ok, msg = cls.is_available()
        except Exception as exc:
            ok, msg = False, f"{type(exc).__name__}: {exc}"
            raised_class = type(exc).__name__

    # Engine-owned output can contain much more than shaped HF tokens: local
    # paths, arbitrary credentials, source lines, or a nested traceback.
    from core.public_errors import public_engine_health

    latency_ms = (perf_counter() - t0) * 1000.0
    if not ok:
        # The response tells the user to "check the backend log for details",
        # and docs/engines/*.md asks a user diagnosing an unavailable engine to
        # copy that engine's log lines. The old line named neither the engine
        # nor anything about the probe, so neither instruction could be
        # followed (#1866).
        #
        # `probe=` reports what the PROBE DID, not what went wrong. It cannot
        # classify the cause: SubprocessBackend.health_check() swallows its own
        # exceptions per Plan 02-01's contract, so a dead sidecar and a package
        # that was never installed both arrive here as `returned-unavailable`.
        # Separating those needs structured failure metadata from the probes
        # themselves, which is a wider change than this one.
        #
        # Still no diagnostic text and still not the caller-supplied id: the
        # engine id comes off the resolved registry class and a raised probe
        # contributes only its exception class, the same shape
        # core.public_errors.public_failure() logs as `class=`.
        # tests/test_response_safety.py pins that boundary and passes
        # unchanged.
        #
        # The id is a class attribute off the registry rather than caller
        # input, but this line is a log-injection surface either way, so it is
        # flattened to a single token before it goes in.
        engine_label = str(getattr(cls, "id", None) or cls.__name__)
        engine_label = "".join(
            c if (c.isalnum() or c in "-_.") else "-" for c in engine_label
        )[:64]
        logger.warning(
            "Engine health check failed; engine=%s probe=%s, details withheld",
            engine_label or "unknown",
            f"raised:{raised_class}" if raised_class else "returned-unavailable",
        )
    return {
        "id": engine_id,
        "ok": bool(ok),
        "message": public_engine_health(bool(ok), msg),
        "latency_ms": latency_ms,
    }


# ── Real-synthesis self-test (in-process TTS engines) ──────────────────────
#
# ``/health`` above is a liveness/import probe — for an in-process backend it
# only calls ``is_available()`` and the UI labels the result "deps OK". This
# route goes one step further: for an AVAILABLE, IN-PROCESS TTS engine it runs
# a *tiny real synthesis* from a fixed short phrase and reports duration +
# sample-rate + sample count, proving the engine actually emits audio rather
# than merely importing. The Compat Matrix's "Self-test" button calls it.
#
# Guardrails (kept identical across macOS/Windows/Linux per the default-feature
# rule — the phrase, timeout and gating don't branch on OS):
#   * TTS family + available + in-process only. Subprocess engines keep their
#     spawn-and-ping ``health_check`` (a real synth there is a sidecar
#     cold-start — out of scope for a click-to-test affordance).
#   * Bounded wall-clock timeout (``OMNIVOICE_SELFTEST_TIMEOUT_S``, default 90s):
#     a runaway synth returns ``ok=False`` / ``timed_out=True`` instead of
#     hanging the Settings panel. The orphaned worker is best-effort daemon.
#   * A process-wide lock serialises self-tests so a click-storm can't stack
#     concurrent model loads.
#   * Only ever on user click (POST) — never on Settings load. Admin-gated.

# Deliberately short + ASCII so the synth stays CPU-cheap and the phrase never
# trips the no-hardcoded-CJK guard.
_SELFTEST_PHRASE = "VoiceStudio engine self test."
_SELFTEST_LOCK = threading.Lock()


def _selftest_timeout_s() -> float:
    try:
        return max(1.0, float(os.environ.get("OMNIVOICE_SELFTEST_TIMEOUT_S", "90")))
    except (TypeError, ValueError):
        return 90.0


def _sample_count(audio) -> int:
    """Total sample count of an engine's ``generate()`` return, tolerant of
    torch.Tensor / numpy.ndarray / list shapes. 0 when it can't be measured."""
    try:
        shape = getattr(audio, "shape", None)
        if shape is not None and len(shape) > 0:
            return int(shape[-1])
        return int(len(audio))
    except Exception:
        return 0


def _run_synth_bounded(backend, timeout_s: float) -> dict | None:
    """Run one tiny synthesis in a daemon thread, bounded by ``timeout_s``.

    Returns ``{"audio": .., "duration_ms": ..}`` on success, ``{"error": exc}``
    on a synth exception, or ``None`` when the timeout elapsed (worker left
    running best-effort — Python threads can't be force-killed)."""
    box: dict = {}

    def _worker():
        t0 = perf_counter()
        try:
            audio = backend.generate(_SELFTEST_PHRASE, language="en", num_step=8)
            box["audio"] = audio
        except Exception as exc:  # noqa: BLE001 — surfaced to the caller as ok=False
            box["error"] = exc
        finally:
            box["duration_ms"] = (perf_counter() - t0) * 1000.0

    th = threading.Thread(target=_worker, name="engine-selftest", daemon=True)
    th.start()
    th.join(timeout_s)
    if th.is_alive():
        return None
    return box


class SelfTestResponse(BaseModel):
    id: str
    ok: bool
    message: str
    duration_ms: float
    sample_rate: int | None = None
    num_samples: int | None = None
    audio_seconds: float | None = None
    timed_out: bool = False


@router.post(
    "/engines/{engine_id}/selftest",
    response_model=SelfTestResponse,
    dependencies=[Depends(require_admin)],
)
def engine_selftest(engine_id: str):
    """Run a bounded, real synthesis on an available in-process TTS engine.

    404 for an unknown TTS id; 400 when the engine is subprocess-isolated or
    not currently available (a real synth on either is meaningless). Never
    raises through to a 500 on a synth failure — the exception is captured into
    ``ok=False`` / ``message`` so the panel renders a per-row failure."""
    if engine_id not in tts_backend._REGISTRY:
        raise HTTPException(
            status_code=404,
            detail=f"unknown TTS engine id: {engine_id!r}",
        )
    cls = tts_backend._REGISTRY[engine_id]
    if getattr(cls, "_is_subprocess_isolated", False):
        raise HTTPException(
            status_code=400,
            detail=(
                f"{engine_id} is subprocess-isolated — self-test runs real "
                "synthesis for in-process engines only. Use Test engine "
                "(spawn-and-ping) for subprocess engines."
            ),
        )
    try:
        ok, msg = cls.is_available()
    except Exception as exc:  # noqa: BLE001
        ok, msg = False, f"{type(exc).__name__}: {exc}"
    if not ok:
        raise HTTPException(
            status_code=400,
            detail=(
                f"{engine_id} is not available: {tts_backend._mask_hf_tokens(msg)}. "
                "Install/enable the engine, then self-test."
            ),
        )

    timeout_s = _selftest_timeout_s()
    # Serialise so a click-storm can't stack concurrent model loads.
    with _SELFTEST_LOCK:
        backend = _get_engine_instance(cls)
        res = _run_synth_bounded(backend, timeout_s)

    if res is None:
        return SelfTestResponse(
            id=engine_id,
            ok=False,
            message=f"timed out after {timeout_s:.0f}s (model still loading?)",
            duration_ms=timeout_s * 1000.0,
            timed_out=True,
        )
    if "error" in res:
        exc = res["error"]
        return SelfTestResponse(
            id=engine_id,
            ok=False,
            message=tts_backend._mask_hf_tokens(f"{type(exc).__name__}: {exc}"),
            duration_ms=res.get("duration_ms", 0.0),
        )

    n = _sample_count(res.get("audio"))
    try:
        sr = int(getattr(backend, "sample_rate", 0) or 0) or None
    except Exception:
        sr = None
    secs = round(n / sr, 3) if (sr and n) else None
    return SelfTestResponse(
        id=engine_id,
        ok=n > 0,
        message="synthesized" if n > 0 else "engine returned no audio",
        duration_ms=res["duration_ms"],
        sample_rate=sr,
        num_samples=n or None,
        audio_seconds=secs,
    )


class SelectEngineRequest(BaseModel):
    family: str   # "tts" | "asr" | "llm"
    backend_id: str
    # Only meaningful for family="tts", backend_id="mlx-audio" (#981) — picks
    # which of mlx-audio's curated models is actually loaded. A curated key
    # ("kokoro") or a raw HF repo id ("mlx-community/Kokoro-82M-bf16") — the
    # same tolerance MLXAudioBackend.__init__ already has. Ignored otherwise.
    model_id: str | None = None


class SelectEngineResponse(BaseModel):
    family: str
    active: str
    env_override: bool
    # Routing verdict for the selected engine on THIS host (#21). Always present
    # so the UI can show a confirm/warning toast on a cpu_fallback pick without
    # branching on key presence; defaults match a legacy/degraded row.
    routing_status: str = "cpu_only"
    effective_device: str = "cpu"
    routing_reason: str | None = None


@router.post(
    "/engines/select",
    response_model=SelectEngineResponse,
    dependencies=[Depends(require_admin)],
)
def select_engine(req: SelectEngineRequest):
    """Persist a family's engine pick to prefs.json. Refuses unknown backends,
    backends whose deps aren't installed, AND backends that cannot run on THIS
    host's hardware (routing_status == "unavailable") — so the UI can't silently
    brick a pipeline by picking an engine that needs a GPU this machine lacks.
    A `cpu_fallback` pick is allowed (it runs, just slower) — only a hard
    `unavailable` is blocked. LLM is never routing-gated (its status is "n/a")."""
    family = _FAMILIES.get(req.family)
    if not family:
        raise HTTPException(400, f"Unknown family: {req.family}. Expected one of tts/asr/llm.")
    module, pref_key = family
    # MPS intentionally hides the redundant explicit OmniVoice sidecar from
    # the picker, but existing scripts and saved preferences may still submit
    # that supported compatibility id directly.
    rows = (
        module.list_backends(include_hidden=True)
        if req.family == "tts"
        else module.list_backends()
    )
    available = {b["id"]: b for b in rows}
    if req.backend_id not in available:
        raise HTTPException(400, f"Unknown {req.family} backend: {req.backend_id!r}")
    entry = available[req.backend_id]
    if not entry["available"]:
        reason = entry.get("reason") or "unavailable"
        raise HTTPException(400, f"Backend {req.backend_id} not ready: {reason}")
    # Host-routing gate (no silent CPU fallback). `.get` is defensive so an
    # older/legacy payload without routing keys still selects cleanly.
    if entry.get("routing_status") == "unavailable":
        why = entry.get("routing_reason") or "requires a GPU this host doesn't have"
        raise HTTPException(
            400,
            f"Backend {req.backend_id} can't run on this machine: {why}. "
            f"Pick an engine with a CPU path, or one that supports this host's GPU.",
        )
    # #981: mlx-audio multiplexes 7+ curated models behind one backend id —
    # persist the model pick alongside the backend id so the UI can actually
    # select which curated model gets loaded (previously it always defaulted
    # to Kokoro no matter what the user downloaded in the engine's Weights list in Model Catalogue).
    if req.family == "tts" and req.backend_id == "mlx-audio" and req.model_id is not None:
        known_keys = tts_backend.MLXAudioBackend.CURATED_MODELS
        # Accept a curated key OR a raw HF repo id ("owner/name") — the same
        # tolerance MLXAudioBackend.__init__ already has for power users.
        # Anything else (typo'd key, malformed id) is rejected outright
        # rather than silently persisted as a "custom repo" that then fails
        # to resolve at load time.
        if req.model_id not in known_keys and not _is_hf_repo_id(req.model_id):
            raise HTTPException(
                400,
                "Unknown mlx-audio model. Expected a curated model key or a "
                "Hugging Face repo ID like 'owner/name'.",
            )
        prefs.set_("mlx_audio_model_id", req.model_id)
    if req.family == "asr" and req.model_id is not None:
        if req.backend_id not in {"faster-whisper", "faster-whisper-isolated"}:
            raise HTTPException(400, "This ASR engine does not accept a CTranslate2 model")
        from api.routers.setup.models import KNOWN_MODELS, is_cached

        model = next((item for item in KNOWN_MODELS if item["repo_id"] == req.model_id), None)
        compatible = req.model_id.startswith("Systran/faster-") or req.model_id == (
            "deepdml/faster-whisper-large-v3-turbo-ct2"
        )
        if model is None or str(model.get("role", "")).lower() != "asr" or not compatible:
            raise HTTPException(400, "This model is not compatible with Faster-Whisper")
        if not is_cached(req.model_id):
            raise HTTPException(409, "Install this ASR model before selecting it")
        try:
            asr_backend.select_faster_whisper_model(req.model_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    prefs.set_(pref_key, req.backend_id)
    return {
        "family": req.family,
        "active": module.active_backend_id(),
        "env_override": bool(__import__("os").environ.get(f"OMNIVOICE_{req.family.upper()}_BACKEND")),
        # Echo the routing verdict so the UI can warn on a cpu_fallback pick.
        "routing_status": entry.get("routing_status", "cpu_only"),
        "effective_device": entry.get("effective_device", "cpu"),
        "routing_reason": entry.get("routing_reason"),
    }
