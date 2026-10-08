"""Shared local performance preferences and runtime defaults. No model downloads."""
from __future__ import annotations

import math
import os

from services.performance_budget import TIERS as _PERFORMANCE_TIERS

#: TTS engines whose sampling the performance tiers tune: native OmniVoice and
#: the same model in its registered crash-isolated sidecar. The one source for
#: the backend gates and the renderer (via ``tts_tiered_engines``).
TIERED_TTS_ENGINES = frozenset({"omnivoice", "omnivoice-subprocess"})


_PERFORMANCE_PROFILE_KEY = "performance_profile"
_PERFORMANCE_FAMILIES = (
    "tts",
    "asr",
    "dictation",
    "diarisation",
    "translation",
    "llm",
)

# Advertise only implemented runtime controls, never speculative model switches.
_PERFORMANCE_TARGETS = {
    "tts": {
        "fast": {"steps": 8, "postprocess": False},
        "balanced": {"steps": 16, "postprocess": True},
        "quality": {"steps": 32, "postprocess": True},
        "max": {"steps": 64, "postprocess": True, "model_policy": "tts-first-installed"},
    },
    "asr": {
        tier: {"beam_size": width, "best_of": width, "engine": "faster-whisper"}
        for tier, width in zip(_PERFORMANCE_TIERS, (1, 3, 5, 8))
    },
    "dictation": {
        "fast": {"decoding_method": "greedy_search", "max_active_paths": 1, "engine": "sherpa-onnx"},
        "balanced": {"decoding_method": "greedy_search", "max_active_paths": 4, "engine": "sherpa-onnx"},
        "quality": {"decoding_method": "modified_beam_search", "max_active_paths": 4, "engine": "sherpa-onnx"},
        "max": {"decoding_method": "modified_beam_search", "max_active_paths": 8, "engine": "sherpa-onnx"},
    },
    "diarisation": {
        "fast": {"engine": "audiocpp-sortformer"},
        "balanced": {"engine": "audiocpp-sortformer"},
        "quality": {"engine": "pyannote"},
        "max": {"engine": "pyannote"},
    },
    "translation": {
        "fast": {"num_beams": 1, "engine": "argos"},
        "balanced": {"num_beams": 3, "engine": "argos"},
        "quality": {"num_beams": 5, "engine": "nllb"},
        "max": {"num_beams": 8, "engine": "nllb"},
    },
}


_TIER_POSITION = {"fast": 0.0, "balanced": 0.5, "quality": 0.8, "max": 1.0}


def _tier_choice(items: list, tier: str, *, size) -> object | None:
    """Pick an installed model along the user's speed/quality continuum."""
    if not items:
        return None
    ordered = sorted(items, key=lambda item: (float(size(item) or 0), str(item)))
    position = _TIER_POSITION.get(tier, _TIER_POSITION["balanced"])
    index = math.floor(position * (len(ordered) - 1) + 0.5)
    return ordered[index]


def _installed_ct2_models() -> list[dict]:
    """Installed CTranslate2 Whisper models usable by the shared ASR runtime."""
    from api.routers.setup.models import (
        KNOWN_MODELS,
        _model_supported,
        cache_is_complete,
        is_cached,
    )

    return [
        model
        for model in KNOWN_MODELS
        if str(model.get("role", "")).lower() == "asr"
        and not model.get("dictation_id")
        and (
            str(model.get("repo_id", "")).startswith("Systran/faster-")
            or model.get("repo_id") == "deepdml/faster-whisper-large-v3-turbo-ct2"
        )
        and _model_supported(model)
        and is_cached(model["repo_id"])
        and cache_is_complete(model)
    ]


def _faster_whisper_backend() -> str | None:
    from services import asr_backend

    if asr_backend._probe_available(asr_backend.FasterWhisperBackend):
        return "faster-whisper"
    row = next(
        (
            item
            for item in asr_backend.list_backends()
            if item["id"] == "faster-whisper-isolated"
        ),
        None,
    )
    return (
        "faster-whisper-isolated"
        if row and row.get("available") and row.get("routing_status") != "unavailable"
        else None
    )


def _dictation_supports_locale(spec, language: str | None) -> bool:
    if not language or spec.id == "sherpa-whisper-tiny":
        return True
    if spec.id == "sherpa-parakeet-tdt-v3":
        from services.asr_backend import _PARAKEET_MLX_LANGS

        return language in _PARAKEET_MLX_LANGS
    if spec.id in {"sherpa-parakeet-tdt-v2", "sherpa-zipformer-en-20m"}:
        return language == "en"
    if spec.id == "sherpa-zipformer-zh-14m":
        return language == "zh"
    if spec.id in {
        "sherpa-zipformer-bilingual-zh-en",
        "sherpa-paraformer-bilingual-zh-en",
    }:
        return language in {"en", "zh"}
    return True


