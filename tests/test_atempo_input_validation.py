"""Invalid stretch inputs must fail promptly instead of looping forever."""
import importlib
import os
import subprocess
import sys
from pathlib import Path

import pytest


def _run_rejection(code):
    env = dict(os.environ)
    env["PYTHONPATH"] = str(Path(__file__).resolve().parents[1] / "backend")
    with subprocess.Popen([sys.executable, "-B", "-c", code], env=env,
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
        try:
            # Start the short hang deadline only after imports/worker startup.
            assert process.stdout.readline().strip() == "ready"
            _output, error = process.communicate(timeout=3)
            assert process.returncode == 0, error
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()


@pytest.mark.parametrize("value", ["0", "-1", "inf", "-inf", "nan"])
def test_invalid_atempo_ratios_fail_in_a_bounded_child(value):
    # A subprocess timeout makes the regression safe even on the old infinite
    # loop. Run the production function, not a copied implementation.
    code = f"""
from services.ffmpeg_utils import _atempo_chain
print("ready", flush=True)
try:
    _atempo_chain(float({value!r}))
except ValueError:
    pass
else:
    raise AssertionError('invalid ratio was accepted')
"""
    _run_rejection(code)


@pytest.mark.parametrize("ratio", [.125, .5, 1, 2, 8])
def test_valid_ratios_keep_bounded_stages_and_the_same_product(ratio):
    from services.ffmpeg_utils import _atempo_chain
    values = [float(stage.partition("=")[2]) for stage in _atempo_chain(ratio).split(",")]
    assert all(.5 <= value <= 2 for value in values)
    import math
    assert math.prod(values) == pytest.approx(ratio)


def test_empty_waveform_is_rejected_before_starting_ffmpeg(monkeypatch):
    import asyncio
    import torch
    ffmpeg_utils = importlib.import_module("services.ffmpeg_utils")

    async def unexpected_spawn(*args, **kwargs):
        pytest.fail("empty audio must not spawn ffmpeg")

    monkeypatch.setattr(ffmpeg_utils, "spawn_subprocess", unexpected_spawn)
    # Test this in the child as well so baseline zero/positive loops stay bounded.
    code = """
import asyncio
import torch
from services.ffmpeg_utils import _pitch_preserving_stretch
print("ready", flush=True)
try:
    asyncio.run(_pitch_preserving_stretch(torch.empty(1, 0), 240, 24000))
except RuntimeError:
    pass
else:
    raise AssertionError('empty waveform was accepted')
"""
    _run_rejection(code)
    # In-process call proves the same path fails before the mocked subprocess.
    with pytest.raises(RuntimeError, match="empty"):
        asyncio.run(ffmpeg_utils._pitch_preserving_stretch(torch.empty(1, 0), 240, 24000))


def test_existing_noop_targets_keep_the_original_tensor():
    import asyncio
    import torch
    from services.ffmpeg_utils import _pitch_preserving_stretch

    for wav, target in [(torch.empty(1, 0), 0), (torch.ones(1, 4), 0), (torch.ones(1, 4), 4)]:
        assert asyncio.run(_pitch_preserving_stretch(wav, target, 24000)) is wav
