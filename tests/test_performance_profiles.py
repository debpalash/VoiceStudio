from services import performance_profiles as profiles
from types import SimpleNamespace

import pytest

_installed_mlx_whisper_model = profiles._installed_mlx_whisper_model

MLX_LARGE = {"repo_id": "mlx-community/whisper-large-v3-mlx", "size_gb": 3.0}


@pytest.fixture(autouse=True)
def no_metal_whisper(monkeypatch):
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: None)


def test_diarisation_is_applicable_only_with_a_real_runtime_choice(monkeypatch):
    from core import prefs
    from services import asr_backend, diarization_runtime, sherpa_dictation, tts_backend

    values = {
        "dictation.enabled": True,
        "dictation.model_id": "sherpa-parakeet-tdt-v3",
        "translation_backend": "nllb",
        "performance_profile": {"global": "balanced"},
    }
    monkeypatch.setattr(prefs, "get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(sherpa_dictation, "get_spec", sherpa_dictation.get_spec)

    monkeypatch.setattr(
        diarization_runtime,
        "installed_backends",
        lambda: {diarization_runtime.PYANNOTE},
    )
    assert "diarisation" not in profiles.profile_state()["applicable_families"]

    monkeypatch.setattr(
        diarization_runtime,
        "installed_backends",
        lambda: {diarization_runtime.PYANNOTE, diarization_runtime.SORTFORMER},
    )
    state = profiles.profile_state()
    assert "diarisation" in state["implemented_families"]
    assert "diarisation" in state["applicable_families"]
    assert state["targets"]["diarisation"]["engine"] == diarization_runtime.SORTFORMER
    assert state["selections"]["tts"] == {
        "engine": "omnivoice",
        "model": "k2-fsa/OmniVoice",
    }
    assert state["selections"]["dictation"]["model"] == "sherpa-parakeet-tdt-v3"
    assert state["selections"]["translation"]["model"] == "facebook/nllb-200-distilled-600M"


def test_diarisation_preset_switches_only_between_installed_runtimes(monkeypatch):
    from services import diarization_runtime, model_manager

    monkeypatch.delenv("OMNIVOICE_DIARIZATION_BACKEND", raising=False)
    monkeypatch.setattr(
        diarization_runtime,
        "installed_backends",
        lambda: {diarization_runtime.PYANNOTE, diarization_runtime.SORTFORMER},
    )
    selected = []
    monkeypatch.setattr(diarization_runtime, "select_backend", selected.append)
    unloaded = []
    monkeypatch.setattr(model_manager, "unload_diarization_pipeline", lambda: unloaded.append(True))

    fast = profiles.activate_performance_tier("fast", "diarisation")
    quality = profiles.activate_performance_tier("quality", "diarisation")

    assert selected == [diarization_runtime.SORTFORMER, diarization_runtime.PYANNOTE]
    assert unloaded == [True]
    assert fast == {"diarisation": {"engine": diarization_runtime.SORTFORMER}}
    assert quality == {"diarisation": {"engine": diarization_runtime.PYANNOTE}}


def test_diarisation_preset_respects_external_runtime_pin(monkeypatch):
    from services import diarization_runtime

    monkeypatch.setenv("OMNIVOICE_DIARIZATION_BACKEND", diarization_runtime.PYANNOTE)
    selected = []
    monkeypatch.setattr(diarization_runtime, "select_backend", selected.append)

    assert profiles.activate_performance_tier("fast", "diarisation") == {}
    assert selected == []


def test_balanced_preset_recovers_an_installed_asr_model(monkeypatch):
    from core import prefs
    from services import asr_backend

    selected = []
    writes = []
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda _key: False)
    monkeypatch.setattr(
        profiles,
        "_installed_ct2_models",
        lambda: [
            {"repo_id": "local/small", "size_gb": 0.5},
            {"repo_id": "local/balanced", "size_gb": 1.5},
            {"repo_id": "local/max", "size_gb": 3.0},
        ],
    )
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    monkeypatch.setattr(asr_backend, "faster_whisper_model_id", lambda: "missing/old")
    monkeypatch.setattr(asr_backend, "select_faster_whisper_model", selected.append)
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "whisperx")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))

    result = profiles.activate_performance_tier("balanced", "asr")

    assert result == {
        "asr": {"engine": "faster-whisper", "model": "local/balanced"}
    }
    assert selected == ["local/balanced"]
    assert writes == [("asr_backend", "faster-whisper")]


