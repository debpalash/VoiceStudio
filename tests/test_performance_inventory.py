"""Installed/runtime/locale gates and actual application of hardware plans."""
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

import importlib


@pytest.fixture(autouse=True)
def current_modules(monkeypatch):
    # Other suites exercise cold imports by removing modules from sys.modules.
    # Patch the same current modules that function-local runtime imports use.
    global profiles, inventory
    profiles = importlib.import_module("services.performance_profiles")
    inventory = importlib.import_module("services.performance_inventory")
    monkeypatch.setattr(importlib.import_module("services"), "performance_profiles", profiles)


@pytest.fixture
def local_inventory(monkeypatch):
    from api.routers.setup import models
    from core import prefs
    from services import tts_backend, engine_routing, sherpa_dictation, translation_engines, diarization_runtime

    for key in ("OMNIVOICE_TTS_BACKEND", "OMNIVOICE_MODEL", "OMNIVOICE_KITTENTTS_MODEL",
                "OMNIVOICE_ASR_BACKEND", "OMNIVOICE_SHERPA_ASR_MODEL", "OMNIVOICE_DIARIZATION_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(inventory, "hardware_snapshot", lambda: {
        "device": "cuda", "ram_gb": 32, "vram_gb": 8, "cpu_threads": 16, "probe_ok": True,
    })
    monkeypatch.setattr(models, "is_cached", lambda repo: True)
    monkeypatch.setattr(models, "cache_is_complete", lambda model: True)
    monkeypatch.setattr(models, "_model_supported", lambda model: True)
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda engine: SimpleNamespace(is_available=lambda: (True, "ready")))
    monkeypatch.setattr(engine_routing, "runtime_compute_profile", lambda *args: {
        "effective_device": "cuda", "routing_status": "accelerated",
    })
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        {"repo_id": "Systran/faster-whisper-tiny"}, {"repo_id": "Systran/faster-whisper-large-v3"},
    ])
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: None)
    monkeypatch.setattr(sherpa_dictation, "sherpa_available", lambda: (True, "ready"))
    monkeypatch.setattr(profiles, "_installed_dictation_models", lambda: [
        SimpleNamespace(id="sherpa-whisper-tiny", kind="offline-whisper", label="Whisper Tiny"),
        SimpleNamespace(id="sherpa-parakeet-tdt-v3", kind="offline-transducer", label="Parakeet v3"),
    ])
    monkeypatch.setattr(translation_engines, "is_ready", lambda engine, **_kw: False)
    monkeypatch.setattr(diarization_runtime, "installed_backends", lambda: set())
    selections = {family: {"engine": "inactive", "model": None} for family in profiles._PERFORMANCE_FAMILIES}
    selections["tts"] = {"engine": "kittentts", "model": "kittentts"}
    return selections


def test_real_inventory_prioritizes_omni_and_upgrades_dictation(local_inventory):
    plan = inventory.profile_plan("max", {}, local_inventory)
    assert plan["families"]["tts"]["selection"]["engine"] == "omnivoice"
    assert plan["families"]["asr"]["selection"]["model"].endswith("tiny")
    assert plan["families"]["dictation"]["selection"]["model"] == "sherpa-parakeet-tdt-v3"


def test_metal_host_plans_gpu_whisper_over_its_cpu_only_copy(local_inventory, monkeypatch):
    monkeypatch.setattr(inventory, "hardware_snapshot", lambda: {
        "device": "mps", "ram_gb": 32, "vram_gb": 0, "cpu_threads": 12, "probe_ok": True,
    })
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: {
        "repo_id": "mlx-community/whisper-large-v3-mlx",
    })
    entry = inventory.profile_plan("quality", {}, local_inventory)["families"]["asr"]
    assert entry["selection"]["engine"] == "mlx-whisper"
    assert entry["selection"]["model"] == "mlx-community/whisper-large-v3-mlx"


def test_auto_quality_does_not_strand_tts_or_dictation_on_the_smallest_model(local_inventory):
    plan = inventory.profile_plan("auto", {}, local_inventory)
    assert plan["resolved"] == "quality"
    assert plan["families"]["tts"]["selection"]["engine"] == "omnivoice"
    assert plan["families"]["tts"]["tier"] == "quality"
    assert plan["families"]["dictation"]["selection"]["model"] == "sherpa-parakeet-tdt-v3"
    assert plan["families"]["dictation"]["tier"] == "quality"


def test_incomplete_omni_is_not_selected(local_inventory, monkeypatch):
    from api.routers.setup import models
    monkeypatch.setattr(models, "cache_is_complete", lambda model: model["repo_id"] != "k2-fsa/OmniVoice")
    entry = inventory.profile_plan("max", {}, local_inventory)["families"]["tts"]
    assert entry["selection"]["engine"] == "kittentts"
    assert entry["reason"] == "installed"


