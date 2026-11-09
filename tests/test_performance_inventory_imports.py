"""Performance reconciliation inventories installs rather than importing model runtimes."""
import importlib
from types import SimpleNamespace

import pytest


def test_selectable_translation_inventory_never_imports_runtime(monkeypatch):
    profiles = importlib.import_module("services.performance_profiles")
    translation = importlib.import_module("services.translation_engines")
    sherpa = importlib.import_module("services.sherpa_dictation")
    monkeypatch.setattr(profiles, "_installed_asr_models", lambda: [])
    monkeypatch.setattr(sherpa, "sherpa_available", lambda: (False, "absent"))
    monkeypatch.setattr(profiles, "_installed_dictation_models", lambda: [])
    def installed(engine, *, import_check=True):
        assert import_check is False, "Inventory imported translation runtime"
        return engine == "nllb"
    monkeypatch.setattr(translation, "is_ready", installed)
    assert profiles._installed_selectable_families() == {"translation"}


def test_family_translation_selection_validates_in_child_without_parent_import(monkeypatch):
    profiles = importlib.import_module("services.performance_profiles")
    translation = importlib.import_module("services.translation_engines")
    prefs = importlib.import_module("core.prefs")
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    def installed(engine, *, import_check=True):
        assert import_check is False, "Preset selection imported translation runtime"
        return engine == "nllb"
    monkeypatch.setattr(translation, "is_ready", installed)
    monkeypatch.setattr(profiles, "selection_ready", lambda family, engine: engine == "nllb")
    assert profiles._activate_translation_model("quality")["engine"] == "nllb"


def test_faster_whisper_inventory_never_enumerates_or_imports_all_engines(monkeypatch):
    profiles = importlib.import_module("services.performance_profiles")
    asr = importlib.import_module("services.asr_backend")
    from importlib import util
    monkeypatch.setattr(util, "find_spec", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})
    monkeypatch.setattr(asr, "_probe_available", lambda cls: pytest.fail("Inventory probed a runtime"))
    monkeypatch.setattr(asr, "list_backends", lambda: pytest.fail("Inventory enumerated all runtime engines"))
    assert profiles._faster_whisper_backend() == "faster-whisper"


def test_cached_tts_inventory_does_not_call_model_runtime_availability(monkeypatch):
    """Build cached TTS inventory without importing or probing model runtimes."""
    inventory = importlib.import_module("services.performance_inventory")
    profiles = importlib.import_module("services.performance_profiles")
    models = importlib.import_module("api.routers.setup.models")
    tts = importlib.import_module("services.tts_backend")
    routing = importlib.import_module("services.engine_routing")
    prefs = importlib.import_module("core.prefs")
    caps = importlib.import_module("core.device_caps")
    translation = importlib.import_module("services.translation_engines")
    sherpa = importlib.import_module("services.sherpa_dictation")
    diarization = importlib.import_module("services.diarization_runtime")
    from importlib import util
    monkeypatch.setattr(util, "find_spec", lambda name: SimpleNamespace(name=name))
    for key in ("OMNIVOICE_MODEL", "OMNIVOICE_TTS_BACKEND", "OMNIVOICE_KITTENTTS_MODEL", "OMNIVOICE_ASR_BACKEND"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(inventory, "hardware_snapshot", lambda: {
        "device": "cpu", "ram_gb": 32, "vram_gb": 0, "cpu_threads": 8, "probe_ok": True})
    monkeypatch.setattr(caps, "detect_host_caps", lambda: SimpleNamespace(family="cpu"))
    monkeypatch.setattr(models, "_model_supported", lambda model: True)
    monkeypatch.setattr(models, "is_cached", lambda repo: True)
    monkeypatch.setattr(models, "cache_is_complete", lambda model: True)
    monkeypatch.setattr(tts, "get_backend_class", lambda engine: SimpleNamespace(
        is_available=lambda: pytest.fail("TTS inventory imported OmniVoice")))
    monkeypatch.setattr(routing, "runtime_compute_profile", lambda *args: {
        "effective_device": "cpu", "routing_status": "cpu_only"})
    monkeypatch.setattr(profiles, "_preset_asr_models", lambda engine: [])
    monkeypatch.setattr(profiles, "_faster_whisper_backend", lambda: None)
    monkeypatch.setattr(profiles, "_installed_dictation_models", lambda: [])
    monkeypatch.setattr(sherpa, "sherpa_available", lambda: (False, "absent"))
    monkeypatch.setattr(translation, "is_ready", lambda engine, **kw: False)
    monkeypatch.setattr(diarization, "installed_backends", lambda: set())
    selections = {name: {"engine": "inactive", "model": None} for name in profiles._PERFORMANCE_FAMILIES}
    selections["tts"] = {"engine": "omnivoice", "model": "k2-fsa/OmniVoice"}
    plan = inventory.profile_plan("max", {}, selections)
    assert plan["families"]["tts"]["selection"]["engine"] == "omnivoice"


def test_profile_auto_selection_does_not_execute_asr_availability_imports(monkeypatch):
    """Resolve profile auto selections without executing heavy ASR availability imports."""
    profiles = importlib.import_module("services.performance_profiles")
    inventory = importlib.import_module("services.performance_inventory")
    asr = importlib.import_module("services.asr_backend")
    tts = importlib.import_module("services.tts_backend")
    diarization = importlib.import_module("services.diarization_runtime")
    prefs = importlib.import_module("core.prefs")
    from importlib import util
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: default)
    monkeypatch.setattr(util, "find_spec", lambda name: SimpleNamespace(name=name))
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})
    monkeypatch.setattr(asr, "_mps_available", lambda: False)
    monkeypatch.setattr(asr, "_rocm_torch", lambda: False)
    monkeypatch.setattr(asr, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(asr.WhisperXBackend, "is_available", classmethod(
        lambda cls: pytest.fail("Profile auto detection imported WhisperX/Transformers")))
    monkeypatch.setattr(tts, "active_backend_id", lambda: "custom")
    monkeypatch.setattr(diarization, "installed_backends", lambda: set())
    monkeypatch.setattr(profiles, "_installed_selectable_families", lambda: set())
    monkeypatch.setattr(profiles, "active_asr_selection", lambda: "pytorch-whisper")
    monkeypatch.setattr(inventory, "profile_plan", lambda *args: {"families": {
        family: {"tier": "balanced", "selection": None, "reason": "kept"}
        for family in profiles._PERFORMANCE_FAMILIES}})
    assert profiles.profile_state()["selections"]["asr"]["engine"] == "pytorch-whisper"


def test_inventory_context_never_suppresses_real_runtime_preflight(monkeypatch):
    readiness = importlib.import_module("services.performance_readiness")
    asr = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(readiness, "package_available", lambda name: True)
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})
    calls = []
    class BrokenPipeline:
        id = "pytorch-whisper"
        @classmethod
        def is_available(cls):
            calls.append("real-runtime-probe")
            return False, "broken transitive dependency"
    @readiness.inventory_only
    def inventory_failure():
        assert asr._probe_available(BrokenPipeline)
        assert not calls
        raise ValueError("inventory stopped")
    with pytest.raises(ValueError, match="inventory stopped"):
        inventory_failure()
    assert not asr._probe_available(BrokenPipeline)
    assert calls == ["real-runtime-probe"]