def test_balanced_preset_recovers_an_installed_dictation_model(monkeypatch):
    from core import prefs
    from services import asr_backend, sherpa_dictation

    writes = []
    model = SimpleNamespace(
        id="sherpa-parakeet-tdt-v3",
        kind="offline-transducer",
        size_gb=0.67,
    )
    monkeypatch.delenv("OMNIVOICE_SHERPA_ASR_MODEL", raising=False)
    monkeypatch.setattr(sherpa_dictation, "sherpa_available", lambda: (True, "ready"))
    monkeypatch.setattr(profiles, "_installed_dictation_models", lambda: [model])
    monkeypatch.setattr(prefs, "get", lambda key, default=None: "missing/old")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(asr_backend, "_capture_backend", object())
    monkeypatch.setattr(asr_backend, "_capture_backend_key", "old")

    result = profiles.activate_performance_tier("balanced", "dictation")

    assert result == {
        "dictation": {
            "engine": "offline-transducer",
            "model": "sherpa-parakeet-tdt-v3",
        }
    }
    assert writes == [("dictation.model_id", "sherpa-parakeet-tdt-v3")]
    assert asr_backend._capture_backend is None
    assert asr_backend._capture_backend_key is None


def test_preset_recovers_from_an_unavailable_network_translator(monkeypatch):
    from core import prefs
    from services import translation_engines

    writes = []
    monkeypatch.setattr(prefs, "get", lambda key, default=None: "google")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(
        translation_engines,
        "is_ready",
        lambda engine: engine in {"argos", "nllb"},
    )

    result = profiles.activate_performance_tier("max", "translation")

    assert result == {
        "translation": {
            "engine": "nllb",
            "model": "facebook/nllb-200-distilled-600M",
        }
    }
    assert writes == [("translation_backend", "nllb")]


def test_preset_keeps_a_ready_explicit_network_translator(monkeypatch):
    from core import prefs
    from services import translation_engines

    writes = []
    monkeypatch.setattr(prefs, "get", lambda key, default=None: "google")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(translation_engines, "is_ready", lambda engine: engine == "google")

    assert profiles.activate_performance_tier("max", "translation") == {}
    assert writes == []


def test_preset_uses_the_remaining_ready_local_translator(monkeypatch):
    from core import prefs
    from services import translation_engines

    writes = []
    monkeypatch.setattr(prefs, "get", lambda key, default=None: "google")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(translation_engines, "is_ready", lambda engine: engine == "argos")

    result = profiles.activate_performance_tier("max", "translation")

    assert result == {"translation": {"engine": "argos", "model": "argos"}}
    assert writes == [("translation_backend", "argos")]


def test_persisted_profile_is_reconciled_as_one_shared_budget(monkeypatch):
    from core import prefs

    calls = []
    monkeypatch.setattr(
        prefs,
        "get",
        lambda key, default=None: {
            "global": "balanced",
            "asr": "quality",
        }
        if key == "performance_profile"
        else default,
    )
    monkeypatch.setattr(
        profiles,
        "activate_performance_tier",
        lambda tier, family=None: calls.append((tier, family)) or {},
    )

    assert profiles.reconcile_active_profile() == {}
    assert calls == [("balanced", None)]


def test_initial_visible_balanced_profile_is_reconciled(monkeypatch):
    from core import prefs

    calls = []
    monkeypatch.setattr(prefs, "get", lambda _key, default=None: default)
    monkeypatch.setattr(
        profiles,
        "activate_performance_tier",
        lambda tier, family=None: calls.append((tier, family)) or {},
    )

    assert profiles.reconcile_active_profile() == {}
    assert calls == [("balanced", None)]


def test_tiered_tts_engines_are_registered_omnivoice_runtimes():
    """The tier gate must name engine ids the registry can actually activate;
    a stale id there silently ignored the speed/quality tier for the sidecar."""
    from services import tts_backend

    assert profiles.TIERED_TTS_ENGINES == {"omnivoice", "omnivoice-subprocess"}
    for engine in profiles.TIERED_TTS_ENGINES:
        assert engine in tts_backend._REGISTRY


def test_tier_applies_to_the_omnivoice_sidecar(monkeypatch):
    from core import prefs
    from services import asr_backend, tts_backend

    values = {"performance_profile": {"global": "fast"}}
    monkeypatch.setattr(prefs, "get", lambda key, default=None: values.get(key, default))
    fast = profiles.tts_defaults("omnivoice")
    assert fast and profiles.tts_defaults("omnivoice-subprocess") == fast
    assert profiles.tts_defaults("kittentts") == {}

    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "omnivoice-subprocess")
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "faster-whisper")
    state = profiles.profile_state()
    assert "tts" in state["applicable_families"]
    assert state["selections"]["tts"]["model"] == "k2-fsa/OmniVoice"
    assert state["tts_tiered_engines"] == ["omnivoice", "omnivoice-subprocess"]


