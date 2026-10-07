"""Settings API — HF token save/clear/state endpoints (Phase 1 AUTH-03 backend half).

These endpoints are the backend half of the Wave 2 Settings → API Keys
panel. Threat T-01-03 mitigation: the router-level `require_admin` dependency
keeps desktop callers loopback-only and requires the long API key for every
remote server-mode mutation. Read-only bare-Docker discovery remains available
until an API key is configured; once configured, reads require it too.

The state endpoint duplicates `/system/hf-token/state` (which lives on
`system.py` for legacy-router compatibility); both return the same shape.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
import subprocess
from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from core.logging_utils import log_safe
from core.nvidia_smi import find_nvidia_smi
from core.engine_licenses import LICENSE_GATED_ENGINES
from api.dependencies import require_admin, require_admin_action

logger = logging.getLogger("omnivoice.api.settings")

router = APIRouter(
    prefix="/api/settings",
    tags=["settings"],
    dependencies=[Depends(require_admin)],
)


class _HFTokenBody(BaseModel):
    token: str = Field(..., min_length=1, description="HuggingFace access token")


def _state_response(*, validate: bool = False) -> dict:
    """Return the same shape the React panel renders. Never includes raw token."""
    from services import token_resolver

    s = token_resolver.state(validate=validate)
    return {
        "active": s["active"],
        "sources": [asdict(row) for row in s["sources"]],
    }


@router.post("/hf-token")
def save_hf_token(body: _HFTokenBody):
    """Persist a new HF token to the encrypted settings store + the HF
    canonical file (via huggingface_hub.login). Returns the updated
    cascade state."""
    token = body.token.strip()
    if not token:
        raise HTTPException(status_code=400, detail="token must be non-empty")
    from services import token_resolver
    try:
        token_resolver.save_app_token(token)
    except Exception:
        logger.exception("save_app_token failed")
        raise HTTPException(status_code=500, detail="Failed to save HF token")
    return _state_response()


@router.delete("/hf-token")
def clear_hf_token(also_clear_hf_cli: bool = Query(False)):
    """Clear the App token and optionally recognized local Hub token files."""
    from services import token_resolver
    try:
        token_resolver.clear_app_token(also_clear_hf_cli=also_clear_hf_cli)
    except Exception:
        logger.exception("clear_app_token failed")
        raise HTTPException(status_code=500, detail="Failed to clear HF token")
    return _state_response()


@router.get("/hf-token/state")
def get_hf_token_state(fresh: bool = Query(False)):
    """3-source HF token cascade state for the Settings UI.

    ``fresh=1`` drops the resolver's whoami validation cache first so the
    response re-runs whoami for every source — this is what the panel's
    "Test now" button sends. Plain GETs only inspect local token presence.
    """
    from services import token_resolver
    if fresh:
        token_resolver.invalidate_cache()
    return _state_response(validate=fresh)


# ── Performance settings (INST-12) ────────────────────────────────────────
# Threat T-02-04: same admin guard as the hf-token endpoints via the
# router-level `require_admin` dep.


_TORCH_COMPILE_KEY = "perf.torch_compile_disabled"

from services.performance_profiles import (
    _PERFORMANCE_PROFILE_KEY, _PERFORMANCE_TIERS, _PERFORMANCE_FAMILIES,
    activate_performance_tier,
    profile_state as _performance_profile_state,
)


class _PerformanceProfileBody(BaseModel):
    tier: str = Field(..., description="fast | balanced | quality | max | auto")
    family: str | None = Field(None, description="Engine family, or null to set the global tier")




@router.get("/performance-profile")
def get_performance_profile():
    """Return the global speed/quality preference and per-engine overrides."""
    return _performance_profile_state()


@router.put("/performance-profile")
def set_performance_profile(body: _PerformanceProfileBody):
    """Persist a performance preference and apply installed compatible picks."""
    from core import prefs

    tier = body.tier.strip().lower()
    if tier not in (*_PERFORMANCE_TIERS, "auto"):
        raise HTTPException(status_code=400, detail="Unknown performance tier")
    family = body.family.strip().lower() if body.family else None
    if family is not None and family not in _PERFORMANCE_FAMILIES:
        raise HTTPException(status_code=400, detail="Unknown engine family")
    if family is not None and tier == "auto":
        raise HTTPException(status_code=400, detail="Auto manages the whole device; use the global control")
    state = _performance_profile_state()
    applicable = state["applicable_families"]
    # A global pack policy must be saved before its first models are installed.
    # Activation is installed-only; the installer reconciles the saved policy
    # as models become available. Family controls still need a usable engine.
    if family is not None and family not in applicable:
        raise HTTPException(status_code=409, detail="The selected engines do not support this performance preset")
    from core import job_store
    from api.routers.batch import list_batch_jobs
    if job_store.list_jobs(status="active", limit=1) or list_batch_jobs(status="active", limit=1):
        raise HTTPException(status_code=409, detail="Wait for queued or running jobs to finish before changing performance presets")
    try:
        if family is None:
            # One atomic write clears family overrides together with the global
            # choice, so a crash cannot leave half of a global change persisted.
            prefs.update_mapping(_PERFORMANCE_PROFILE_KEY, {"global": tier}, replace=True)
        else:
            stored = prefs.get(_PERFORMANCE_PROFILE_KEY, {})
            resolved = dict(stored.get("resolved", {})) if isinstance(stored, dict) else {}
            resolved.pop(family, None)
            prefs.update_mapping(_PERFORMANCE_PROFILE_KEY, {family: tier, "resolved": resolved})
    except Exception:
        logger.exception("set_performance_profile failed")
        raise HTTPException(status_code=500, detail="Failed to persist performance profile")
    activations = activate_performance_tier(tier, family)
    result = _performance_profile_state()
    if activations:
        result["runtime_activations"] = activations
    if tier == "max":
        result["capacity_activations"] = activations
    return result


class _TorchCompileBody(BaseModel):
    enabled: bool = Field(..., description="True to disable torch.compile (eager mode) for the engine")


def _torch_compile_state() -> dict:
    import sys
    from services import settings_store

    raw = settings_store.get_text(_TORCH_COMPILE_KEY, "0")
    return {"enabled": raw == "1", "platform": sys.platform}


@router.get("/perf/torch-compile-disabled")
def get_torch_compile_disabled():
    """Return the current torch.compile-disabled toggle + the runtime platform.

    `platform` is still reported (clients may show it), but since #2135 the
    toggle is live on every host: it used to be rendered disabled off Windows
    on the assumption that only #65's Windows OOM needed it, which left the
    Linux/CUDA reporter of #2135 with no way to switch off the compile that
    was killing their backend.
    """
    return _torch_compile_state()


@router.put("/perf/torch-compile-disabled")
def set_torch_compile_disabled(body: _TorchCompileBody):
    """Persist the toggle. Honoured by `services.engine_env.build_engine_env()`
    (subprocess engines) and `services.engine_env.should_torch_compile()`
    (in-process), on every platform since #2135."""
    from services import settings_store

    try:
        settings_store.set_text(_TORCH_COMPILE_KEY, "1" if body.enabled else "0")
    except Exception:
        logger.exception("set_torch_compile_disabled failed")
        raise HTTPException(status_code=500, detail="Failed to persist setting")
    return _torch_compile_state()


