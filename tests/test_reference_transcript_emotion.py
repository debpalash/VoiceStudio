"""Saved takes pair their audio with spoken text."""

import pytest



@pytest.mark.parametrize("marker", ["[emotion:quiet]", "[EMOTION:quiet]", "[emotion:]", "[emotion:quiet\nand tense]"])
def test_saved_take_transcript_keeps_reactions_and_omits_emotion_directions(marker):
    import importlib

    take_reference_transcript = importlib.import_module("services.reference_transcript").take_reference_transcript  # resolved at test time
    assert take_reference_transcript(f"{marker} Hello [laughter] there.") == "Hello [laughter] there."
