"""Completed GPU work, rather than enumeration alone, determines readiness."""
from __future__ import annotations

from types import SimpleNamespace

import pytest


@pytest.fixture
def failed_gpu(monkeypatch):
    from core import gpu_self_test

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setattr(gpu_self_test, "_thread", None)
    monkeypatch.setattr(
        gpu_self_test, "_verdict",
        gpu_self_test.Verdict("failed", "RuntimeError: HIPBLAS_STATUS_ALLOC_FAILED"),
    )


@pytest.mark.parametrize("family", ["cuda", "rocm"])
def test_completed_self_test_failure_matches_actual_parent_loader(monkeypatch, failed_gpu, family):
    from core import device_caps
    from services import model_manager
    from services.engine_routing import resolve_routing

    caps = device_caps.HostCaps(family=family, available_families=(family, "cpu"))
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(device_caps, "arch_unsupported", lambda _: None)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True),
        version=SimpleNamespace(hip=None),
    ))
    assert model_manager.get_best_device() == "cpu"

    route = resolve_routing((family, "cpu"), caps, self_test_policy="forceable")
    assert route["effective_device"] == "cpu"
    assert route["routing_status"] == "cpu_fallback"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in route["routing_reason"]


@pytest.mark.parametrize("vendor,backend", [("amd", "rocm"), ("nvidia", "cuda"), ("unknown", "cuda")])
@pytest.mark.parametrize("forced", [False, True])
def test_preflight_does_not_call_failed_gpu_ready(monkeypatch, tmp_path, failed_gpu, vendor, backend, forced):
    from api.routers.setup import wizard
    from services import media_tools

    if forced:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 32)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _: 100)
    monkeypatch.setattr(wizard, "hf_cache_dir", lambda: str(tmp_path / "hf"))
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "status": "pass", "detail": "Local test", "fix": None,
    })
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": vendor, "device_name": "Test GPU", "driver": "600.0",
        "backend": backend, "available": True, "notes": [],
    })
    monkeypatch.setattr(media_tools, "summary", lambda **_: {"ready": True})
    monkeypatch.setattr("services.tts_backend.gpu_routing_verdict", lambda: None)

    row = next(c for c in wizard.preflight()["checks"] if c["id"] == "gpu")
    assert row["status"] == "warn"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in row["detail"]
    assert ("explicitly forced" if forced else "running on CPU") in row["detail"]
    assert "ready" not in row["detail"]


@pytest.mark.parametrize("family", ["cuda", "rocm"])
def test_failed_parent_torch_does_not_downgrade_independent_runtime(failed_gpu, family):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps(family=family, available_families=(family, "cpu"))
    route = resolve_routing((family, "cpu"), caps, parent_torch=False, self_test_policy="forceable")
    assert route["effective_device"] == family
    assert route["routing_status"] == "accelerated"


@pytest.mark.parametrize("status", [None, "passed", "inconclusive", "skipped", "no_gpu"])
def test_readiness_does_not_wait_or_start_another_gpu_test(monkeypatch, status):
    from core import gpu_self_test
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    class PendingTest:
        def join(self, *args):
            pytest.fail("readiness must never wait for a GPU self-test")

    monkeypatch.setattr(gpu_self_test, "_thread", PendingTest())
    monkeypatch.setattr(gpu_self_test, "_verdict", None if status is None else gpu_self_test.Verdict(status))
    monkeypatch.setattr(gpu_self_test, "_run_child", lambda: pytest.fail("readiness must not launch GPU work"))
    route = resolve_routing(("cuda", "cpu"), HostCaps("cuda", ("cuda", "cpu")), self_test_policy="forceable")
    assert route["routing_status"] == "accelerated"
    assert route["routing_reason"] is None


def test_force_cuda_preserves_explicit_gpu_choice(monkeypatch, failed_gpu):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    route = resolve_routing(("cuda", "cpu"), HostCaps("cuda", ("cuda", "cpu")), self_test_policy="forceable")
    assert route["routing_status"] == "accelerated"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in route["routing_reason"]


