"""#3422 — expressive controls must match what the active engine renders.

Insert menus offered OmniVoice's reaction tags on every engine, and the
emotion popover vanished for all but IndexTTS2, so users could neither find
direction nor tell which tags would be spoken as words. Engines now declare
``inline_tags`` and ``instruct_directs_delivery``; the catalogue derives
``expression_caps`` from them.
"""
import importlib
import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]


def _registry():
    tts_backend = importlib.import_module("services.tts_backend")
    entries = {
        entry["id"]: entry
        for entry in tts_backend.list_backends(include_hidden=True)
    }
    return tts_backend, entries


def test_omnivoice_tags_match_the_model_tokenizer():
    """The declared set is the one the bundled model tokenizes standalone."""
    expression = importlib.import_module("services.expression")
    source = (REPO / "omnivoice" / "models" / "omnivoice.py").read_text(encoding="utf-8")
    block = re.search(r"_NONVERBAL_PATTERN = re\.compile\((.*?)\n\)", source, re.DOTALL)
    assert block, "OmniVoice's _NONVERBAL_PATTERN moved; update OMNIVOICE_INLINE_TAGS"
    pattern = "".join(re.findall(r'r"([^"]*)"', block.group(1)))
    names = re.fullmatch(r"\\\[\((.*)\)\\\]", pattern).group(1).split("|")
    assert expression.OMNIVOICE_INLINE_TAGS == tuple(f"[{name}]" for name in names)


def test_catalogue_reports_each_engines_mechanism():
    """Each engine kind reports the mechanism its UI panel is built from."""
    _, entries = _registry()
    expression = importlib.import_module("services.expression")
    assert entries["omnivoice"]["expression_caps"] == {
        "emotion": "none",
        "tags": list(expression.OMNIVOICE_INLINE_TAGS),
    }
    assert entries["indextts2"]["expression_caps"] == {"emotion": "graded", "tags": []}
    assert entries["cosyvoice"]["expression_caps"] == {
        "emotion": "direction",
        "tags": ["[breath]", "[laughter]"],
    }
    assert entries["voxcpm2"]["expression_caps"] == {"emotion": "direction", "tags": []}
    assert entries["kittentts"]["expression_caps"] == {"emotion": "none", "tags": []}


def test_every_entry_has_a_well_formed_capability():
    """No engine, declared or not, ships a malformed capability to clients."""
    _, entries = _registry()
    for bid, entry in entries.items():
        caps = entry["expression_caps"]
        assert caps["emotion"] in {"graded", "direction", "none"}, bid
        assert all(re.fullmatch(r"\[[a-z-]+\]", tag) for tag in caps["tags"]), bid


def test_sidecar_twins_declare_the_same_capabilities():
    """A subprocess variant that drops a flag would gray out working controls."""
    tts_backend = importlib.import_module("services.tts_backend")
    expression = importlib.import_module("services.expression")
    pairs = [
        (tts_backend.OmniVoiceBackend, "engines.omnivoice_subprocess", "OmniVoiceSubprocessBackend"),
        (tts_backend.OmniVoiceBackend, "engines.omnivoice_subprocess", "OmniVoiceMPSSubprocessBackend"),
        (tts_backend.CosyVoiceBackend, "engines.cosyvoice_subprocess", "CosyVoiceSubprocessBackend"),
        (tts_backend.VoxCPM2Backend, "engines.voxcpm2_subprocess", "VoxCPM2SubprocessBackend"),
    ]
    for in_process, module, name in pairs:
        twin = getattr(importlib.import_module(module), name)
        assert expression.expression_caps(twin) == expression.expression_caps(in_process), name
