"""Selection snapshots cover non-ASR presets and detached mutable values."""
import importlib
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("family,key,environment", [
    ("dictation", "dictation.model_id", False),
    ("dictation", "OMNIVOICE_SHERPA_ASR_MODEL", True),
    ("translation", "translation_backend", False),
])
def test_direct_family_keeps_manual_choice_made_during_readiness(monkeypatch, family, key, environment):
    """Preserve dictation or translation choices changed during readiness checks."""
    profiles = importlib.import_module("services.performance_profiles")
    prefs = importlib.import_module("core.prefs")
    values, writes = {}, []
    monkeypatch.delenv("OMNIVOICE_SHERPA_ASR_MODEL", raising=False)
    monkeypatch.setattr(prefs, "get", lambda key, default=None: values.get(key, default))
    monkeypatch.setattr(prefs, "set_", lambda *args: writes.append(args))
    monkeypatch.setattr(profiles, "package_available", lambda name: True)
    monkeypatch.setattr(profiles, "_installed_dictation_models", lambda: [
        SimpleNamespace(id="preset-model", kind="offline-transducer", size_gb=1)])

    def ready(*args):
        """Simulate a user changing a preference or environment selection during probing."""
        if environment:
            monkeypatch.setenv(key, "new-user-choice")
        else:
            values[key] = "new-user-choice"
        return True

    monkeypatch.setattr(profiles, "selection_ready", ready)
    assert profiles.activate_performance_tier("balanced", family) == {}
    assert not writes


def test_snapshot_copies_mutable_profile_without_retaining_its_live_dict(monkeypatch):
    """Detect profile mutations through an independent selection snapshot."""
    selection = importlib.import_module("services.performance_selection")
    prefs = importlib.import_module("core.prefs")
    profile = {"global": "balanced"}
    monkeypatch.setattr(prefs, "get", lambda key: profile if key == "performance_profile" else None)
    snapshot = selection.selection_snapshot("asr")
    profile["global"] = "fast"
    with selection.unchanged_selection(snapshot) as unchanged:
        assert not unchanged