def test_gpu_only_parent_engine_explains_failed_work(failed_gpu):
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    route = resolve_routing(("cuda",), HostCaps("cuda", ("cuda", "cpu")), self_test_policy="forceable")
    assert route["routing_status"] == "unavailable"
    assert "HIPBLAS_STATUS_ALLOC_FAILED" in (route["routing_reason"] or "")


def test_same_interpreter_child_does_not_inherit_parent_verdict(failed_gpu):
    from core.device_caps import HostCaps
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend

    caps = HostCaps("cuda", ("cuda", "cpu"))
    assert OmniVoiceSubprocessBackend.shares_parent_torch() is True
    route = OmniVoiceSubprocessBackend.runtime_compute_profile(caps)
    assert route["routing_status"] == "accelerated"
    assert route["effective_device"] == "cuda"


def test_completed_verdict_updates_routing_without_refreshing_host_caps(monkeypatch):
    from core import gpu_self_test
    from core.device_caps import HostCaps
    from services.engine_routing import resolve_routing

    caps = HostCaps("cuda", ("cuda", "cpu"))
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setattr(gpu_self_test, "_verdict", None)
    assert resolve_routing(("cuda", "cpu"), caps, self_test_policy="forceable")["routing_status"] == "accelerated"
    monkeypatch.setattr(gpu_self_test, "_verdict", gpu_self_test.Verdict("failed", "GPU test failure"))
    assert resolve_routing(("cuda", "cpu"), caps, self_test_policy="forceable")["routing_status"] == "cpu_fallback"


def test_faster_whisper_profile_matches_independent_ct2_loader(monkeypatch, failed_gpu):
    import sys

    from core.device_caps import HostCaps
    from core.windows_rocm import asr_isolation, ct2
    from services import asr_backend
    from services.engine_routing import runtime_compute_profile

    selected = []
    monkeypatch.setattr(asr_isolation, "ensure_isolated", lambda _: False)
    monkeypatch.setattr(ct2, "prepare_import", lambda: None)
    monkeypatch.setattr(ct2, "gpu_compatible", lambda: ("cuda", "cpu"))
    monkeypatch.setattr(asr_backend, "_ctranslate2_execstack_ok", lambda: (True, ""))
    monkeypatch.setattr(asr_backend, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(asr_backend, "_local_model_source", lambda _: "owned-installed-model")
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(
        WhisperModel=lambda *args, **kwargs: selected.append(kwargs["device"]) or object(),
    ))
    backend = asr_backend.FasterWhisperBackend()
    backend._ensure_model()
    assert selected == ["cuda"]
    profile = runtime_compute_profile(backend, HostCaps("cuda", ("cuda", "cpu")))
    assert profile["effective_device"] == backend._device == "cuda"
    assert profile["routing_status"] == "accelerated"


def test_remote_tts_profile_is_not_changed_by_parent_test(monkeypatch, failed_gpu):
    from contextlib import contextmanager
    import io
    import json
    import torch

    from core.device_caps import HostCaps
    from services import audio_io, outbound_http
    from services.tts_backend import GPTSoVITSBackend

    requests = []

    @contextmanager
    def respond(url, **kwargs):
        requests.append((kwargs["method"], json.loads(kwargs["body"])))
        yield io.BytesIO(b"owned server audio fixture")

    monkeypatch.setattr(outbound_http, "open_trusted_endpoint", respond)
    monkeypatch.setattr(audio_io, "load_audio", lambda _: (torch.zeros(1, 4), 32000))
    backend = GPTSoVITSBackend()
    assert backend.generate("Hello", language="en", ref_audio="owned-reference.wav").shape == (1, 4)
    assert requests[0][0] == "POST"
    assert requests[0][1]["text"] == "Hello"
    profile = backend.runtime_compute_profile(HostCaps("cuda", ("cuda", "cpu")))
    assert profile["effective_device"] == "cuda"
    assert profile["routing_status"] == "accelerated"


