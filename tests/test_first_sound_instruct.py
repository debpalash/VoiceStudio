"""The actual onboarding defaults must pass the runtime engine validator."""
import json
from pathlib import Path

def test_first_sound_instruction_passes_runtime_taxonomy_validation():
    from omnivoice.models.omnivoice import _resolve_instruct

    defaults = json.loads(
        (Path(__file__).parents[1] / "electron/src/shared/utils/firstSound.json").read_text()
    )
    instruct = defaults["instruct"]
    # VoiceDesign requires a nonempty description; OmniVoice validates tokens.
    assert instruct.strip()
    assert _resolve_instruct(instruct) == instruct
    assert _resolve_instruct(instruct, use_zh=True) == "中年，低音调"
