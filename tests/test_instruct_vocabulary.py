"""Engines declare how their ``instruct`` text is read ("tags" vs "freeform")."""
import os

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _catalogue():
    from services import tts_backend

    return {e["id"]: e for e in tts_backend.list_backends()}


def test_catalogue_reports_vocabulary_per_engine():
    cat = _catalogue()
    assert cat["omnivoice"]["instruct_vocabulary"] == "tags"
    assert cat["voxcpm2"]["instruct_vocabulary"] == "freeform"
    assert cat["mlx-audio"]["instruct_vocabulary"] == "freeform"


@pytest.mark.usefixtures("cpu_host")
def test_every_omnivoice_entry_is_tags():
    entries = [e for bid, e in _catalogue().items() if bid.startswith("omnivoice")]
    assert len(entries) >= 3
    assert {e["instruct_vocabulary"] for e in entries} == {"tags"}


def test_subprocess_class_declares_tags_itself():
    # Its registry id is replaced at runtime, so the class must carry the value.
    from engines.omnivoice_subprocess import OmniVoiceMPSSubprocessBackend

    assert "instruct_vocabulary" in vars(OmniVoiceMPSSubprocessBackend)
    assert OmniVoiceMPSSubprocessBackend.instruct_vocabulary == "tags"


def test_engine_instructions_follow_the_declared_vocabulary():
    from api.routers.openai_compat import _engine_instructions
    from services import tts_backend

    prose = "female, whisper, speak cheerfully"
    omni = tts_backend.OmniVoiceBackend()
    assert _engine_instructions(omni, prose) == "female, whisper"
    assert _engine_instructions(omni, "speak cheerfully") is None
    free = tts_backend.get_backend_class("mlx-audio")()
    assert _engine_instructions(free, prose) == prose
    assert _engine_instructions(free, "") is None
