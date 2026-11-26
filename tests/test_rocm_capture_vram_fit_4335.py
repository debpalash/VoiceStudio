"""ROCm dictation picks the PyTorch Whisper checkpoint by VRAM fit (#4335).

large-v3 needs 5.0 GB, so a 4 GB card never reached the GPU even with the
catalogue's turbo (3.6 GB) installed.
"""
import importlib
import sys
import types

import pytest

LARGE = "openai/whisper-large-v3"
TURBO = "openai/whisper-large-v3-turbo"


@pytest.fixture
def ab(monkeypatch):
    ab = importlib.import_module("services.asr_backend")
    monkeypatch.setattr(ab, "_capture_backend", None)
    monkeypatch.setattr(ab, "_capture_backend_key", None)
    monkeypatch.setattr(ab, "_rocm_capture_nofit_logged", set())
    monkeypatch.delenv("OMNIVOICE_PYTORCH_ASR_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_ASR_VRAM_PREFLIGHT", raising=False)
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab, "_capture_prefers_parakeet", lambda: False)
    for name in ("MLXWhisperBackend", "ParakeetMLXBackend"):
        monkeypatch.setattr(getattr(ab, name), "is_available",
                            classmethod(lambda cls: (False, "no")))
    monkeypatch.setattr(ab.FasterWhisperBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_ctranslate2_cuda_ok", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_gpu_probe_skipped", lambda: False)
    monkeypatch.setattr(ab, "_probe_available", lambda cls: True)
    return ab


def _host(ab, monkeypatch, *, installed, free_gb):
    monkeypatch.setattr(ab, "_repo_installed", lambda repo, **kw: repo in installed)
    fake = types.SimpleNamespace(cuda=types.SimpleNamespace(
        mem_get_info=lambda: (int(free_gb * 1024**3), 4 * 1024**3)))
    monkeypatch.setitem(sys.modules, "torch", fake)


def test_4gb_card_with_only_turbo_installed_uses_turbo(ab, monkeypatch):
    _host(ab, monkeypatch, installed={TURBO}, free_gb=3.9)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.PyTorchWhisperBackend)
    assert be._resolved_model_name() == TURBO
    assert ab._capture_whisper_repo() == TURBO


def test_ample_vram_prefers_large_v3(ab, monkeypatch):
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=16)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert be._resolved_model_name() == LARGE


def test_both_installed_on_4gb_falls_to_turbo(ab, monkeypatch):
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=3.9)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.PyTorchWhisperBackend)
    assert be._resolved_model_name() == TURBO


def test_nothing_fits_keeps_faster_whisper_and_logs_once(ab, monkeypatch, caplog):
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=1.0)
    with caplog.at_level("INFO"):
        be = ab._select_capture_asr_backend(skip_sherpa=True)
        ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.FasterWhisperBackend)
    assert sum("does not fit" in r.message for r in caplog.records) == 1


def test_nothing_installed_never_picks_pytorch(ab, monkeypatch):
    _host(ab, monkeypatch, installed=set(), free_gb=16)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.FasterWhisperBackend)


def test_explicit_pin_wins(ab, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PYTORCH_ASR_MODEL", "openai/whisper-small")
    _host(ab, monkeypatch, installed={LARGE, TURBO, "openai/whisper-small"}, free_gb=16)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert be._resolved_model_name() == "openai/whisper-small"


def test_loaded_backend_keeps_its_checkpoint_after_it_consumes_vram(ab, monkeypatch):
    _host(ab, monkeypatch, installed={LARGE}, free_gb=16)
    first = ab._select_capture_asr_backend(skip_sherpa=True)
    first._pipe = types.SimpleNamespace(device="cuda:0")  # resident on the GPU
    _host(ab, monkeypatch, installed={LARGE}, free_gb=0.5)  # its own weights now
    assert ab._select_capture_asr_backend(skip_sherpa=True) is first


@pytest.mark.parametrize("pipe", [None, types.SimpleNamespace(device="cpu")])
def test_cold_or_cpu_backend_is_not_sticky(ab, monkeypatch, pipe):
    # A large-v3 singleton holding no VRAM must not block turbo when the
    # card has since filled up (otherwise its loader would land on the CPU).
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=16)
    first = ab._select_capture_asr_backend(skip_sherpa=True)
    assert first._resolved_model_name() == LARGE
    first._pipe = pipe
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=3.9)
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.PyTorchWhisperBackend) and be._resolved_model_name() == TURBO


@pytest.mark.parametrize("gate", ["_gpu_probe_skipped", "_rocm_torch"])
def test_vram_probe_never_runs_when_the_rocm_tier_is_gated_off(ab, monkeypatch, gate):
    # The probe initialises the driver a "restart on CPU" recovery avoids
    # (#4006), and non-ROCm hosts must not pay for it at all.
    _host(ab, monkeypatch, installed={LARGE, TURBO}, free_gb=8.0)

    probes = []
    sys.modules["torch"].cuda.mem_get_info = lambda: probes.append(1) or (0, 0)
    monkeypatch.setattr(ab, gate, (lambda: True) if gate == "_gpu_probe_skipped"
                        else (lambda: False))
    be = ab._select_capture_asr_backend(skip_sherpa=True)
    assert isinstance(be, ab.FasterWhisperBackend)
    ab._capture_whisper_repo()
    assert probes == []  # the probe's fail-open would hide a raise