# ── Offload the TTS model to RAM after generation (#2618) ─────────────────


class _OffloadAfterGenerationBody(BaseModel):
    enabled: bool = Field(..., description="True to move the TTS model to system RAM after each generation")


def _offload_after_generation_state() -> dict:
    """`enabled` is the effective value (env > saved > off). `env_pinned` means
    OMNIVOICE_OFFLOAD_AFTER_GENERATION decides and a saved value is ignored.
    `device` is the TTS device: on `cpu` the setting has nothing to move."""
    from services import model_manager as mm

    try:
        device = str(mm.get_best_device()).split(":", 1)[0]
    except Exception:  # noqa: BLE001 — a device probe must not break Settings
        device = "cpu"
    return {
        "enabled": mm.offload_after_generation_enabled(),
        "env_pinned": bool(os.environ.get(mm.OFFLOAD_AFTER_GENERATION_ENV)),
        "device": device,
    }


@router.get("/perf/offload-after-generation")
def get_offload_after_generation():
    """Whether the in-process TTS model moves to system RAM after generation."""
    return _offload_after_generation_state()


@router.put("/perf/offload-after-generation")
def set_offload_after_generation(body: _OffloadAfterGenerationBody):
    """Persist the toggle. Applies from the next generation, no restart."""
    from core import prefs
    from services import model_manager as mm

    try:
        prefs.set_(mm.OFFLOAD_AFTER_GENERATION_PREF, bool(body.enabled))
    except Exception:
        logger.exception("set_offload_after_generation failed")
        raise HTTPException(status_code=500, detail="Failed to persist setting")
    return _offload_after_generation_state()


# ── Compute-device override (Settings → Performance) ──────────────────────


class _ComputeDeviceBody(BaseModel):
    value: str = Field(..., description="auto | cuda | rocm | xpu | mps | cpu")


def _compute_device_state() -> dict:
    """Everything the Performance panel needs to render the device control:
    the resolved pick (env > prefs > auto), what this process actually applied
    at probe time (differs after a change until restart — caps are immutable
    per process), what auto would pick, and which families exist here."""
    from core import device_caps

    caps = device_caps.detect_host_caps()
    env_pin = (os.environ.get("OMNIVOICE_DEVICE") or "").strip().lower()
    auto_family = next(
        (f for f in device_caps.ACCELERATOR_PRIORITY if f in caps.available_families),
        "cpu",
    )
    value = device_caps.requested_device_override()
    return {
        "value": value,
        "applied": caps.requested_family,
        "restart_required": value != caps.requested_family,
        # The running process asked for a family it doesn't have (env pin on
        # the wrong machine, hardware removed): auto is in effect, and a
        # restart would not change that — the panel says so instead of
        # pretending the pick took.
        "override_ignored": (
            caps.requested_family not in ("auto", caps.family)
        ),
        "effective_family": caps.family,
        "auto_family": auto_family,
        "available_families": list(caps.available_families),
        "env_pinned": env_pin in device_caps.DEVICE_OVERRIDE_CHOICES and env_pin != "",
        "choices": list(device_caps.DEVICE_OVERRIDE_CHOICES),
    }


@router.get("/compute-device")
def get_compute_device():
    """Current compute-device override state (Settings → Performance)."""
    return _compute_device_state()


@router.put("/compute-device")
def set_compute_device(body: _ComputeDeviceBody):
    """Persist the compute-device pick. Applied by the capability probe at
    the next backend start (host caps are immutable per process — same
    restart contract as the rest of the Performance tab). ``OMNIVOICE_DEVICE``
    always wins over this pick; the UI shows the pin instead of pretending."""
    from core import device_caps, prefs

    value = (body.value or "").strip().lower()
    if value not in device_caps.DEVICE_OVERRIDE_CHOICES:
        raise HTTPException(
            status_code=400,
            detail=f"Unknown device '{value}'. Valid: {', '.join(device_caps.DEVICE_OVERRIDE_CHOICES)}",
        )
    caps = device_caps.detect_host_caps()
    if value not in ("auto", "cpu") and value not in caps.available_families:
        raise HTTPException(
            status_code=400,
            detail=(
                f"'{value}' is not available on this host "
                f"(have: {', '.join(caps.available_families)})"
            ),
        )
    try:
        prefs.set_("compute_device", value)
    except Exception:
        logger.exception("set_compute_device failed")
        raise HTTPException(status_code=500, detail="Failed to persist setting")
    return _compute_device_state()


@router.get("/gpu-report")
def get_gpu_report():
    """Which engines will use the GPU on this host, and why not otherwise.

    Codes + params only (the renderer owns the prose, via i18n): the physical
    GPUs, the installed PyTorch build, a host state such as ``amd_cuda_build``,
    the honest options for that state, and a verdict per TTS/ASR engine.
    """
    from core.gpu_report import collect_gpu_report

    return collect_gpu_report()


# ── CUDA adapter selection (multi-GPU hosts) ─────────────────────────────


_CUDA_VISIBLE_DEVICES = "CUDA_VISIBLE_DEVICES"
_CUDA_UUID = re.compile(r"^GPU-[A-Za-z0-9-]+$")


class _CudaDeviceBody(BaseModel):
    value: str = Field(..., description="auto or an NVIDIA GPU UUID")