@pytest.mark.parametrize("name", ["VoxCPM2Backend", "MossTTSNanoBackend", "CosyVoiceBackend", "SherpaOnnxBackend"])
def test_other_tts_loaders_do_not_claim_parent_fallback(failed_gpu, name):
    from core.device_caps import HostCaps
    from services import tts_backend

    profile = getattr(tts_backend, name).runtime_compute_profile(HostCaps("cuda", ("cuda", "cpu")))
    assert profile["routing_status"] == "accelerated"


@pytest.mark.parametrize("name", ["NeMoASRBackend", "FunASRBackend"])
def test_upstream_asr_loaders_do_not_claim_parent_fallback(failed_gpu, name):
    from core.device_caps import HostCaps
    from services import asr_backend
    from services.engine_routing import runtime_compute_profile

    profile = runtime_compute_profile(getattr(asr_backend, name), HostCaps("cuda", ("cuda", "cpu")))
    assert profile["routing_status"] == "accelerated"


@pytest.mark.parametrize("force", [False, True])
def test_whisperx_profile_keeps_actual_failed_test_fallback(monkeypatch, failed_gpu, force):
    from core.device_caps import HostCaps
    from services import asr_backend
    from services.engine_routing import runtime_compute_profile

    if force:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    monkeypatch.setattr(asr_backend, "_ctranslate2_cuda_ok", lambda: True)
    assert asr_backend.WhisperXBackend._pick_device() == ("cpu", "int8")
    profile = runtime_compute_profile(asr_backend.WhisperXBackend, HostCaps("cuda", ("cuda", "cpu")))
    assert profile["effective_device"] == "cpu"
    assert profile["routing_status"] == "cpu_fallback"


@pytest.mark.parametrize("name", ["omnivoice", "pytorch-whisper"])
@pytest.mark.parametrize("force", [False, True])
def test_explicit_parent_loader_policies_match_device_selection(monkeypatch, failed_gpu, name, force):
    from core import device_caps
    from services import asr_backend, model_manager
    from services.engine_routing import runtime_compute_profile
    from services.tts_backend import OmniVoiceBackend

    caps = device_caps.HostCaps("cuda", ("cuda", "cpu"))
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setattr(device_caps, "arch_unsupported", lambda _: None)
    monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(
        cuda=SimpleNamespace(is_available=lambda: True), version=SimpleNamespace(hip=None),
    ))
    monkeypatch.setenv("OMNIVOICE_ASR_VRAM_PREFLIGHT", "0")
    if force:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    cls = OmniVoiceBackend if name == "omnivoice" else asr_backend.PyTorchWhisperBackend
    device = model_manager.get_best_device() if name == "omnivoice" else cls._pick_device()
    profile = runtime_compute_profile(cls, caps)
    assert profile["effective_device"] == device == ("cuda" if force else "cpu")
    assert profile["routing_status"] == ("accelerated" if force else "cpu_fallback")


