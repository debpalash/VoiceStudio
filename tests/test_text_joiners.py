"""Orthographic joiners must survive the shared pre-TTS safety pass (#2902)."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from services.text_normalization import _strip_unsafe_controls, normalize_text


class TextJoinerTests(unittest.TestCase):
    def test_joiners_survive_normalization_and_repeated_passes(self):
        cases = [
            ("fa", "\u0645\u06cc\u200c\u062f\u0648\u0646\u0645"),
            ("fa", "\u062e\u0648\u0646\u0647\u200c\u0647\u0627"),
            ("ur", "a\u200cb"),
            ("ku", "a\u200cb"),
            ("hi", "\u0915\u094d\u200d\u0937"),
            (None, "\U0001f469\u200d\U0001f4bb"),
        ]
        for language, text in cases:
            with self.subTest(language=language, text=text):
                self.assertEqual(_strip_unsafe_controls(text), text)
                self.assertEqual(normalize_text(text, language), text)
                self.assertEqual(normalize_text(normalize_text(text, language), language), text)

    def test_adjacent_zero_width_and_direction_controls_are_still_removed(self):
        for control in ("\u200b", "\u200e", "\u200f", "\u202e", "\u2060", "\ufeff"):
            with self.subTest(control=repr(control)):
                self.assertEqual(normalize_text("a" + control + "b"), "ab")


if __name__ == "__main__":
    unittest.main()
