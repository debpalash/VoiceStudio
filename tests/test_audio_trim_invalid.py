"""Silence trimming preserves invalid model output for later postprocessing."""
import ast
from pathlib import Path
from types import SimpleNamespace

import pytest
import torch


@pytest.mark.parametrize("helper", ["trim_trailing_silence", "trim_speech_padding"])
@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), -float("inf")])
@pytest.mark.parametrize("position", [0, -1])
@pytest.mark.parametrize("channels", [None, 1, 2])
def test_invalid_audio_remains_the_original_tensor(helper, invalid, position, channels):
    from services import audio_dsp

    audio = torch.zeros(121) if channels is None else torch.zeros(channels, 121)
    audio[..., 20:40] = .5
    if channels is None:
        audio[position] = invalid
    else:
        audio[-1, position] = invalid
    before = audio.clone()
    result = getattr(audio_dsp, helper)(audio, 100)
    assert result is audio
    assert torch.equal(torch.isfinite(result), torch.isfinite(before))
    torch.testing.assert_close(result, before, equal_nan=True)


def test_finite_audio_still_trims_each_supported_silence_boundary():
    from services.audio_dsp import trim_speech_padding, trim_trailing_silence

    audio = torch.zeros(2, 121)
    audio[:, 20:40] = .5
    torch.testing.assert_close(trim_trailing_silence(audio, 100), audio[:, :70])
    torch.testing.assert_close(trim_speech_padding(audio, 100), audio[:, 15:45])
    silent = torch.zeros(2, 121)
    assert trim_trailing_silence(silent, 100) is silent
    assert trim_speech_padding(silent, 100) is silent


def test_actual_voxcpm_finalizer_retains_invalid_tensor_before_postprocessing():
    # Execute the maintained method without importing the model/ML engine tree.
    source = Path(__file__).resolve().parents[1] / "backend/services/tts_backend.py"
    tree = ast.parse(source.read_text(encoding="utf-8"))
    cls = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "VoxCPM2Backend")
    method = next(n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == "_finalize")
    namespace = {"torch": torch}
    exec(compile(ast.Module(body=[method], type_ignores=[]), str(source), "exec"), namespace)
    audio = torch.zeros(1, 121)
    audio[:, :20] = .5
    audio[0, -1] = float("nan")
    assert namespace["_finalize"](SimpleNamespace(sample_rate=100), audio) is audio
