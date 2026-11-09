"""#3340: a CPU reference encode that cannot fit must fail cleanly, once.

A CPU-only Windows host (8 GB RAM) died with an access violation inside the
DAC encoder while preparing a voice-clone prompt. Three guards:

* the encode is refused up front when free RAM + swap cannot hold it;
* a host-RAM failure is never retried through the inline path (which repeats
  the same encode on the same CPU);
* the encode runs without autograd even when called directly by the cache.
"""
from __future__ import annotations

import pytest
import torch

from core.failure import classify, is_host_oom
from omnivoice.utils import audio as oa
from services import tts_backend as tb

GIB = 1024 ** 3


def test_estimate_grows_with_reference_length():
    assert oa.reference_encode_bytes(20) > oa.reference_encode_bytes(5) > oa.reference_encode_bytes(0)
    # Measured 2.17 GB for a 20 s clip; the estimate must not undershoot it.
    assert oa.reference_encode_bytes(20) >= int(2.1 * GIB)


def test_refuses_when_ram_plus_swap_cannot_hold_the_encode(monkeypatch):
    monkeypatch.delenv(oa.REF_ENCODE_MEMORY_CHECK_ENV, raising=False)
    with pytest.raises(oa.ReferenceEncodeMemoryError) as excinfo:
        oa.check_reference_encode_memory(20.0, "cpu", probe=lambda: int(0.5 * GIB))
    message = str(excinfo.value)
    assert "20-second" in message and "shorter reference" in message
    # Recognized as host-RAM exhaustion, so the UI shows that remedy.
    assert is_host_oom(excinfo.value)
    assert classify(excinfo.value) == "HOST_MEMORY_EXHAUSTED"


def test_allows_when_memory_is_available(monkeypatch):
    monkeypatch.delenv(oa.REF_ENCODE_MEMORY_CHECK_ENV, raising=False)
    oa.check_reference_encode_memory(20.0, "cpu", probe=lambda: 8 * GIB)


def test_a_collection_that_frees_enough_lets_the_encode_run(monkeypatch):
    monkeypatch.delenv(oa.REF_ENCODE_MEMORY_CHECK_ENV, raising=False)
    readings = iter([int(0.5 * GIB), 4 * GIB])
    oa.check_reference_encode_memory(10.0, "cpu", probe=lambda: next(readings))


@pytest.mark.parametrize("device", ["cuda", "mps"])
def test_non_cpu_devices_are_not_checked(device):
    oa.check_reference_encode_memory(20.0, device, probe=lambda: 0)


def test_unknown_memory_never_blocks():
    oa.check_reference_encode_memory(20.0, "cpu", probe=lambda: None)


def test_opt_out_env_skips_the_check(monkeypatch):
    monkeypatch.setenv(oa.REF_ENCODE_MEMORY_CHECK_ENV, "1")
    oa.check_reference_encode_memory(20.0, "cpu", probe=lambda: 0)


class _HostOomModel:
    def __init__(self, error):
        self.calls = 0
        self.error = error

    def create_voice_clone_prompt(self, ref_audio, ref_text=None, preprocess_prompt=True):
        self.calls += 1
        raise self.error


@pytest.fixture(autouse=True)
def _clear_cache():
    tb.clear_clone_prompt_cache()
    yield
    tb.clear_clone_prompt_cache()


@pytest.mark.parametrize("error", [
    oa.ReferenceEncodeMemoryError("Not enough free memory"),
    RuntimeError("[enforce fail at alloc_cpu.cpp:117] . DefaultCPUAllocator: "
                 "not enough memory: you tried to allocate 2000000000 bytes."),
])
def test_host_oom_in_precompute_is_raised_not_retried_inline(tmp_path, error):
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"\x00" * 100)
    m = _HostOomModel(error)
    with pytest.raises(type(error)):
        tb._get_clone_prompt(m, str(ref), "hello")
    assert m.calls == 1


class _GradProbeTokenizer:
    device = torch.device("cpu")

    class config:
        hop_length = 4

    def __init__(self):
        self.grad_enabled = None

    def encode(self, wav):
        self.grad_enabled = torch.is_grad_enabled()

        class _Out:
            audio_codes = torch.zeros(1, 8, 3, dtype=torch.long)

        return _Out()


def test_prompt_encode_runs_without_autograd(monkeypatch):
    from omnivoice.models import omnivoice as om

    model = om.OmniVoice.__new__(om.OmniVoice)
    tokenizer = _GradProbeTokenizer()
    object.__setattr__(model, "audio_tokenizer", tokenizer)
    object.__setattr__(model, "sampling_rate", 24000)
    monkeypatch.setattr(om, "check_reference_encode_memory", lambda *a, **k: None)
    wav = torch.sin(torch.linspace(0, 200, 24000)).unsqueeze(0) * 0.3
    assert torch.is_grad_enabled()
    prompt = model.create_voice_clone_prompt((wav, 24000), ref_text="hello", preprocess_prompt=False)
    assert prompt.ref_text == "hello"
    assert tokenizer.grad_enabled is False
