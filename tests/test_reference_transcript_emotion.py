"""Saved takes pair their audio with spoken text."""

import pytest

from services.reference_transcript import take_reference_transcript


@pytest.mark.parametrize("marker", ["[emotion:quiet]", "[EMOTION:quiet]", "[emotion:]", "[emotion:quiet\nand tense]"])
def test_saved_take_transcript_keeps_reactions_and_omits_emotion_directions(marker):
    assert take_reference_transcript(f"{marker} Hello [laughter] there.") == "Hello [laughter] there."