def _cuda_devices() -> list[dict]:
    """Enumerate physical NVIDIA adapters without importing torch.

    ``torch.cuda`` only exposes adapters allowed by CUDA_VISIBLE_DEVICES, so it
    cannot offer a way back to a currently hidden card. nvidia-smi sees the
    physical inventory and gives us stable UUIDs, which CUDA accepts directly.
    """
    executable = find_nvidia_smi()
    if not executable:
        return []
    try:
        result = subprocess.run(
            [
                executable,
                "--query-gpu=index,uuid,name",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        logger.debug("CUDA adapter enumeration failed", exc_info=True)
        return []
    if result.returncode != 0:
        return []
    devices = []
    for line in result.stdout.splitlines():
        parts = [part.strip() for part in line.split(",", 2)]
        if len(parts) != 3 or not parts[0].isdigit() or not _CUDA_UUID.fullmatch(parts[1]):
            continue
        devices.append({"index": int(parts[0]), "value": parts[1], "name": parts[2]})
    return devices


def _cuda_device_state() -> dict:
    from core import prefs

    external = prefs.is_env_shadowed(_CUDA_VISIBLE_DEVICES)
    saved = str(prefs.get(f"env.{_CUDA_VISIBLE_DEVICES}", "") or "").strip()
    applied = (
        os.environ[_CUDA_VISIBLE_DEVICES].strip() or "disabled"
        if _CUDA_VISIBLE_DEVICES in os.environ else "auto"
    )
    value = applied if external else saved
    return {
        "value": value or "auto",
        "applied": applied,
        "restart_required": (value or "auto") != applied,
        "env_pinned": external,
        "devices": _cuda_devices(),
    }


@router.get("/cuda-device")
def get_cuda_device():
    """Physical CUDA adapter selected for the next backend launch."""
    return _cuda_device_state()


@router.put("/cuda-device")
def set_cuda_device(body: _CudaDeviceBody):
    """Persist CUDA_VISIBLE_DEVICES before torch is imported on next launch."""
    from core import prefs

    value = (body.value or "").strip()
    if value != "auto" and value not in {device["value"] for device in _cuda_devices()}:
        raise HTTPException(status_code=400, detail="Unknown CUDA adapter")
    try:
        if value == "auto":
            prefs.delete(f"env.{_CUDA_VISIBLE_DEVICES}")
        else:
            prefs.set_(f"env.{_CUDA_VISIBLE_DEVICES}", value)
    except Exception:
        logger.exception("set_cuda_device failed")
        raise HTTPException(status_code=500, detail="Failed to persist CUDA adapter")
    return _cuda_device_state()


# ── Generation-history retention (Studio takes rail) ──────────────────────


class _HistoryRetentionBody(BaseModel):
    cap: int = Field(
        ...,
        ge=0,
        le=100000,
        description="Max takes kept before the oldest UNstarred ones (rows + WAVs) are pruned; 0 = unlimited",
    )


def _history_retention_state() -> dict:
    from api.routers.generation import DEFAULT_HISTORY_CAP, _history_cap

    return {"cap": _history_cap(), "default": DEFAULT_HISTORY_CAP}


@router.get("/history-retention")
def get_history_retention():
    """Current generation-history retention cap (Settings → Storage)."""
    return _history_retention_state()


@router.put("/history-retention")
def set_history_retention(body: _HistoryRetentionBody):
    """Persist the retention cap. Enforced after every generation: the oldest
    unstarred takes over the cap are pruned (rows + their audio files);
    starred takes are never pruned. 0 disables pruning entirely."""
    from core import prefs
    from api.routers.generation import HISTORY_CAP_PREF_KEY

    try:
        prefs.set_(HISTORY_CAP_PREF_KEY, int(body.cap))
    except Exception:
        logger.exception("set_history_retention failed")
        raise HTTPException(status_code=500, detail="Failed to persist setting")
    return _history_retention_state()


# ── Dictation refinement (parity program Wave 2.1 / Spec 3 phase 2) ───────


class _RefinementBody(BaseModel):
    auto: bool | None = None
    smart_cleanup: bool | None = None
    self_correction: bool | None = None
    preserve_technical: bool | None = None


def _refinement_state():
    from services.refinement import (
        _skill_llm,
        get_last_refine_status,
        get_refinement_config,
    )

    cfg = get_refinement_config()
    # `llm_ready` only means "an endpoint is CONFIGURED" — a placeholder/dead
    # endpoint still reads ready. It's resolved through the LLM Skills registry
    # so a disabled dictation_refinement skill / per-skill provider override
    # reads the same here as on the actual refine path. The honesty layer is
    # `last_refine_status`: {ok, reason, at} from the most recent final, so the
    # panel can flag a configured-but-failing LLM (the real safety is the hard
    # refine timeout, which keeps a dead endpoint from ever stalling the final).
    cfg["llm_ready"] = _skill_llm().id != "off"
    cfg["last_refine_status"] = get_last_refine_status()
    return cfg


@router.get("/dictation-refinement")
def get_dictation_refinement():
    """Current refinement config + whether an LLM backend is configured."""
    return _refinement_state()


@router.put("/dictation-refinement")
def set_dictation_refinement(body: _RefinementBody):
    from services.refinement import set_refinement_config

    try:
        set_refinement_config({k: v for k, v in body.model_dump().items() if v is not None})
    except Exception:
        logger.exception("set_dictation_refinement failed")
        raise HTTPException(status_code=500, detail="Failed to persist setting")
    return _refinement_state()


# ── LLM endpoint (parity program Wave 2.4 / §R2 rung 4) ───────────────────
# Focused configuration for the OpenAI-compatible LLM endpoint that powers
# cinematic translate, glossary auto-extract, and dictation refinement.
# Persistence rides the existing TRANSLATE_BASE_URL / TRANSLATE_API_KEY /
# TRANSLATE_MODEL env vars (already in system.py PERSISTENT_KEYS, restored
# at startup) so the resolution path in llm_backend/translator is unchanged.


class _LLMEndpointBody(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = None  # None = leave unchanged; "" = clear


def _mask(secret: str | None) -> str | None:
    if not secret:
        return None
    return f"…{secret[-4:]}" if len(secret) > 4 else "set"


def _llm_endpoint_state():
    from services.llm_backend import OpenAICompatBackend

    ok, reason = OpenAICompatBackend.is_available()
    return {
        "base_url": os.environ.get("TRANSLATE_BASE_URL", ""),
        "model": os.environ.get("TRANSLATE_MODEL", ""),
        "api_key_masked": _mask(
            os.environ.get("TRANSLATE_API_KEY") or os.environ.get("OPENAI_API_KEY")
        ),
        "available": ok,
        "reason": None if ok else reason,
    }


@router.get("/llm-endpoint")
def get_llm_endpoint():
    """Current OpenAI-compatible LLM endpoint config + live availability."""
    return _llm_endpoint_state()


@router.put("/llm-endpoint")
def set_llm_endpoint(body: _LLMEndpointBody):
    """Persist base URL / model / API key for the OpenAI-compatible endpoint.

    Reuses the env-var persistence path (prefs.json, restored at startup):
    base_url -> TRANSLATE_BASE_URL, model -> TRANSLATE_MODEL,
    api_key -> encrypted TRANSLATE_API_KEY storage. A None field is left unchanged; an empty
    string clears it. Ollama ignores the key; vLLM / LM Studio require it.
    """
    from services import settings_store
    from core.prefs import set_ as prefs_set, delete as prefs_delete

    mapping = {
        "TRANSLATE_BASE_URL": body.base_url,
        "TRANSLATE_MODEL": body.model,
        "TRANSLATE_API_KEY": body.api_key,
    }
    for env_key, val in mapping.items():
        if val is None:
            continue  # untouched
        val = val.strip()
        if val:
            os.environ[env_key] = val
            if env_key != "TRANSLATE_API_KEY":
                prefs_set(f"env.{env_key}", val)
        else:
            os.environ.pop(env_key, None)
            prefs_delete(f"env.{env_key}")
        if env_key == "TRANSLATE_API_KEY":
            settings_store.set_secret("translation_env.TRANSLATE_API_KEY", val or None)
            prefs_delete(f"env.{env_key}")
    # get_active_llm_backend() builds a fresh backend (and its OpenAI client
    # reads env at construction) on every call, so there's no singleton to
    # invalidate — the next translate/refine picks up the new values.
    return _llm_endpoint_state()


# ── Multi-provider LLM registry (Settings → LLM Providers) ────────────────
# Keys persist ENCRYPTED via settings_store.set_secret (never .env, never
# returned). base_url/model/account overrides are non-secret. Loopback-gated
# by the router dep, so LAN peers can't read masks or write keys.

class _LLMProviderBody(BaseModel):
    api_key: str | None = Field(None, description="API key; '' clears it, None leaves unchanged")
    base_url: str | None = None
    model: str | None = None
    account_id: str | None = Field(None, description="Cloudflare account id")
    make_active: bool = False
    activate_if_unset: bool = True  # Legacy clients; editors opt out for save/test.


class _LLMActiveBody(BaseModel):
    provider: str = Field(..., description="provider id to activate")


@router.get("/llm-providers")
def list_llm_providers():
    """All providers with resolved base_url/model + whether a key is configured.

    Never returns key material — only `has_key`/`key_from_env` booleans.
    """
    from services import llm_providers, llm_backend
    return {
        "active": llm_providers.active_provider_id(),
        "engine_active": llm_backend.active_backend_id(),
        "engine_from_env": bool(os.environ.get("OMNIVOICE_LLM_BACKEND")),
        "providers": [llm_providers.describe(p) for p in llm_providers.all_providers()],
    }


@router.put("/llm-providers/{provider_id}")
def save_llm_provider(provider_id: str, body: _LLMProviderBody):
    """Save a provider's key (encrypted) + optional base_url/model/account.

    A None field is left unchanged; an empty api_key clears the stored key.
    """
    from services import llm_providers
    p = llm_providers.get_provider(provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider_id!r}")
    if not body.make_active and not body.activate_if_unset:
        from core import prefs
        from services import llm_backend
        # Persist the pre-save mode before adding a first cloud key. Otherwise
        # legacy key auto-detection would enable LLMs before Connect verifies it.
        if prefs.get("llm_backend") is None and not os.environ.get("OMNIVOICE_LLM_BACKEND"):
            prefs.set_("llm_backend", llm_backend.active_backend_id())
    if body.api_key is not None:
        llm_providers.save_key(provider_id, body.api_key.strip())
    llm_providers.save_overrides(
        provider_id, base_url=body.base_url, model=body.model,
        account_id=body.account_id,
    )
    # An explicit save also claims the active slot when the user has never
    # chosen a provider (#963). Without this, a saved-and-tested local
    # provider (Ollama/LM Studio) evaporates on restart: active_provider_id()
    # deliberately excludes local providers from auto-select, so the plain
    # "Save" left nothing persisted to resolve. Gated on the STORED selection
    # only — an explicit prior choice is never stolen by a plain save, and an
    # unconfigured provider can't claim the slot.
    if body.make_active:
        _activate_llm_provider(provider_id)
    elif (
        body.activate_if_unset
        and llm_providers.stored_active_provider_id() is None
        and llm_providers.is_configured(p)
    ):
        llm_providers.set_active_provider(provider_id)
    return list_llm_providers()


@router.post("/llm-providers/active")
def set_active_llm_provider(body: _LLMActiveBody):
    from services import llm_providers
    if llm_providers.get_provider(body.provider) is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {body.provider!r}")
    _activate_llm_provider(body.provider)
    return list_llm_providers()


def _validate_llm_activation(provider_id: str) -> None:
    from services import llm_providers
    p = llm_providers.get_provider(provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail="Unknown LLM provider.")
    pin = llm_providers._active_env_pin()
    if (pin and pin != provider_id) or os.environ.get("OMNIVOICE_LLM_BACKEND") not in (None, "", "openai-compat"):
        raise HTTPException(status_code=409, detail="LLM selection is pinned by the environment.")
    error = llm_providers.configuration_error(p)
    if error:
        raise HTTPException(status_code=400, detail=error)


def _activate_llm_provider(provider_id: str) -> None:
    """Explicit activation enables both provider and engine; pins still win."""
    from core import prefs
    from services import llm_providers
    _validate_llm_activation(provider_id)
    llm_providers.set_active_provider(provider_id)
    prefs.set_("llm_backend", "openai-compat")


@router.post("/llm-providers/{provider_id}/connect")
def connect_llm_provider(provider_id: str):
    """Enable a provider only after it returns a usable completion."""
    from services import llm_providers
    p = llm_providers.get_provider(provider_id)
    _validate_llm_activation(provider_id)
    def configuration():
        return (llm_providers.resolve_base_url(p), llm_providers.configured_model(p),
                llm_providers.resolve_api_key(p), llm_providers.resolve_account_id(p))
    verified = configuration()
    result = test_llm_provider(provider_id)
    if result["ok"]:
        if configuration() != verified:
            return {"ok": False, "kind": "config"}
        _activate_llm_provider(provider_id)
    return result


def _scrub_llm_detail(e: Exception, api_key: str | None) -> str:
    """Scrubbed, UI-safe failure text. scrub_text() covers env secrets and
    home paths — but a STORE-persisted key isn't in the env, and some
    providers echo the key in error bodies, so redact the exact resolved key
    explicitly before the generic pass."""
    from core.scrub import scrub_text
    detail = f"{type(e).__name__}: {e}"
    if api_key and api_key != "local" and len(api_key) >= 8:
        detail = detail.replace(api_key, "•••")
    return scrub_text(detail)


def _classify_llm_error(e: Exception) -> str:
    """Map a provider-call failure to an actionable kind the UI can localize.

    Kinds: auth (bad/missing key), not_found (model or endpoint path),
    rate_limit, network (DNS/conn/timeout), error (everything else).
    Status codes win when the OpenAI SDK provides one; exception-family
    names catch the non-HTTP failures (DNS, refused, TLS, timeout).
    """
    from urllib.error import HTTPError
    status = e.code if isinstance(e, HTTPError) else getattr(e, "status_code", None)
    if status in (401, 403):
        return "auth"
    if status == 404:
        return "not_found"
    if status == 429:
        return "rate_limit"
    name = type(e).__name__
    if name in ("APIConnectionError", "APITimeoutError", "ConnectError", "URLError",
                "ConnectTimeout", "TimeoutError"):
        return "network"
    if name == "AuthenticationError":
        return "auth"
    if name == "NotFoundError":
        return "not_found"
    if name == "RateLimitError":
        return "rate_limit"
    return "error"


@router.post("/llm-providers/{provider_id}/test")
def test_llm_provider(provider_id: str):
    """One cheap round-trip against a provider to prove the key/URL work.

    Temporarily activates the provider for the probe by resolving its config
    directly (does not change the persisted active selection). Returns
    latency_ms plus, on failure, a classified ``kind`` (config / auth /
    not_found / rate_limit / network / error) so the UI shows an actionable,
    localizable message instead of a raw exception string.
    """
    import time as _time

    from services import llm_providers
    p = llm_providers.get_provider(provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider_id!r}")
    base_url = llm_providers.resolve_base_url(p)
    api_key = llm_providers.resolve_api_key(p)
    error = llm_providers.configuration_error(p)
    if error:
        return {"ok": False, "kind": "config", "detail": error}
    t0 = _time.monotonic()
    try:
        from httpx import Timeout
        from services.llm_transport import create_client
        # max_retries=0: this is an interactive probe with a live spinner — the
        # SDK's default 2 automatic retries turn a 429/timeout into a ~34s hang.
        # Surface the first failure immediately instead.
        client = create_client(p)
        model = llm_providers.resolve_model(p)
        res = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with the single word: ok"}],
            # Local runtimes may spend over 20s loading weights on first use.
            # Keep connection failures fast while allowing that cold start.
            timeout=Timeout(120 if p.local or p.transport == "cli" else 20, connect=5),
        )
        from services.llm_backend import _strip_reasoning
        reply = _strip_reasoning(res.choices[0].message.content or "")
        if not reply:
            raise ValueError("Provider returned no usable answer.")
        return {
            "ok": True,
            "model": model,
            "reply": reply[:80],
            "latency_ms": int((_time.monotonic() - t0) * 1000),
        }
    except Exception as e:  # noqa: BLE001 — classify without exposing diagnostics
        kind = _classify_llm_error(e)
        from core.public_errors import provider_failure
        failure = provider_failure(kind)
        # A successful local catalog probe proves the cached model is stale.
        # Invalidate it, but never include catalog or exception text in the
        # response: both are controlled by the provider.
        if kind == "not_found" and p.local:
            available = _local_models(base_url, api_key)
            if available is not None:
                llm_providers.forget_discovered_models(p.id)
        return {
            "ok": False,
            **failure,
            "latency_ms": int((_time.monotonic() - t0) * 1000),
        }


def _local_models(base_url: str, api_key: str):
    """Model ids a local OpenAI-compatible server currently serves.

    ``None`` when the listing itself failed, ``[]`` when it succeeded and the
    server has nothing loaded. The distinction is load-bearing: collapsing both
    to ``[]`` let the caller state "reports no loaded models" on a lookup that
    never happened, which is a confident wrong diagnosis in place of a vague
    right one (CodeRabbit). Only used to sharpen an error message, so it must
    never raise a second error on top of the first.
    """
    try:
        from openai import OpenAI
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        return sorted(m.id for m in client.models.list(timeout=5))
    except Exception:  # noqa: BLE001
        return None


@router.get(
    "/llm-providers/{provider_id}/models",
    dependencies=[Depends(require_admin_action)],
)
def list_llm_provider_models(provider_id: str):
    """List model ids the provider's key can access (OpenAI-compat /models).

    Powers the model-picker datalist in Settings → LLM Providers so users
    don't have to guess model names. Read-only; failures return the same
    classified shape as /test; capped so a huge catalog can't bloat the UI.
    """
    from services import llm_providers
    p = llm_providers.get_provider(provider_id)
    if p is None:
        raise HTTPException(status_code=404, detail=f"unknown provider {provider_id!r}")
    if p.transport != "openai":
        return {"ok": True, "models": [], "truncated": False}
    base_url = llm_providers.resolve_base_url(p)
    api_key = llm_providers.resolve_api_key(p)
    if llm_providers.configuration_error(p, require_model=False):
        return {"ok": False, "kind": "config", "models": []}
    try:
        from openai import OpenAI
        # max_retries=0: interactive probe — fail fast, don't burn ~34s on the
        # SDK's default retry ladder when the key/URL is wrong (matches /test).
        client = OpenAI(api_key=api_key, base_url=base_url, max_retries=0)
        ids = sorted(m.id for m in client.models.list(timeout=10))
        # Cap so a huge catalog can't bloat the datalist; flag the cap so the UI
        # can say "first 200 shown" rather than implying it's the full list.
        return {"ok": True, "models": ids[:200], "truncated": len(ids) > 200}
    except Exception as e:  # noqa: BLE001
        from core.public_errors import provider_failure
        return {
            "ok": False,
            **provider_failure(_classify_llm_error(e)),
            "models": [],
        }


# ── LLM Skills (Settings → LLM Skills) ─────────────────────────────────────
# Per-feature enable/route control for every LLM consumption point. Each
# skill can be toggled off (degrades exactly like "no LLM configured") or
# routed to a specific provider (local Ollama/LM Studio vs a remote key)
# instead of the one global active provider. Loopback-gated (router dep).


class _LLMSkillBody(BaseModel):
    enabled: bool | None = Field(None, description="None leaves the toggle unchanged")
    provider_override: str | None = Field(
        None,
        description="provider id to route this skill to; '' or null clears "
                    "it (skill follows the active provider). Omit to leave "
                    "unchanged.",
    )


@router.get("/llm-skills")
def list_llm_skills():
    """Every LLM skill with its toggle, routing, and resolved ready status."""
    from services import llm_skills
    return {"skills": [llm_skills.describe(s.id) for s in llm_skills.all_skills()]}


@router.put("/llm-skills/{skill_id}")
def set_llm_skill(skill_id: str, body: _LLMSkillBody):
    """Toggle a skill and/or set its provider routing.

    Field semantics match the providers PUT: an omitted field is left
    unchanged; ``provider_override: ""``/``null`` clears the override.
    404 for an unknown skill or an unknown provider id.
    """
    from services import llm_skills
    if llm_skills.get_skill(skill_id) is None:
        raise HTTPException(status_code=404, detail=f"unknown LLM skill {skill_id!r}")
    kwargs = {}
    if body.enabled is not None:
        kwargs["enabled"] = body.enabled
    if "provider_override" in body.model_fields_set:
        kwargs["provider_override"] = body.provider_override
    try:
        if kwargs:
            llm_skills.configure_skill(skill_id, **kwargs)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    return list_llm_skills()


# ── License acceptance (Phase 3 Plan 03-01 / TTS-05) ──────────────────────
# Frontend ``SupertonicLicenseDialog`` flips the engine-license bit via this
# endpoint. The handler is loopback-gated (router-level dep) and the
# engine_id is allow-listed so an arbitrary string cannot be persisted.
# Threat T-03-04 in the plan frontmatter: this is an honest-acknowledgment
# gate, not a security boundary; the loopback + allow-list keeps the
# attack surface tight regardless.


#: Engines that have an in-tree acceptance dialog. Adding a new engine
#: here means adding a corresponding frontend dialog + a license URLs
#: dict in its constants module. Until that, the API refuses the write.
_LICENSE_ALLOWED_ENGINES = LICENSE_GATED_ENGINES


class _LicenseAcceptBody(BaseModel):
    engine_id: str = Field(..., min_length=1, max_length=64)
    accepted: bool = Field(..., description="True to accept the license terms")


@router.post("/license")
def post_license_acceptance(body: _LicenseAcceptBody) -> dict:
    """Persist a per-engine license-acceptance boolean.

    Returns ``{"ok": True, "engine_id": ..., "accepted": ...}`` so the
    caller can update its UI without a second round-trip. Validation:
    ``engine_id`` must be in the in-tree allow-list ‑‑ refuses arbitrary
    keys so the settings table can't be polluted via this route.
    """
    eid = body.engine_id.strip().lower()
    if eid not in _LICENSE_ALLOWED_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"engine_id {eid!r} is not in the license allow-list "
                f"{sorted(_LICENSE_ALLOWED_ENGINES)}"
            ),
        )
    from services import settings_store
    try:
        settings_store.set_license_accepted(eid, body.accepted)
    except Exception as exc:
        logger.error("set_license_accepted failed for %s: %s", log_safe(eid), log_safe(exc))
        raise HTTPException(status_code=500, detail="Failed to persist license acceptance")
    return {"ok": True, "engine_id": eid, "accepted": bool(body.accepted)}


