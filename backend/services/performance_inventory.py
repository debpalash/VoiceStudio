"""Installed-only adapter for the performance planner. No model construction."""
from __future__ import annotations

import os

from services.performance_budget import Candidate, hardware_snapshot, make_plan


def profile_plan(choice: str, overrides: dict, selections: dict) -> dict:
    from core import prefs

    from services import (
        diarization_runtime,
        sherpa_dictation,
        translation_engines,
        tts_backend,
    )
    from services import performance_profiles as profiles

    hardware = hardware_snapshot()
    dedicated = hardware["device"] in {"cuda", "rocm", "xpu"}
    inventory: dict[str, list[Candidate]] = {}
    fixed: dict[str, Candidate] = {}
    current: dict[str, Candidate] = {}

    # These are advisory working-set estimates, deliberately independent of
    # disk size. Unknown/custom engines keep their user's explicit selection.
    for family, selected in selections.items():
        engine, model = selected["engine"], selected.get("model")
        if not model or engine == "inactive":
            continue
        ram, vram = {"tts": (8, 6), "asr": (4, 5), "translation": (3, 3),
                     "dictation": (3, 0), "diarisation": (2, 2), "llm": (8, 6)}[family]
        if engine == "kittentts":
            ram, vram = .5, 0
        current[family] = Candidate(engine, model, 1, ram, vram if dedicated else 0)

    current_tts = selections["tts"]["engine"]
    from services.model_manager import resolve_omnivoice_checkpoint
    custom_checkpoint = resolve_omnivoice_checkpoint() != "k2-fsa/OmniVoice"
    if not custom_checkpoint and not any(os.environ.get(key) for key in ("OMNIVOICE_TTS_BACKEND", "OMNIVOICE_KITTENTTS_MODEL")) and current_tts in {
        "kittentts", "omnivoice", "omnivoice-subprocess",
    }:
        from api.routers.setup.models import (
            KNOWN_MODELS,
            _model_supported,
            cache_is_complete,
            is_cached,
        )
        from core.device_caps import detect_host_caps

        from services.engine_routing import runtime_compute_profile

        tts = []
        # Never replace a cloning engine with a preset-voice-only engine.
        omni_runtime = current_tts if current_tts.startswith("omnivoice") else "omnivoice"
        choices = [(omni_runtime, "k2-fsa/OmniVoice", 4, 8, 6, "OmniVoice")]
        if current_tts == "kittentts":
            choices.append(("kittentts", "KittenML/kitten-tts-mini-0.8", 1, .5, 0, "KittenTTS"))
        for engine, repo, rank, ram, vram, label in choices:
            model = next((m for m in KNOWN_MODELS if m["repo_id"] == repo), None)
            if not model or not _model_supported(model) or not is_cached(repo) or not cache_is_complete(model):
                continue
            # Respect custom checkpoint overrides; this catalogue entry would
            # otherwise claim to select weights the runtime won't actually use.
            if engine == "kittentts" and os.environ.get("OMNIVOICE_KITTENTTS_MODEL"):
                continue
            cls = tts_backend.get_backend_class(engine)
            if not cls.is_available()[0]:
                continue
            routing = runtime_compute_profile(cls, detect_host_caps())
            if routing["routing_status"] == "unavailable":
                continue
            tts.append(Candidate(engine, repo, rank, ram,
                                 vram if routing["effective_device"] in {"cuda", "rocm", "xpu"} else 0, label))
        inventory["tts"] = tts
    elif "tts" in current:
        fixed["tts"] = current["tts"]

    asr_engine = current["asr"].engine if "asr" in current else None
    installed = None
    if not (
        os.environ.get("OMNIVOICE_ASR_BACKEND")
        or prefs.is_env_shadowed("ASR_MODEL_FASTER")
        or (asr_engine is not None and asr_engine not in profiles.TIERED_ASR_ENGINES)
    ):
        installed = profiles._preset_asr_models(asr_engine)
    # A Mac on MLX Whisper with no other MLX model keeps its GPU engine.
    if installed is None or (asr_engine == "mlx-whisper" and not installed):
        if "asr" in current:
            fixed["asr"] = current["asr"]
    else:
        if installed or profiles._faster_whisper_backend():
            from core.windows_rocm.ct2 import hip_ct2_available

            ct2_gpu = hardware["device"] == "cuda" or (
                hardware["device"] == "rocm" and hip_ct2_available()
            )
            models = []
            for backend, model in installed:
                repo = model["repo_id"]
                # Explicit Whisper quality order, not download-size sorting.
                rank, ram, vram = (4, 4, 5)
                if "turbo" in repo or "medium" in repo:
                    rank, ram, vram = 3, 3, 3
                elif "small" in repo:
                    rank, ram, vram = 2, 1.5, 1.5
                elif "tiny" in repo or "base" in repo:
                    rank, ram, vram = 1, .6, .6
                # Verified Windows HIP CT2 consumes VRAM just like CUDA; MLX
                # runs on Apple's unified memory, which the RAM budget covers.
                models.append(Candidate(backend, repo, rank, ram,
                                        vram if ct2_gpu and backend != "mlx-whisper" else 0))
            inventory["asr"] = models

    if os.environ.get("OMNIVOICE_SHERPA_ASR_MODEL") or not prefs.get("dictation.enabled", True):
        if "dictation" in current:
            fixed["dictation"] = current["dictation"]
    elif sherpa_dictation.sherpa_available()[0]:
        inventory["dictation"] = [
            Candidate(m.kind, m.id, 4 if "parakeet-tdt-v3" in m.id else
                      3 if "parakeet" in m.id else 2 if "bilingual" in m.id else 1,
                      3 if "parakeet" in m.id else .7,
                      label=m.label)
            for m in profiles._installed_dictation_models()
        ]

    translator = selections["translation"]["engine"]
    if translator == "nllb" and translation_engines.is_ready(translator, import_check=False):
        # A device-wide preset has no source/target language pair to validate.
        # Argos' installed status cannot prove it replaces NLLB's coverage.
        if "translation" in current:
            fixed["translation"] = current["translation"]
    elif translator not in {"argos", "nllb"} and translation_engines.is_ready(translator, import_check=False):
        if "translation" in current:
            fixed["translation"] = Candidate(translator, current["translation"].model, 1, 0)
    else:
        inventory["translation"] = [
            Candidate(engine, repo, rank, ram, vram if dedicated else 0)
            for engine, repo, rank, ram, vram in [
                ("argos", "argos", 1, 1, 0),
                ("nllb", "facebook/nllb-200-distilled-600M", 4, 3, 3),
            ] if translation_engines.is_ready(engine, import_check=False)
        ]
    if os.environ.get("OMNIVOICE_DIARIZATION_BACKEND"):
        if "diarisation" in current:
            fixed["diarisation"] = current["diarisation"]
    else:
        installed = diarization_runtime.installed_backends()
        inventory["diarisation"] = [
            Candidate(engine, repo, rank, ram, vram if dedicated else 0)
            for engine, repo, rank, ram, vram in [
                (diarization_runtime.SORTFORMER, diarization_runtime.SORTFORMER_REPO, 1, 1.5, 1.5),
                (diarization_runtime.PYANNOTE, "pyannote/speaker-diarization-3.1", 4, 2, 2),
            ] if engine in installed
        ]
    # Retained unmanaged engines must consume budget too.
    for family, candidates in inventory.items():
        selected = selections.get(family, {})
        match = next((c for c in candidates if c.model == selected.get("model")), None)
        if match is not None:
            current[family] = match
    for family, candidate in current.items():
        if family not in inventory and family not in fixed:
            fixed[family] = candidate
    result = make_plan(choice, hardware, inventory, fixed, overrides, current)
    result["max_status"] = make_plan("max", hardware, inventory, fixed, current=current)["status"]
    return result
