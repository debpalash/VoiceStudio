"""Recovery from valid JSON that is not a preferences object."""
import json

import pytest

from core import prefs


@pytest.mark.parametrize("stored", [["old"], "old", 42, True, None, []])
@pytest.mark.parametrize("operation", ["get", "set", "update", "delete"])
def test_non_object_preferences_recover(tmp_path, monkeypatch, stored, operation):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps(stored), encoding="utf-8")
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(path))
    if operation == "get":
        assert prefs.get("translation_backend", "argos") == "argos"
        assert json.loads(path.read_text()) == stored
    elif operation == "set":
        prefs.set_("translation_backend", "argos")
        assert json.loads(path.read_text()) == {"translation_backend": "argos"}
    elif operation == "update":
        prefs.update_mapping("performance", {"workers": 2})
        assert json.loads(path.read_text()) == {"performance": {"workers": 2}}
    else:
        prefs.delete("translation_backend")
        assert json.loads(path.read_text()) == {}


def test_object_preferences_preserve_unrelated_values(tmp_path, monkeypatch):
    path = tmp_path / "prefs.json"
    path.write_text(json.dumps({"unknown": [1, 2], "translation_backend": "argos"}))
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(path))
    assert prefs.get("translation_backend") == "argos"
    prefs.set_("tts_backend", "omnivoice")
    assert json.loads(path.read_text())["unknown"] == [1, 2]