@pytest.fixture
def metal_host(monkeypatch):
    from api.routers.setup import models
    from services import asr_backend

    monkeypatch.delenv("ASR_MODEL", raising=False)
    monkeypatch.setattr(asr_backend, "_mps_available", lambda: True)
    monkeypatch.setattr(asr_backend, "_probe_available", lambda cls: True)
    monkeypatch.setattr(models, "_model_supported", lambda model: True)
    monkeypatch.setattr(models, "is_cached", lambda repo: True)
    monkeypatch.setattr(models, "cache_is_complete", lambda model: True)
    return models


def test_metal_whisper_is_the_installed_model_the_mlx_backend_loads(metal_host):
    assert _installed_mlx_whisper_model()["repo_id"] == "mlx-community/whisper-large-v3-mlx"


def test_metal_whisper_needs_a_metal_host(metal_host, monkeypatch):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "_mps_available", lambda: False)
    assert _installed_mlx_whisper_model() is None


@pytest.mark.parametrize("missing", ["unknown", "unsupported", "uncached", "incomplete"])
def test_metal_whisper_must_be_a_complete_supported_download(metal_host, monkeypatch, missing):
    if missing == "unknown":
        monkeypatch.setenv("ASR_MODEL", "someone/custom-whisper")
    else:
        attr = {"unsupported": "_model_supported", "uncached": "is_cached",
                "incomplete": "cache_is_complete"}[missing]
        monkeypatch.setattr(metal_host, attr, lambda _value: False)
    assert _installed_mlx_whisper_model() is None


def test_metal_whisper_replaces_its_cpu_only_copy(monkeypatch):
    tiny = {"repo_id": "Systran/faster-whisper-tiny", "size_gb": 0.08}
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        tiny, {"repo_id": "Systran/faster-whisper-large-v3", "size_gb": 2.9},
    ])
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: MLX_LARGE)

    assert profiles._installed_asr_models() == [("faster-whisper", tiny), ("mlx-whisper", MLX_LARGE)]


def test_metal_whisper_is_offered_without_a_ctranslate2_runtime(monkeypatch):
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: None)
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: MLX_LARGE)

    assert profiles._installed_asr_models() == [("mlx-whisper", MLX_LARGE)]


def test_asr_preset_selects_metal_whisper_instead_of_pinning_the_cpu(monkeypatch):
    from core import prefs
    from services import asr_backend

    writes = []
    selected = []
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda _key: False)
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: "faster-whisper")
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        {"repo_id": "Systran/faster-whisper-large-v3", "size_gb": 2.9},
    ])
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: MLX_LARGE)
    monkeypatch.setattr(asr_backend, "select_faster_whisper_model", selected.append)
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))

    assert profiles.activate_performance_tier("max", "asr") == {
        "asr": {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx"}
    }
    assert selected == []
    assert writes == [("asr_backend", "mlx-whisper")]


def test_shared_plan_applies_metal_whisper_without_a_ctranslate2_selection(monkeypatch):
    from core import prefs
    from services import asr_backend

    writes = []
    selected = []
    pick = {"engine": "mlx-whisper", "model": "mlx-community/whisper-large-v3-mlx"}
    monkeypatch.setattr(profiles, "profile_state", lambda tier=None: {
        "effective": {"asr": "max"},
        "plan": {"families": {"asr": {"selection": pick, "reason": "fits"}}},
    })
    monkeypatch.setattr(asr_backend, "select_faster_whisper_model", selected.append)
    monkeypatch.setattr(prefs, "set_", lambda key, value: writes.append((key, value)))
    monkeypatch.setattr(prefs, "update_mapping", lambda *_args: None)

    assert profiles.activate_performance_tier("max") == {"asr": pick}
    assert selected == []
    assert writes == [("asr_backend", "mlx-whisper")]


def test_metal_whisper_advertises_no_decoding_effort_it_cannot_apply(monkeypatch):
    from core import prefs
    from services import asr_backend, diarization_runtime, tts_backend

    values = {"performance_profile": {"global": "max"}}
    monkeypatch.setattr(prefs, "get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(tts_backend, "active_backend_id", lambda: "omnivoice")
    monkeypatch.setattr(diarization_runtime, "installed_backends", lambda: set())
    monkeypatch.setattr(asr_backend, "active_backend_id", lambda: "mlx-whisper")
    monkeypatch.delenv("ASR_MODEL", raising=False)

    state = profiles.profile_state()

    assert "asr" in state["applicable_families"]
    assert state["targets"]["asr"] == {"engine": "mlx-whisper"}
    assert state["selections"]["asr"] == {
        "engine": "mlx-whisper",
        "model": "mlx-community/whisper-large-v3-mlx",
    }