def _installed_dictation_models() -> list:
    from services import asr_backend, sherpa_dictation

    language = asr_backend._locale_language()
    installed = [
        spec
        for spec in sherpa_dictation.list_specs()
        if sherpa_dictation.is_installed(spec)
        and not sherpa_dictation.is_demoted(spec.id)
    ]
    compatible = [
        spec for spec in installed if _dictation_supports_locale(spec, language)
    ]
    # Never "upgrade" a model to one that cannot understand the user's language.
    return compatible


def _activate_asr_model(tier: str) -> dict | None:
    from core import prefs
    from services import asr_backend

    if os.environ.get("OMNIVOICE_ASR_BACKEND") or prefs.is_env_shadowed(
        "ASR_MODEL_FASTER"
    ):
        return None
    model = _tier_choice(
        _installed_ct2_models(), tier, size=lambda item: item.get("size_gb")
    )
    backend_id = _faster_whisper_backend()
    if model is None or backend_id is None:
        return None
    repo_id = str(model["repo_id"])
    if asr_backend.faster_whisper_model_id() != repo_id:
        asr_backend.select_faster_whisper_model(repo_id)
    if asr_backend.active_backend_id() != backend_id:
        prefs.set_("asr_backend", backend_id)
    return {"engine": backend_id, "model": repo_id}


def _activate_dictation_model(tier: str) -> dict | None:
    from core import prefs
    from services import asr_backend, sherpa_dictation

    if os.environ.get("OMNIVOICE_SHERPA_ASR_MODEL"):
        return None
    available, _ = sherpa_dictation.sherpa_available()
    if not available:
        return None
    model = _tier_choice(
        _installed_dictation_models(), tier, size=lambda item: item.size_gb
    )
    if model is None:
        return None
    if prefs.get("dictation.model_id") != model.id:
        prefs.set_("dictation.model_id", model.id)
        asr_backend._capture_backend = None
        asr_backend._capture_backend_key = None
    return {"engine": model.kind, "model": model.id}


def _activate_translation_model(tier: str) -> dict | None:
    from core import prefs
    from services import translation_engines

    current = str(prefs.get("translation_backend", "argos"))
    # Keep a usable explicitly chosen network provider. A stale provider whose
    # package/key disappeared must not strand Dubbing while an installed local
    # translator is ready.
    if current not in {"argos", "nllb"} and translation_engines.is_ready(current):
        return None
    target = str(_PERFORMANCE_TARGETS["translation"][tier]["engine"])
    if not translation_engines.is_ready(target):
        target = next(
            (
                candidate
                for candidate in ("argos", "nllb")
                if translation_engines.is_ready(candidate)
            ),
            "",
        )
    if not target:
        return None
    if current != target:
        prefs.set_("translation_backend", target)
    return {
        "engine": target,
        "model": "facebook/nllb-200-distilled-600M" if target == "nllb" else target,
    }


def _installed_selectable_families() -> set[str]:
    from services import sherpa_dictation, translation_engines

    families: set[str] = set()
    if _installed_ct2_models() and _faster_whisper_backend():
        families.add("asr")
    sherpa_available, _ = sherpa_dictation.sherpa_available()
    if sherpa_available and _installed_dictation_models():
        families.add("dictation")
    if translation_engines.is_ready("nllb"):
        families.add("translation")
    return families


def _activate_installed_models(tier: str, family: str | None) -> dict[str, dict]:
    requested = set(_PERFORMANCE_FAMILIES if family is None else (family,))
    activated: dict[str, dict] = {}
    selectors = {
        "asr": _activate_asr_model,
        "dictation": _activate_dictation_model,
        "translation": _activate_translation_model,
    }
    for name, select in selectors.items():
        if name in requested:
            result = select(tier)
            if result:
                activated[name] = result
    return activated