@router.get("/license/{engine_id}")
def get_license_acceptance(engine_id: str) -> dict:
    """Return ``{"engine_id": ..., "accepted": bool}``.

    Same allow-list as the POST handler so an unknown engine id is a
    400 rather than a silent ``accepted=false`` for a non-existent
    engine.
    """
    eid = engine_id.strip().lower()
    if eid not in _LICENSE_ALLOWED_ENGINES:
        raise HTTPException(
            status_code=400,
            detail=(
                f"engine_id {eid!r} is not in the license allow-list "
                f"{sorted(_LICENSE_ALLOWED_ENGINES)}"
            ),
        )
    from services import settings_store
    try:
        accepted = settings_store.get_license_accepted(eid)
    except Exception as exc:
        logger.error("get_license_accepted failed for %s: %s", log_safe(eid), log_safe(exc))
        raise HTTPException(status_code=500, detail="Failed to read license acceptance")
    return {"engine_id": eid, "accepted": bool(accepted)}


# ── Storage: configurable models directory (#64) ──────────────────────────
# Where HuggingFace / Torch download model weights. The user's choice is
# persisted durably to the per-user env file as OMNIVOICE_CACHE_DIR, which
# main.py maps to HF_HOME / HF_HUB_CACHE / TORCH_HOME at startup. That env file
# is the *single source of truth*: PUT writes it, GET reads it back — there is
# no second store to diverge from. Takes effect on the next backend restart
# (a storage-location change can't safely move an in-use cache mid-process).
_MODELS_DIR_ENV = "OMNIVOICE_CACHE_DIR"