def test_legacy_kernel_guidance_survives_failed_self_test(monkeypatch, tmp_path, failed_gpu):
    from api.routers.setup import wizard
    from core.device_caps import HostCaps, KERNEL_RISK_MARKER
    from services import media_tools
    from services.engine_routing import resolve_routing

    caps = HostCaps("cuda", ("cuda", "cpu"), notes=(f"sm_52 — {KERNEL_RISK_MARKER}",))
    profile = {"engine": "omnivoice", **resolve_routing(("cuda", "cpu"), caps, self_test_policy="forceable")}
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 32)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _: 100)
    monkeypatch.setattr(wizard, "hf_cache_dir", lambda: str(tmp_path / "hf"))
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "status": "pass", "detail": "Local test", "fix": None,
    })
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": "nvidia", "device_name": "Legacy test GPU", "driver": "600.0",
        "backend": "cuda", "available": True, "notes": [], "kernels_missing": "sm_52",
    })
    monkeypatch.setattr(media_tools, "summary", lambda **_: {"ready": True})
    monkeypatch.setattr("services.tts_backend.gpu_routing_verdict", lambda: profile)
    rows = {row["id"]: row for row in wizard.preflight()["checks"]}
    for key in ("gpu", "gpu_routing"):
        assert rows[key]["status"] == "warn"
        assert "HIPBLAS_STATUS_ALLOC_FAILED" in rows[key]["detail"]
        assert "driver update will not help" in rows[key]["fix"].lower()
        assert "Vulkan" in rows[key]["fix"]


@pytest.mark.parametrize("verdict", [None, "passed", "inconclusive", "failed"])
@pytest.mark.parametrize("force", [False, True])
def test_whisperx_rechecks_completed_verdict_before_loading_and_alignment(monkeypatch, verdict, force):
    import asyncio
    import sys

    from core import gpu_self_test
    from core.windows_rocm import pipelines
    from services import asr_backend

    selected = {"asr": [], "alignment": []}
    result = {"segments": [{"start": 0.0, "end": 1.0, "text": "Owned fixture"}], "language": "en"}

    def load_asr(*args, **kwargs):
        selected["asr"].append((kwargs["device"], kwargs["compute_type"]))
        return SimpleNamespace(transcribe=lambda *args, **kwargs: result)

    def load_alignment(**kwargs):
        selected["alignment"].append(kwargs["device"])
        return object(), {}

    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    if force:
        monkeypatch.setenv("OMNIVOICE_FORCE_CUDA", "1")
    monkeypatch.setattr(gpu_self_test, "_thread", None)
    monkeypatch.setattr(gpu_self_test, "_verdict", None)
    monkeypatch.setattr(asr_backend, "_ctranslate2_cuda_ok", lambda: True)
    monkeypatch.setattr(asr_backend, "_ctranslate2_execstack_ok", lambda: (True, ""))
    monkeypatch.setattr(asr_backend, "_harden_speechbrain_lazy_imports", lambda: None)
    monkeypatch.setattr(asr_backend, "_local_model_source", lambda _: "owned-installed-model")
    monkeypatch.setattr(asr_backend, "_decode_audio_16k_mono", lambda _: [0.0] * 16)
    monkeypatch.setattr(asr_backend, "_ALIGN_CACHE", {})
    monkeypatch.setattr(pipelines, "whisperx_error", lambda *args: None)
    monkeypatch.setattr(pipelines, "pipeline_error", lambda *args: None)
    monkeypatch.setattr(pipelines, "prepare_audio_imports", lambda: None)
    monkeypatch.setattr(asr_backend.WhisperXBackend, "_degrade_for_vram", lambda self, d, c: (d, c))
    monkeypatch.setattr(asr_backend.WhisperXBackend, "_allow_vad_pickle_globals", lambda self: None)
    monkeypatch.setitem(sys.modules, "whisperx", SimpleNamespace(
        load_model=load_asr, load_align_model=load_alignment,
        align=lambda *args, **kwargs: result,
    ))

    async def construct_while_verdict_unknown():
        return asr_backend.WhisperXBackend()

    backend = asyncio.run(construct_while_verdict_unknown())
    assert (backend._device, backend._compute_type) == ("cuda", "float16")
    if verdict is not None:
        monkeypatch.setattr(gpu_self_test, "_verdict", gpu_self_test.Verdict(verdict, "Owned late verdict"))
    assert backend.transcribe("owned-audio.wav")["segments"]
    expected = "cpu" if verdict == "failed" else "cuda"
    assert selected == {
        "asr": [(expected, "int8" if expected == "cpu" else "float16")],
        "alignment": [expected],
    }

