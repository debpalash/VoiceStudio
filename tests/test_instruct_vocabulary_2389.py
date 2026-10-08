"""#2389 — free-form voice descriptions were reduced to OmniVoice's tag set.

Only the OmniVoice family parses a closed voice-design vocabulary. Qwen3-TTS
VoiceDesign (mlx-audio), VoxCPM2 and audio.cpp read the description as written,
yet every client path ran it through the OmniVoice sanitizer first. Engines now
declare ``instruct_vocabulary``; the catalogue surfaces it so clients reduce
prose to tags only where the engine requires it.
"""
import importlib


def _registry():
    # Resolve per test: other suites rebind `services.tts_backend` in sys.modules.
    tts_backend = importlib.import_module("services.tts_backend")
    return tts_backend, {entry["id"]: entry for entry in tts_backend.list_backends(include_hidden=True)}


def test_catalogue_reports_vocabulary():
    _, entries = _registry()
    assert entries["omnivoice"]["instruct_vocabulary"] == "tags"
    assert entries["voxcpm2"]["instruct_vocabulary"] == "freeform"
    assert entries["mlx-audio"]["instruct_vocabulary"] == "freeform"


def test_every_omnivoice_engine_declares_tags():
    """A new OmniVoice variant that forgets the flag would 400 on prose."""
    tts_backend, entries = _registry()
    omnivoice = [bid for bid in entries if bid.startswith("omnivoice")]
    assert omnivoice
    for bid in omnivoice:
        assert entries[bid]["instruct_vocabulary"] == "tags", bid
    # The MPS proxy only replaces `omnivoice` at runtime, so check it directly.
    proxy = importlib.import_module("engines.omnivoice_subprocess").OmniVoiceMPSSubprocessBackend
    assert proxy.instruct_vocabulary == "tags"


def test_openai_route_keeps_prose_for_freeform_engines():
    tts_backend, _ = _registry()
    compat = importlib.import_module("api.routers.openai_compat")
    prose = "An elderly Scottish woman with a raspy, low voice"
    assert compat._engine_instructions(tts_backend.MLXAudioBackend, prose) == prose
    assert (
        compat._engine_instructions(tts_backend.OmniVoiceBackend, "female, whisper, speak cheerfully")
        == "female, whisper"
    )