def _default_models_dir() -> str:
    """huggingface_hub's default cache root, honoring XDG_CACHE_HOME on Linux
    (matches HF so GET reports the *true* default the backend would use)."""
    base = os.environ.get("XDG_CACHE_HOME") or os.path.expanduser("~/.cache")
    return os.path.join(base, "huggingface")


def _effective_models_dir() -> str:
    return (
        os.environ.get("HF_HUB_CACHE")
        or os.environ.get("HUGGINGFACE_HUB_CACHE")
        or os.environ.get("HF_HOME")
        or _default_models_dir()
    )


class _ModelsDirBody(BaseModel):
    authorization: str = Field(description="One-shot native desktop authorization")


@router.get("/storage/models-dir")
def get_models_dir():
    """Current models directory: the persisted choice (from the durable env
    file — the same value main.py reads at startup), what's effective in this
    process, and the platform default."""
    from core import user_env

    configured = user_env.get_user_env(_MODELS_DIR_ENV) or None
    return {
        "configured": configured,
        "effective": _effective_models_dir(),
        "default": _default_models_dir(),
        "restart_required": False,
    }


@router.put("/storage/models-dir")
def set_models_dir(body: _ModelsDirBody):
    """Set (or clear, with an empty path) the models download directory.

    Validates the directory is writable, then writes OMNIVOICE_CACHE_DIR to the
    durable per-user env file so main.py applies it on the next launch. The env
    file is the only persisted store, so GET can never diverge from what was
    saved. Returns restart_required=True.
    """
    from core import user_env
    from core.path_authorization import PathAuthorizationError, consume

    try:
        raw = consume(body.authorization, "models_dir").strip()
    except PathAuthorizationError as exc:
        raise HTTPException(status_code=403, detail=str(exc)) from exc
    if not raw:
        user_env.unset_user_env(_MODELS_DIR_ENV)
        return {"configured": None, "default": _default_models_dir(), "restart_required": True}

    # Tauri already validates this before issuing the capability. Keep the
    # backend checks as defense in depth against a corrupt capability file.
    if any(ord(ch) < 0x20 or ord(ch) == 0x7F for ch in raw):
        raise HTTPException(status_code=400, detail="Path contains invalid control characters")

    path = os.path.abspath(os.path.expanduser(raw))
    try:
        os.makedirs(path, exist_ok=True)
        probe = os.path.join(path, ".omnivoice_write_test")
        with open(probe, "w", encoding="utf-8") as f:
            f.write("ok")
    except OSError as e:
        raise HTTPException(status_code=400, detail=f"Directory is not writable: {e}") from e
    finally:
        # Best-effort cleanup; a failed remove (concurrent process, perm change)
        # must not leave the request hanging or mask the real error.
        try:
            os.remove(os.path.join(path, ".omnivoice_write_test"))
        except OSError:
            pass

    user_env.set_user_env(_MODELS_DIR_ENV, path)
    return {"configured": path, "effective": _effective_models_dir(), "restart_required": True}