def test_unavailable_omni_runtime_is_not_selected(local_inventory, monkeypatch):
    from services import tts_backend
    monkeypatch.setattr(tts_backend, "get_backend_class", lambda engine: SimpleNamespace(
        is_available=lambda: (engine == "kittentts", "missing")))
    assert inventory.profile_plan("max", {}, local_inventory)["families"]["tts"]["selection"]["engine"] == "kittentts"


def test_low_does_not_remove_voice_cloning(local_inventory):
    local_inventory["tts"] = {"engine": "omnivoice", "model": "k2-fsa/OmniVoice"}
    assert inventory.profile_plan("fast", {}, local_inventory)["families"]["tts"]["selection"]["engine"] == "omnivoice"


def test_env_pinned_tts_is_preserved(local_inventory, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "kittentts")
    entry = inventory.profile_plan("max", {}, local_inventory)["families"]["tts"]
    assert entry["selection"]["engine"] == "kittentts"
    assert entry["reason"] == "kept"


@pytest.mark.parametrize("runtime", ["omnivoice-subprocess", "omnivoice-isolated"])
def test_preserves_selected_omnivoice_isolation(local_inventory, runtime, monkeypatch):
    from services import tts_backend
    # The legacy isolated ID is not registered. Retain it without probing a
    # backend class that the runtime cannot resolve.
    available_class = tts_backend.get_backend_class("omnivoice")
    def registered_class(engine):
        if engine not in tts_backend._REGISTRY:
            raise ValueError(f"Unknown TTS backend: {engine!r}")
        return available_class
    monkeypatch.setattr(tts_backend, "get_backend_class", registered_class)
    local_inventory["tts"] = {"engine": runtime, "model": "k2-fsa/OmniVoice"}
    assert inventory.profile_plan("max", {}, local_inventory)["families"]["tts"]["selection"]["engine"] == runtime


def test_preserves_working_nllb_language_coverage(local_inventory, monkeypatch):
    from services import translation_engines
    local_inventory["translation"] = {"engine": "nllb", "model": "facebook/nllb-200-distilled-600M"}
    monkeypatch.setattr(translation_engines, "is_ready", lambda engine, **_kw: engine in {"argos", "nllb"})
    entry = inventory.profile_plan("fast", {}, local_inventory)["families"]["translation"]
    assert entry["reason"] == "kept"
    assert entry["selection"]["engine"] == "nllb"


def test_global_sortformer_switch_releases_pyannote(monkeypatch):
    from core import prefs
    from services import diarization_runtime, model_manager
    monkeypatch.setattr(profiles, "profile_state", lambda tier: {
        "effective": {"diarisation": "fast"}, "plan": {"families": {"diarisation": {
            "selection": {"engine": diarization_runtime.SORTFORMER, "model": "sortformer"}, "reason": "fits",
        }}},
    })
    monkeypatch.setattr(diarization_runtime, "selected_backend", lambda: diarization_runtime.PYANNOTE)
    monkeypatch.setattr(diarization_runtime, "select_backend", Mock())
    unload = Mock()
    monkeypatch.setattr(model_manager, "unload_diarization_pipeline", unload)
    monkeypatch.setattr(prefs, "update_mapping", Mock())
    profiles.activate_performance_tier("fast")
    unload.assert_called_once_with()


def test_language_incompatible_dictation_is_not_a_fallback(monkeypatch):
    from services import asr_backend, sherpa_dictation
    monkeypatch.setattr(asr_backend, "_locale_language", lambda: "ja")
    monkeypatch.setattr(sherpa_dictation, "is_installed", lambda spec: "parakeet" in spec.id)
    monkeypatch.setattr(sherpa_dictation, "is_demoted", lambda model: False)
    assert profiles._installed_dictation_models() == []


def test_global_activation_applies_exact_shared_plan_and_caches_defaults(monkeypatch):
    from core import prefs
    from services import tts_backend, asr_backend
    selected = {
        "tts": {"engine": "omnivoice", "model": "k2-fsa/OmniVoice"},
        "asr": {"engine": "faster-whisper", "model": "Systran/faster-whisper-tiny"},
        "dictation": {"engine": "offline-transducer", "model": "sherpa-parakeet-tdt-v3"},
    }
    state = {"effective": {"tts": "max", "asr": "fast", "dictation": "max"},
             "plan": {"families": {name: {"selection": pick, "reason": "fits"} for name, pick in selected.items()}}}
    monkeypatch.setattr(profiles, "profile_state", lambda tier=None: state)
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "kittentts")
    monkeypatch.setattr(asr_backend, "faster_whisper_model_id", lambda: "old")
    choose = Mock()
    write = Mock()
    cache = Mock()
    monkeypatch.setattr(asr_backend, "select_faster_whisper_model", choose)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    monkeypatch.setattr(prefs, "set_", write)
    monkeypatch.setattr(prefs, "update_mapping", cache)
    assert profiles.activate_performance_tier("auto") == selected
    write.assert_any_call("tts_backend", "omnivoice")
    write.assert_any_call("dictation.model_id", "sherpa-parakeet-tdt-v3")
    choose.assert_called_once_with("Systran/faster-whisper-tiny")
    cache.assert_called_once_with("performance_profile", {"resolved": state["effective"]})


