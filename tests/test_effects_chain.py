"""
Tests for the audio effects chain DSP pipeline.

Pure functions — no GPU or model loading required.
Tests run in seconds on any machine.

Note: sys.path for backend imports is handled by tests/conftest.py.
"""

import pytest
import torch
import math
pedalboard = pytest.importorskip("pedalboard")

from services.audio_dsp import (
    apply_effects_chain,
    get_effect_chain,
    list_effect_presets,
    EFFECT_PRESETS,
)


def _make_test_audio(duration_s=1.0, sample_rate=24000) -> torch.Tensor:
    """Create a test audio tensor with a simple sine wave."""
    t = torch.linspace(0, duration_s, int(duration_s * sample_rate))
    return torch.sin(2 * math.pi * 440 * t).unsqueeze(0)  # 440 Hz sine, mono


class TestListEffectPresets:
    def test_returns_all_presets(self):
        presets = list_effect_presets()
        assert len(presets) == 6
        ids = [p["id"] for p in presets]
        assert "broadcast" in ids
        assert "cinematic" in ids
        assert "podcast" in ids
        assert "raw" in ids
        assert "warm" in ids
        assert "bright" in ids

    def test_preset_has_required_fields(self):
        for preset in list_effect_presets():
            assert "id" in preset
            assert "label" in preset
            assert "icon" in preset
            assert "description" in preset


class TestGetEffectChain:
    def test_broadcast_returns_chain(self):
        chain = get_effect_chain("broadcast")
        assert len(chain) > 0
        types = [fx["type"] for fx in chain]
        assert "highpass" in types
        assert "leveler" in types  # #3412: levels, no fast compressor
        assert "compressor" not in types
        assert "limiter" in types

    def test_cinematic_has_reverb(self):
        chain = get_effect_chain("cinematic")
        types = [fx["type"] for fx in chain]
        assert "reverb" in types

    def test_raw_returns_empty(self):
        chain = get_effect_chain("raw")
        assert chain == []

    def test_unknown_returns_empty(self):
        chain = get_effect_chain("nonexistent")
        assert chain == []


class TestApplyEffectsChain:
    def test_raw_preset_returns_unmodified(self):
        audio = _make_test_audio()
        result = apply_effects_chain(audio, sample_rate=24000, chain=[])
        assert torch.equal(audio, result)

    def test_broadcast_preset_returns_tensor(self):
        audio = _make_test_audio()
        chain = get_effect_chain("broadcast")
        result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        assert isinstance(result, torch.Tensor)
        assert result.shape == audio.shape

    def test_cinematic_preset_returns_tensor(self):
        audio = _make_test_audio()
        chain = get_effect_chain("cinematic")
        result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        assert isinstance(result, torch.Tensor)
        assert result.shape == audio.shape

    def test_podcast_preset_returns_tensor(self):
        audio = _make_test_audio()
        chain = get_effect_chain("podcast")
        result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        assert isinstance(result, torch.Tensor)
        assert result.shape == audio.shape

    def test_warm_preset_returns_tensor(self):
        audio = _make_test_audio()
        chain = get_effect_chain("warm")
        result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        assert isinstance(result, torch.Tensor)
        assert result.shape == audio.shape

    def test_bright_preset_returns_tensor(self):
        audio = _make_test_audio()
        chain = get_effect_chain("bright")
        result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        assert isinstance(result, torch.Tensor)
        assert result.shape == audio.shape

    def test_all_presets_produce_output(self):
        """Smoke test: every preset processes without error."""
        audio = _make_test_audio()
        for preset_id in EFFECT_PRESETS:
            chain = get_effect_chain(preset_id)
            result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
            assert isinstance(result, torch.Tensor)
            assert result.shape[0] == 1  # mono

    def test_clipping_prevention(self):
        """Output should not exceed [-1.0, 1.0] range after limiter presets."""
        audio = _make_test_audio()
        for preset_id in ("broadcast", "podcast", "bright"):
            chain = get_effect_chain(preset_id)
            result = apply_effects_chain(audio, sample_rate=24000, chain=chain)
            assert result.abs().max() <= 1.0

    def test_different_presets_produce_different_output(self):
        """Different presets should produce audibly different output."""
        audio = _make_test_audio(duration_s=2.0)
        results = {}
        for preset_id in ("broadcast", "cinematic", "raw"):
            chain = get_effect_chain(preset_id)
            results[preset_id] = apply_effects_chain(audio, sample_rate=24000, chain=chain)
        # broadcast and cinematic should differ from raw (unprocessed)
        assert not torch.equal(results["broadcast"], results["raw"])
        assert not torch.equal(results["cinematic"], results["raw"])