# ── Storage report (Settings → Storage) ────────────────────────────────────
# Per-volume disk totals + du-style sizes for everything the app owns (HF
# model cache, app data subtotals, engine venvs, temp files) with server-side
# warnings. Heavy directory walks run in a worker thread with per-category
# deadlines and a 5-minute in-process cache (services.storage_report), so the
# endpoint stays cheap on repeat Settings visits. Loopback-gated via the
# router-level dep like every sibling.


@router.get("/storage")
async def get_storage_report(refresh: bool = Query(False)):
    """Disk + per-category storage usage for the Settings → Storage panel.

    `refresh=1` bypasses the 5-minute cache and rescans. `min_free_gb`
    reuses the setup wizard's constant so both surfaces warn at the same
    threshold.
    """
    from api.routers.setup.wizard import MIN_FREE_GB
    from core.config import DATA_DIR
    from services import storage_report

    try:
        return await asyncio.to_thread(
            storage_report.get_report,
            data_dir=DATA_DIR,
            hf_cache_dir=_effective_models_dir(),
            app_venv=storage_report.default_app_venv(),
            min_free_gb=MIN_FREE_GB,
            refresh=refresh,
        )
    except Exception:
        logger.exception("storage report failed")
        raise HTTPException(status_code=500, detail="Failed to compute storage report")