def test_runtime_defaults_use_resolved_auto_without_probing(monkeypatch):
    from core import prefs
    monkeypatch.setattr(prefs, "get", lambda key, default=None: {
        "global": "auto", "resolved": {"tts": "max", "asr": "fast"},
    })
    assert profiles.tts_defaults() == {"num_step": 64, "postprocess_output": True}
    assert profiles.asr_decode_defaults() == {"beam_size": 1, "best_of": 1}


@pytest.mark.parametrize("verified,expected", [(True, 5), (False, 0)])
def test_verified_windows_hip_asr_reserves_vram(local_inventory, monkeypatch, verified, expected):
    monkeypatch.setattr(inventory, "hardware_snapshot", lambda: {
        "device": "rocm", "ram_gb": 32, "vram_gb": 16, "cpu_threads": 16, "probe_ok": True,
    })
    monkeypatch.setattr("core.windows_rocm.ct2.hip_ct2_available", lambda: verified)
    captured = {}
    def capture(choice, hardware, candidates, *args, **kwargs):
        captured.update(candidates)
        return {"status": "ready"}
    monkeypatch.setattr(inventory, "make_plan", capture)
    inventory.profile_plan("max", {}, local_inventory)
    assert captured["asr"][-1].vram == expected


@pytest.mark.parametrize("engine", ["mlx-whisper", "parakeet-mlx", "moonshine"])
def test_gpu_or_user_picked_asr_engine_is_kept(local_inventory, engine):
    # Apple Silicon auto-detects MLX Whisper on the GPU; a preset must not pin
    # CPU faster-whisper over it (or over any engine the user picked).
    local_inventory["asr"] = {"engine": engine, "model": engine}
    entry = inventory.profile_plan("max", {}, local_inventory)["families"]["asr"]
    assert entry["reason"] == "kept"
    assert entry["selection"]["engine"] == engine


def test_ct2_asr_engine_still_follows_the_preset(local_inventory):
    local_inventory["asr"] = {"engine": "whisperx", "model": "whisperx"}
    entry = inventory.profile_plan("max", {}, local_inventory)["families"]["asr"]
    assert entry["reason"] != "kept"
    assert entry["selection"]["engine"] == "faster-whisper"


def test_family_preset_does_not_pin_faster_whisper_over_mlx(monkeypatch):
    from core import prefs
    from services import asr_backend

    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "mlx-whisper")
    pinned = Mock()
    monkeypatch.setattr(prefs, "set_", pinned)
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        {"repo_id": "Systran/faster-whisper-large-v3", "size_gb": 3.1},
    ])
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    assert profiles._activate_asr_model("max") is None
    pinned.assert_not_called()


def test_mac_on_mlx_whisper_never_drops_to_cpu_whisper_for_a_small_tier(local_inventory, monkeypatch):
    # #3349 lets presets pick MLX Whisper; a Mac already on it must not be moved
    # to the smaller CPU-only CTranslate2 model by the Fast tier.
    monkeypatch.setattr(inventory, "hardware_snapshot", lambda: {
        "device": "mps", "ram_gb": 32, "vram_gb": 0, "cpu_threads": 12, "probe_ok": True,
    })
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: {
        "repo_id": "mlx-community/whisper-large-v3-mlx",
    })
    local_inventory["asr"] = {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx"}
    entry = inventory.profile_plan("fast", {}, local_inventory)["families"]["asr"]
    assert entry["selection"]["engine"] == "mlx-whisper"


def test_family_preset_on_mlx_keeps_to_mlx_models(monkeypatch):
    from core import prefs
    from services import asr_backend

    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "mlx-whisper")
    pinned = Mock()
    monkeypatch.setattr(prefs, "set_", pinned)
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        {"repo_id": "Systran/faster-whisper-base", "size_gb": 0.15},
    ])
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: {
        "repo_id": "mlx-community/whisper-large-v3-mlx", "size_gb": 3.0,
    })
    assert profiles._activate_asr_model("fast") == {
        "engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx",
    }
    pinned.assert_not_called()