def profile_state(choice: str | None = None) -> dict:
    from core import prefs
    from services import asr_backend, diarization_runtime
    from services.sherpa_dictation import get_spec as dictation_spec
    from services.tts_backend import active_backend_id as active_tts

    selected_dictation = (
        dictation_spec(str(prefs.get("dictation.model_id", "")))
        if prefs.get("dictation.enabled", True)
        else None
    )
    diarisation_choices = diarization_runtime.installed_backends()
    tts_engine = active_tts()
    asr_engine = asr_backend.active_backend_id()
    translation_engine = str(prefs.get("translation_backend", "argos"))

    active_engines = {
        "tts": tts_engine,
        "asr": asr_engine,
        "translation": translation_engine,
        "dictation": selected_dictation.kind if selected_dictation else "inactive",
    }
    supported_engines = {
        "tts": TIERED_TTS_ENGINES,
        "asr": {"faster-whisper", "faster-whisper-isolated"},
        "translation": {"nllb"},
        "dictation": {"offline-transducer", "online-transducer"},
    }

    stored = prefs.get(_PERFORMANCE_PROFILE_KEY, {})
    raw = stored if isinstance(stored, dict) else {}
    global_tier = str(choice or raw.get("global", "balanced")).lower()
    if global_tier not in (*_PERFORMANCE_TIERS, "auto"):
        global_tier = "balanced"
    overrides = {
        str(family): str(tier)
        for family, tier in (raw.items() if isinstance(raw, dict) else [])
        if family in _PERFORMANCE_FAMILIES and tier in _PERFORMANCE_TIERS
    }
    applicable_families = [
        family
        for family, engines in supported_engines.items()
        if active_engines[family] in engines
    ]
    for family in _installed_selectable_families():
        if family not in applicable_families:
            applicable_families.append(family)
    if len(diarisation_choices) > 1:
        applicable_families.append("diarisation")
    selections = {
        "tts": {
            "engine": tts_engine,
            # OmniVoice has one checkpoint family today; its performance tiers
            # tune sampling rather than silently changing voice capabilities.
            "model": "k2-fsa/OmniVoice"
            if tts_engine in TIERED_TTS_ENGINES
            else tts_engine,
        },
        "asr": {
            "engine": asr_engine,
            "model": asr_backend.faster_whisper_model_id()
            if asr_engine in {"faster-whisper", "faster-whisper-isolated"}
            else asr_engine,
        },
        "dictation": {
            "engine": selected_dictation.kind if selected_dictation else "inactive",
            "model": selected_dictation.id if selected_dictation else None,
            "label": selected_dictation.label if selected_dictation else None,
        },
        "diarisation": {
            "engine": diarization_runtime.selected_backend()
            if diarisation_choices
            else "inactive",
            "model": (
                diarization_runtime.SORTFORMER_REPO
                if diarization_runtime.selected_backend() == diarization_runtime.SORTFORMER
                else "pyannote/speaker-diarization-3.1"
            )
            if diarisation_choices
            else None,
        },
        "translation": {
            "engine": translation_engine,
            "model": "facebook/nllb-200-distilled-600M"
            if translation_engine == "nllb"
            else translation_engine,
        },
        "llm": {"engine": "inactive", "model": None},
    }
    from services.performance_inventory import profile_plan
    plan = profile_plan(global_tier, overrides, selections)
    effective = {family: overrides.get(family, plan["families"][family]["tier"])
                 for family in _PERFORMANCE_FAMILIES}
    for family, entry in plan["families"].items():
        if family == "tts" and entry["selection"] and entry["reason"] != "kept" and family not in applicable_families:
            applicable_families.append(family)
    return {
        "global": global_tier,
        "overrides": overrides,
        "effective": effective,
        "tiers": list(_PERFORMANCE_TIERS),
        "global_choices": [*_PERFORMANCE_TIERS, "auto"],
        "families": list(_PERFORMANCE_FAMILIES),
        "implemented_families": list(_PERFORMANCE_TARGETS),
        "applicable_families": applicable_families,
        # The renderer mirrors the TTS preset only for these engines.
        "tts_tiered_engines": sorted(TIERED_TTS_ENGINES),
        "targets": {
            family: _PERFORMANCE_TARGETS[family][effective[family]]
            for family in _PERFORMANCE_TARGETS
        },
        "selections": selections,
        "plan": plan,
        "downloads_started": False,
    }



def requested_tier(family: str) -> str | None:
    """None preserves existing workflow defaults until a user picks a preset."""
    from core import prefs
    stored = prefs.get(_PERFORMANCE_PROFILE_KEY, {})
    if not isinstance(stored, dict):
        return None
    tier = stored.get(family, stored.get("global"))
    resolved = stored.get("resolved", {})
    if isinstance(resolved, dict) and resolved.get(family) in _PERFORMANCE_TIERS:
        return resolved[family]
    if tier == "auto":
        from services.performance_budget import hardware_snapshot, recommended_tier
        return recommended_tier(hardware_snapshot())
    return tier if tier in _PERFORMANCE_TIERS else None


def activate_maximum_capacity_models(family: str | None = None) -> dict:
    """Select the strongest already-installed compatible local models.

    This is intentionally download-free. Choosing Max is explicit permission to
    change model selections, but model installation remains its own reviewable
    action in the catalogue.
    """
    return _activate_installed_models("max", family)