@router.post("/storage/temp/clear")
async def clear_temp_files():
    """Delete VoiceStudio-owned temp files (Settings → Storage → Temporary files).

    Removes only the ``omnivoice*`` entries in the OS temp dir — the exact
    population the storage report's "temp" category counts — and invalidates
    the cached report so the next scan reflects the reclaimed space. Partial
    failures (files held open by a running job) are returned per entry.
    """
    from services import storage_report

    try:
        result = await asyncio.to_thread(storage_report.clear_temp)
        storage_report.clear_cache()
        return result
    except Exception:
        logger.exception("clear temp files failed")
        raise HTTPException(status_code=500, detail="Failed to clear temporary files")


# ── HF mirror endpoint (parity program Wave 4.3 / §R4 c) ──────────────────
# Restricted-network users (e.g. behind the Great Firewall) need to point
# huggingface_hub at a mirror. HF reads HF_ENDPOINT at import time, so a
# change takes effect on the next backend start — persisted to the durable
# per-user env so it survives Tauri/Finder launches that don't inherit a
# shell. Loopback-gated via the router dep.

_HF_ENDPOINT_ENV = "HF_ENDPOINT"

# A few well-known mirrors, surfaced as quick-picks in the UI. hf-mirror.com
# is the community mirror most-used in China; the official endpoint clears it.
_HF_MIRROR_PRESETS = [
    {"label": "Hugging Face (official)", "url": ""},
    {"label": "hf-mirror.com (community, China)", "url": "https://hf-mirror.com"},
]


class _HFMirrorBody(BaseModel):
    url: str = Field("", description="HF_ENDPOINT URL; empty string clears it (official endpoint)")
    mode: str | None = Field(
        None,
        description=(
            "'auto' switches to automatic endpoint selection (clears any "
            "explicit endpoint); 'manual' (or omitted — back-compat with older "
            "clients) pins the given url as an explicit choice."
        ),
    )


def _hf_mirror_state() -> dict:
    """The full GET /hf-mirror payload. Auto info comes from the CACHED race
    decision only — reading settings never probes the network."""
    from core import user_env
    from services import endpoint_race

    configured = user_env.get_user_env(_HF_ENDPOINT_ENV) or ""
    try:
        mode = "manual" if configured else endpoint_race.mode()
        auto = endpoint_race.cached_decision() if mode == "auto" else None
        opt_out = endpoint_race.env_opt_out()
    except Exception:  # a broken prefs file must never 500 the settings page
        logger.exception("hf-mirror auto state unavailable")
        mode, auto, opt_out = "manual", None, False
    return {
        # The value that will apply after restart (persisted), and what's
        # live in this process (env may differ until then).
        "configured": configured,
        "effective": os.environ.get(_HF_ENDPOINT_ENV, ""),
        "presets": _HF_MIRROR_PRESETS,
        # Automatic endpoint selection (services.endpoint_race): "auto" only
        # when nothing explicit is configured anywhere. `auto` is the cached
        # race decision ({endpoint, reachable, latency_ms, checked_at,
        # results}) or null when never raced / in manual mode.
        "mode": mode,
        "auto": auto,
        "auto_opt_out": opt_out,
    }


@router.get("/hf-mirror")
def get_hf_mirror():
    return _hf_mirror_state()


@router.put("/hf-mirror")
def set_hf_mirror(body: _HFMirrorBody):
    from core import user_env
    from services import endpoint_race

    mode = (body.mode or "manual").strip().lower()
    if mode not in {"auto", "manual"}:
        raise HTTPException(status_code=400, detail="mode must be 'auto' or 'manual'")
    # Auto mode = no explicit endpoint anywhere; a persisted endpoint would
    # read as an explicit choice, so switching to Auto clears it (plus the
    # `hf_endpoint` pref fallback the download paths resolve).
    url = "" if mode == "auto" else (body.url or "").strip().rstrip("/")
    if url and not url.startswith(("http://", "https://")):
        raise HTTPException(status_code=400, detail="Mirror URL must start with http(s)://")
    # Compare against the currently-persisted value (normalised the same way) so
    # a no-op save doesn't nag the user to restart. Only a real change to the
    # persisted endpoint can require a restart.
    previous = (user_env.get_user_env(_HF_ENDPOINT_ENV) or "").strip().rstrip("/")
    changed = url != previous
    try:
        if url:
            user_env.set_user_env(_HF_ENDPOINT_ENV, url)
            os.environ[_HF_ENDPOINT_ENV] = url  # best-effort for new downloads this session
        else:
            user_env.unset_user_env(_HF_ENDPOINT_ENV)
            os.environ.pop(_HF_ENDPOINT_ENV, None)
        from core import prefs
        if not url:
            # No endpoint anywhere: clearing to official (manual) or switching
            # to auto must also drop the legacy `hf_endpoint` pref fallback —
            # otherwise it silently keeps resolving as an explicit mirror and
            # "switch to official" doesn't actually switch.
            prefs.delete("hf_endpoint")
        endpoint_race.set_mode_pref(mode)
    except Exception:
        logger.exception("set_hf_mirror failed")
        raise HTTPException(status_code=500, detail="Failed to persist mirror setting")
    # An endpoint change invalidates the failed-recently install cooldowns: the
    # user's next action is "retry that download on the new endpoint", and a
    # 429 would dead-end the wizard's switch-and-retry flow.
    try:
        from api.routers.setup.download import clear_install_cooldowns

        clear_install_cooldowns()
    except Exception:  # pragma: no cover — cooldown reset must never fail the save
        logger.warning("could not clear install cooldowns after mirror change", exc_info=True)
    if mode == "auto":
        # Freshly chosen Auto should show a real pick immediately — race now
        # unless a fresh cached decision already exists (probes are ≤3 s and
        # this is an explicit user action, not a hot path).
        try:
            endpoint_race.ensure_decision()
        except Exception:
            logger.exception("endpoint race after switching to auto failed")
    # Model Store downloads pick up the new mirror immediately — the download
    # path resolves the endpoint per-call and we updated os.environ above. Only
    # transformers-side model *loads* (which read HF_ENDPOINT at import time)
    # need a restart, so restart_required is True ONLY when the value actually
    # changed — a no-op re-save never asks for a restart.
    return {**_hf_mirror_state(), "restart_required": changed}


