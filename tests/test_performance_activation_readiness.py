"""Optimistic inventory must not become an unready persisted runtime choice."""
import importlib

import pytest


@pytest.fixture
def activation(monkeypatch):
    profiles = importlib.import_module("services.performance_profiles")
    asr = importlib.import_module("services.asr_backend")
    prefs = importlib.import_module("core.prefs")
    ready = importlib.import_module("services.performance_readiness")
    values, models = {}, []
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(prefs, "set_", lambda key, value: values.__setitem__(key, value))
    monkeypatch.setattr(prefs, "is_env_shadowed", lambda key: False)
    monkeypatch.setattr(ready, "package_available", lambda name: True)
    monkeypatch.setattr(asr, "_DEEP_IMPORT_BROKEN", {})
    monkeypatch.setattr(asr, "_mps_available", lambda: False)
    monkeypatch.setattr(asr, "_rocm_torch", lambda: False)
    monkeypatch.setattr(asr, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(asr.WhisperXBackend, "is_available", classmethod(lambda cls: (False, "CT2 unavailable")))
    monkeypatch.setattr(asr.FasterWhisperBackend, "is_available", classmethod(lambda cls: (False, "CT2 unavailable")))
    monkeypatch.setattr(asr.PyTorchWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(profiles, "_installed_ct2_models", lambda: [
        {"repo_id": "Systran/faster-whisper-medium", "size_gb": 2}])
    monkeypatch.setattr(profiles, "_installed_mlx_whisper_model", lambda: None)
    monkeypatch.setattr(asr, "faster_whisper_model_id", lambda: "missing/old")
    monkeypatch.setattr(asr, "select_faster_whisper_model", models.append)
    monkeypatch.setattr(profiles, "selection_ready", lambda *args: False, raising=False)
    monkeypatch.setattr(profiles, "active_asr_selection", lambda: "pytorch-whisper", raising=False)
    return profiles, asr, prefs, values, models


def test_unusable_ct2_cannot_replace_working_auto_pytorch_fallback(activation):
    """Preserve a working PyTorch fallback when the tier candidate cannot load CT2."""
    profiles, asr, _prefs, values, models = activation
    assert asr.active_backend_id() == "pytorch-whisper"
    assert profiles.activate_performance_tier("balanced", "asr") == {}
    assert not models and "asr_backend" not in values
    assert asr.active_backend_id() == "pytorch-whisper"


def test_validated_ready_asr_selection_applies(activation, monkeypatch):
    profiles, _asr, _prefs, values, models = activation
    monkeypatch.setattr(profiles, "active_asr_selection", lambda: "whisperx")
    monkeypatch.setattr(profiles, "selection_ready", lambda family, engine: True)
    assert profiles.activate_performance_tier("balanced", "asr") == {
        "asr": {"engine": "faster-whisper", "model": "Systran/faster-whisper-medium"}}
    assert values["asr_backend"] == "faster-whisper"
    assert models == ["Systran/faster-whisper-medium"]


def test_unready_global_choices_never_write_any_managed_engine(activation, monkeypatch):
    """Reject every unready global candidate before changing managed preferences."""
    profiles, _asr, prefs, values, models = activation
    choices = {"tts": ("omnivoice", "k2-fsa/OmniVoice"),
               "asr": ("faster-whisper", "Systran/faster-whisper-medium"),
               "dictation": ("offline-whisper", "sherpa-whisper-tiny"),
               "translation": ("nllb", "facebook/nllb-200-distilled-600M")}
    monkeypatch.setattr(profiles, "profile_state", lambda tier: {
        "effective": {family: "balanced" for family in choices},
        "plan": {"families": {family: {"selection": {"engine": engine, "model": model},
                                      "reason": "installed"}
                              for family, (engine, model) in choices.items()}}})
    monkeypatch.setattr(prefs, "update_mapping", lambda *args: None)
    assert profiles.activate_performance_tier("balanced") == {}
    assert not values and not models


def test_explicit_user_engine_pin_is_preserved_without_child_checks(activation, monkeypatch):
    profiles, _asr, _prefs, values, _models = activation
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    monkeypatch.setattr(profiles, "selection_ready", lambda *args: pytest.fail("Pinned engine probed"))
    assert profiles.activate_performance_tier("balanced", "asr") == {}
    assert not values


@pytest.mark.parametrize("key,environment", [
    ("asr_backend", False), ("asr_model_faster", False), ("compute_device", False),
    ("performance_profile", False), ("OMNIVOICE_ASR_BACKEND", True),
    ("ASR_MODEL_FASTER", True),
])
def test_direct_asr_keeps_choice_changed_during_readiness(activation, monkeypatch, key, environment):
    """Preserve ASR preferences or overrides changed during the readiness probe."""
    profiles, _asr, _prefs, values, models = activation
    monkeypatch.setattr(profiles, "active_asr_selection", lambda: "whisperx")

    def ready(*args):
        """Simulate a user changing the selected preference or environment override."""
        if environment:
            monkeypatch.setenv(key, "new-user-choice")
        else:
            values[key] = "new-user-choice"
        return True

    monkeypatch.setattr(profiles, "selection_ready", ready)
    assert profiles.activate_performance_tier("balanced", "asr") == {}
    assert not models and values.get("asr_backend") != "faster-whisper"


def test_direct_asr_snapshot_precedes_slow_active_resolution(activation, monkeypatch):
    """Capture ASR choices before slow backend resolution can race a user change."""
    profiles, _asr, _prefs, values, models = activation

    def resolve():
        values["asr_backend"] = "pytorch-whisper"
        return "whisperx"

    monkeypatch.setattr(profiles, "active_asr_selection", resolve)
    monkeypatch.setattr(profiles, "selection_ready", lambda *args: True)
    assert profiles.activate_performance_tier("balanced", "asr") == {}
    assert values["asr_backend"] == "pytorch-whisper" and not models


@pytest.mark.parametrize("family,key", [
    ("tts", "tts_backend"), ("asr", "asr_backend"),
    ("dictation", "dictation.model_id"), ("translation", "translation_backend"),
])
def test_global_plan_keeps_manual_choice_made_during_probe(activation, monkeypatch, family, key):
    """Preserve manual family selections changed while probing a global plan."""
    profiles, _asr, prefs, values, models = activation
    monkeypatch.setattr(profiles, "profile_state", lambda tier: {
        "effective": {family: "balanced"}, "plan": {"families": {
            family: {"selection": {"engine": "preset-engine", "model": "preset-model"},
                     "reason": "installed"}}}})
    monkeypatch.setattr(prefs, "update_mapping", lambda *args: None)

    def ready(*args):
        values[key] = "new-user-choice"
        return True

    monkeypatch.setattr(profiles, "selection_ready", ready)
    assert profiles.activate_performance_tier("balanced") == {}
    assert values[key] == "new-user-choice" and not models


def test_global_snapshot_precedes_profile_state_and_preserves_new_tier(activation, monkeypatch):
    """Preserve engine and tier changes made while computing the global plan."""
    profiles, _asr, prefs, values, models = activation
    mappings = []

    def plan(tier):
        values.update(asr_backend="pytorch-whisper", performance_profile={"global": "fast"})
        return {"effective": {"asr": "balanced"}, "plan": {"families": {
            "asr": {"selection": {"engine": "faster-whisper", "model": "preset-model"},
                    "reason": "installed"}}}}

    monkeypatch.setattr(profiles, "profile_state", plan)
    monkeypatch.setattr(profiles, "selection_ready", lambda *args: True)
    monkeypatch.setattr(prefs, "update_mapping", lambda *args: mappings.append(args))
    assert profiles.activate_performance_tier("balanced") == {}
    assert values["asr_backend"] == "pytorch-whisper" and not models and not mappings


def test_readiness_wait_releases_preferences_lock_but_application_holds_it(activation, monkeypatch):
    """Allow writes during readiness checks and serialize actual preference changes."""
    import threading

    profiles, asr, prefs, _values, _models = activation
    observations = []
    monkeypatch.setattr(profiles, "active_asr_selection", lambda: "whisperx")

    def ready(*args):
        """Probe whether another thread can acquire the preference lock during readiness."""
        def writer():
            """Record lock availability and release it immediately when acquired."""
            acquired = prefs._MUTATE_LOCK.acquire(timeout=1)
            observations.append(acquired)
            if acquired:
                prefs._MUTATE_LOCK.release()
        thread = threading.Thread(target=writer)
        thread.start()
        thread.join(timeout=2)
        return True

    monkeypatch.setattr(profiles, "selection_ready", ready)
    monkeypatch.setattr(asr, "select_faster_whisper_model", lambda repo:
                        observations.append(prefs._MUTATE_LOCK._is_owned()))
    assert profiles.activate_performance_tier("balanced", "asr")
    assert observations == [True, True]
