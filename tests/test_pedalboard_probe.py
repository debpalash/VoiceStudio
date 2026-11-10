"""pedalboard is probed in a child process so a CPU-level crash cannot kill the backend (#3848)."""
import subprocess
import sys
import types

import pytest
import torch

from services import audio_dsp

CHAIN = [{"type": "highpass", "cutoff_hz": 80}]


class _ExplodingPedalboard(types.ModuleType):
    """Stands in for a wheel that kills the process on import/use."""

    def __getattr__(self, name):
        raise AssertionError("pedalboard was imported in the backend process")


@pytest.fixture(autouse=True)
def _fresh_verdict(monkeypatch):
    monkeypatch.setattr(audio_dsp, "_pedalboard_verdict", None)


def _fake_run(returncode=0, calls=None, raises=None):
    def run(cmd, **kwargs):
        if calls is not None:
            calls.append(cmd)
        if raises:
            raise raises
        return subprocess.CompletedProcess(cmd, returncode, b"", b"")

    return run


def test_crashing_probe_degrades_without_importing_pedalboard(monkeypatch):
    monkeypatch.setitem(sys.modules, "pedalboard", _ExplodingPedalboard("pedalboard"))
    monkeypatch.setattr(audio_dsp.subprocess, "run", _fake_run(returncode=-4))
    audio = torch.zeros(1, 480)
    assert audio_dsp.apply_effects_chain(audio, 24000, CHAIN) is audio


def test_verdict_is_cached_so_the_child_runs_once(monkeypatch):
    calls = []
    monkeypatch.setitem(sys.modules, "pedalboard", _ExplodingPedalboard("pedalboard"))
    monkeypatch.setattr(audio_dsp.subprocess, "run", _fake_run(returncode=132, calls=calls))
    audio = torch.zeros(1, 480)
    for _ in range(3):
        audio_dsp.apply_effects_chain(audio, 24000, CHAIN)
    assert len(calls) == 1
    assert calls[0][1:] == ["-c", "import pedalboard"]


@pytest.mark.parametrize("raises", [OSError("no exec"), subprocess.TimeoutExpired("x", 1)])
def test_inconclusive_probe_keeps_the_in_process_import(monkeypatch, raises):
    monkeypatch.setattr(audio_dsp.subprocess, "run", _fake_run(raises=raises))
    assert audio_dsp._pedalboard_loadable() is True


def test_healthy_probe_lets_the_chain_run(monkeypatch):
    pytest.importorskip("pedalboard")
    monkeypatch.setattr(audio_dsp.subprocess, "run", _fake_run(returncode=0))
    audio = torch.randn(1, 4800)
    out = audio_dsp.apply_effects_chain(audio, 24000, CHAIN)
    assert out.shape == audio.shape
    assert not torch.equal(out, audio)