@router.post("/hf-mirror/test")
def test_hf_mirror():
    """Re-run the endpoint race now (the Auto panel's "Test again").

    Forces fresh probes and re-caches the decision. In manual mode this is a
    no-op (an explicit endpoint is never auto-switched) — the response simply
    reflects the current state."""
    from services import endpoint_race

    try:
        endpoint_race.ensure_decision(force=True)
    except Exception:
        logger.exception("hf-mirror endpoint test failed")
        raise HTTPException(status_code=500, detail="Endpoint test failed")
    return _hf_mirror_state()


# ── OpenAI-compatible remote ASR (#877) ─────────────────────────────────────
# A path to Qwen3-ASR/FunASR/SenseVoice — or OpenAI's own Whisper API — today,
# without waiting on transformers to ship a direct Qwen3-ASR integration.
# base_url/model are plain settings_store text rows; the key is encrypted via
# settings_store.set_secret — same convention as /llm-providers, never
# returned to the client, '' clears it, omitted/None leaves it unchanged.


class _ASROpenAICompatBody(BaseModel):
    base_url: str | None = None
    model: str | None = None
    api_key: str | None = Field(None, description="'' clears it, None leaves unchanged")


@router.get("/asr-openai-compat")
def get_asr_openai_compat():
    from services import asr_backend

    return {
        "base_url": asr_backend.resolve_openai_compat_asr_base_url(),
        "model": asr_backend.resolve_openai_compat_asr_model(),
        "has_key": asr_backend.openai_compat_asr_has_key(),
    }


@router.put("/asr-openai-compat")
def set_asr_openai_compat(body: _ASROpenAICompatBody):
    from services import asr_backend, settings_store

    if body.base_url is not None:
        try:
            url = asr_backend.normalize_openai_compat_asr_base_url(body.base_url)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        settings_store.set_text(asr_backend._ASR_OPENAI_COMPAT_BASE_URL_KEY, url)
    if body.model is not None:
        settings_store.set_text(
            asr_backend._ASR_OPENAI_COMPAT_MODEL_KEY, body.model.strip() or "whisper-1"
        )
    if body.api_key is not None:
        settings_store.set_secret(
            asr_backend._ASR_OPENAI_COMPAT_SECRET_NAME, body.api_key.strip()
        )
    return get_asr_openai_compat()


@router.post("/asr-openai-compat/test")
def test_asr_openai_compat():
    """Cheap connectivity probe for the "Test connection" button.

    GET {base_url}/models against the PERSISTED config — the panel saves
    first, then tests, same stale-config contract as
    /llm-providers/{id}/test. No audio leaves the machine, nothing is
    transcribed. Loopback-only via the router-level guard. Always 200 with a
    structured verdict ({ok, status, latency_ms, ...} — see
    services.asr_backend.probe_openai_compat_server) so the UI renders
    success/latency or the exact failure without a raw 500. The key is never
    logged or echoed back."""
    from services import asr_backend

    return asr_backend.probe_openai_compat_server()


# ── Updates panel: shipped changelog + pre-migration DB backup state ────────
# (feat/safe-updates). Both are read-only, local-first surfaces for
# Settings → Updates: the "What's new" viewer reads the CHANGELOG.md that
# ships with the app, and the backup line shows the newest pre-migration
# snapshot written by core.db_backup before `alembic upgrade head` runs.


@router.get("/changelog")
def get_changelog(limit_versions: int = Query(5, ge=1, le=50)):
    """Structured release notes from the shipped CHANGELOG.md (newest first).

    Bullets are raw markdown-lite (bold leads, `code`, (#NNN) refs) — the
    frontend renders them safely without HTML. `available: false` when this
    install has no changelog (never an error: the viewer just hides)."""
    from core import changelog

    path = changelog.changelog_path()
    if not path:
        return {"available": False, "releases": []}
    try:
        with open(path, encoding="utf-8") as fh:
            releases = changelog.parse_changelog(fh.read(), limit_versions)
    except Exception:
        logger.exception("changelog parse failed")
        return {"available": False, "releases": []}
    return {"available": bool(releases), "releases": releases}


@router.get("/db-backup")
def get_db_backup_state():
    """Newest pre-migration database backup (or none yet). Feeds the
    "your data is backed up before every update" line in Settings → Updates."""
    from core import db_backup
    from core.config import DB_PATH

    latest = db_backup.latest_backup(DB_PATH)
    return {
        "available": latest is not None,
        "latest": latest,
        "count": len(db_backup.list_backups(DB_PATH)),
        "keep": db_backup.KEEP_BACKUPS,
    }


# ── Opt-in product analytics (hardened; default OFF) ───────────────────────
# Local-first means silence is not consent: analytics runs only when the user
# explicitly turns it on AND the build ships a destination token. See
# core/analytics.py for the three rules (opt-in, no exception autocapture,
# allowlisted metadata only).

class _AnalyticsBody(BaseModel):
    enabled: bool = Field(..., description="User's explicit choice. Default is OFF.")


@router.get("/analytics")
def get_analytics():
    from core import analytics

    return {
        "enabled": analytics.enabled(),
        "opted_in": analytics.user_opted_in(),
        # True for source builds too since #1193 (in-repo default token; env/baked
        # overrides). False only for a destination-less build, where the UI can
        # say so instead of offering a toggle that does nothing.
        "available": analytics.token_configured(),
        # Whether the user has ever been explicitly asked (first-run consent step
        # or the one-time banner). The UI uses this to ask exactly once — it never
        # enables anything by itself.
        "prompted": analytics.user_prompted(),
    }


@router.put("/analytics")
def set_analytics(body: _AnalyticsBody):
    from core import analytics

    analytics.set_opted_in(body.enabled)
    return get_analytics()