def activate_performance_tier(tier: str, family: str | None = None) -> dict:
    """Apply installed-only model/runtime selections implied by a preset."""
    if family is None:
        return _activate_budgeted_profile(tier)
    requested = set(_PERFORMANCE_FAMILIES if family is None else (family,))
    activated = _activate_installed_models(tier, family)

    if "diarisation" in requested and not os.environ.get(
        "OMNIVOICE_DIARIZATION_BACKEND"
    ):
        from services import diarization_runtime

        installed = diarization_runtime.installed_backends()
        if len(installed) > 1:
            engine = _PERFORMANCE_TARGETS["diarisation"][tier]["engine"]
            if engine in installed:
                diarization_runtime.select_backend(engine)
                if engine == diarization_runtime.SORTFORMER:
                    from services import model_manager

                    model_manager.unload_diarization_pipeline()
                activated["diarisation"] = {"engine": engine}
    return activated


def _activate_budgeted_profile(tier: str) -> dict:
    """Apply one shared plan; never independently spend the full RAM six times."""
    from core import prefs
    from services import asr_backend, diarization_runtime, tts_backend

    state = profile_state(tier)
    activated = {}
    for family, entry in state["plan"]["families"].items():
        selected = entry["selection"]
        if selected is None or entry["reason"] == "kept":
            continue
        engine, model = selected["engine"], selected["model"]
        if family == "tts":
            if tts_backend.active_backend_id() != engine:
                # Normal next-use switching releases the outgoing engine.
                prefs.set_("tts_backend", engine)
        elif family == "asr":
            if asr_backend.faster_whisper_model_id() != model:
                asr_backend.select_faster_whisper_model(model)
            prefs.set_("asr_backend", engine)
        elif family == "dictation":
            if prefs.get("dictation.model_id") != model:
                prefs.set_("dictation.model_id", model)
                asr_backend._capture_backend = None
                asr_backend._capture_backend_key = None
        elif family == "translation":
            prefs.set_("translation_backend", engine)
        elif family == "diarisation":
            if diarization_runtime.selected_backend() != engine:
                diarization_runtime.select_backend(engine)
            if engine == diarization_runtime.SORTFORMER:
                from services import model_manager
                model_manager.unload_diarization_pipeline()
        activated[family] = selected
    # Audio hot paths read the applied result, without probing model inventory.
    prefs.update_mapping(_PERFORMANCE_PROFILE_KEY, {"resolved": state["effective"]})
    return activated


def reconcile_active_profile() -> dict[str, dict]:
    """Reapply a persisted profile after installs or an app restart.

    Older builds persisted the slider but selected models only for Max. That
    left installed ASR/Dictation models stranded behind stale missing choices.
    Reconciliation is startup-only, installed-only, and never downloads.
    """
    from core import prefs

    # The UI presents Balanced as the selected initial value, so the runtime
    # must honor it even before the user changes the control for the first time.
    stored = prefs.get(_PERFORMANCE_PROFILE_KEY, {})
    if not isinstance(stored, dict):
        return {}
    global_tier = str(stored.get("global", "balanced")).lower()
    if global_tier not in (*_PERFORMANCE_TIERS, "auto"):
        global_tier = "balanced"
    return activate_performance_tier(global_tier)


def tts_defaults(engine: str = "omnivoice") -> dict:
    """Only map sampling controls verified for the selected engine family."""
    tier = requested_tier("tts")
    if tier is None or engine not in TIERED_TTS_ENGINES:
        return {}
    target = _PERFORMANCE_TARGETS["tts"][tier]
    return {"num_step": target["steps"], "postprocess_output": target["postprocess"]}


def asr_decode_defaults() -> dict:
    """Bound Faster-Whisper's search effort without changing language coverage."""
    tier = requested_tier("asr")
    if tier is None:
        return {}
    target = _PERFORMANCE_TARGETS["asr"][tier]
    return {"beam_size": target["beam_size"], "best_of": target["best_of"]}


def translation_decode_defaults() -> dict:
    """Adjust local NLLB search effort without changing the chosen provider."""
    tier = requested_tier("translation")
    if tier is None:
        return {}
    return {"num_beams": _PERFORMANCE_TARGETS["translation"][tier]["num_beams"]}


def dictation_decode_defaults() -> dict:
    """Tune Sherpa transducer search without changing the selected language model."""
    tier = requested_tier("dictation")
    if tier is None:
        return {}
    target = _PERFORMANCE_TARGETS["dictation"][tier]
    return {
        "decoding_method": target["decoding_method"],
        "max_active_paths": target["max_active_paths"],
    }
