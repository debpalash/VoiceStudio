"""#1371 — a missing cuDNN 8 must not kill the backend process.

CTranslate2 4.4.0 (the engine under WhisperX and faster-whisper) requires
cuDNN 8 while torch ships cuDNN 9. When the side-loaded cuDNN 8 is absent,
CTranslate2 does not raise — it prints ``Could not locate
cudnn_ops_infer64_8.dll`` and ``__fastfail``s, taking the whole backend down
with ``0xC0000409`` (exit code ``-1073740791``). There is no traceback to
classify and no exception to fall back from, so the shell restarts the backend
and the next transcribe kills it again.

The backend already *knew*: ``main.py`` probed for the libraries and threw the
answer away. These tests pin the fix — the answer is kept, and the two
CTranslate2 engines report themselves unavailable so ``_auto_detect`` routes to
pytorch-whisper (torch's own cuDNN 9 stack) instead of walking into the abort.

The conservative half matters as much as the strict half: a false positive here
costs a user WhisperX's forced alignment, and therefore lip-sync accuracy. So
the "not gated" cases below (no CUDA, ROCm, macOS) are not padding — a fix that
disqualified CTranslate2 on those hosts would be a worse regression than the
crash it set out to fix.
"""

import importlib
import importlib.util
import os
import sys
from importlib import metadata

import pytest

_CUDA_PRESENT = (True, "CUDA device present")


@pytest.fixture
def cudnn8():
    """Resolved per test, never bound at collection time.

    Other suites re-import ``core.cudnn8`` (they stub ``builtins.__import__``
    to exercise import fallbacks), which rebinds ``sys.modules`` to a fresh
    module object. A module-level ``from core import cudnn8`` would then have
    monkeypatches land on a stale copy that nothing under test consults — the
    tests pass alone and fail in the full suite.
    """
    mod = importlib.import_module("core.cudnn8")
    mod.reset_cache_for_tests()
    yield mod
    mod.reset_cache_for_tests()


@pytest.fixture
def ab():
    """Ditto for the module under test."""
    return importlib.import_module("services.asr_backend")


@pytest.fixture
def cudnn8_missing(monkeypatch, cudnn8):
    """A CUDA host whose cuDNN 8 will not load — the #1371 machine."""
    monkeypatch.setattr(cudnn8, "_preloaded", True)  # nothing to preload
    monkeypatch.setattr(cudnn8, "_torch_wants_cudnn8", lambda: _CUDA_PRESENT)

    def _no_lib(name):
        raise OSError(f"Could not locate {name}")

    monkeypatch.setattr(cudnn8, "_try_load", _no_lib)


@pytest.fixture
def ctranslate2_importable(monkeypatch, ab):
    """Make ``import whisperx`` / ``import faster_whisper`` succeed regardless of
    what is installed, so these tests exercise the cuDNN gate rather than the
    host's package set. Its metadata models a non-HIP CUDA installation."""
    installed_version = metadata.version

    def cuda_version(package):
        if package == "torch":
            return "2.8.0+cu128"
        return installed_version(package)

    monkeypatch.setattr(metadata, "version", cuda_version)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    for name in ("whisperx", "faster_whisper"):
        module = type(sys)(name)
        module.__spec__ = importlib.util.spec_from_loader(name, loader=None)
        monkeypatch.setitem(sys.modules, name, module)


# ── the probe ──────────────────────────────────────────────────────────────


def test_missing_cudnn8_on_a_cuda_host_is_reported_not_swallowed(cudnn8, cudnn8_missing):
    ok, reason = cudnn8.ctranslate2_cudnn_status()
    assert ok is False
    # The message is the only thing standing between the user and a silent
    # restart loop, so it must name the library and a way out.
    assert "cudnn_ops_infer" in reason
    assert "pytorch-whisper" in reason


def test_the_probe_is_cached(monkeypatch, cudnn8, cudnn8_missing):
    calls = []
    real = cudnn8._compute_status
    monkeypatch.setattr(cudnn8, "_compute_status", lambda: (calls.append(1), real())[1])
    cudnn8.ctranslate2_cudnn_status()
    cudnn8.ctranslate2_cudnn_status()
    assert len(calls) == 1


@pytest.mark.parametrize(
    "verdict",
    [
        (False, "no CUDA device — CTranslate2 runs on CPU"),
        (False, "ROCm build — CTranslate2 has no ROCm backend, runs on CPU"),
        (False, "torch unavailable (ImportError)"),
    ],
)
def test_hosts_that_never_need_cudnn8_are_not_disqualified(monkeypatch, cudnn8, verdict):
    """cuDNN is a CUDA library. On a CPU box, a ROCm box, or one where torch is
    missing entirely, CTranslate2 never loads it — gating there would downgrade
    working installs for no reason. ROCm is the sharp edge:
    ``torch.cuda.is_available()`` is True on a HIP build."""
    monkeypatch.setattr(cudnn8, "_preloaded", True)
    monkeypatch.setattr(cudnn8, "_torch_wants_cudnn8", lambda: verdict)

    def _boom(*a, **kw):
        raise AssertionError("probed for cuDNN 8 on a host that does not need it")

    monkeypatch.setattr(cudnn8, "_try_load", _boom)
    ok, reason = cudnn8.ctranslate2_cudnn_status()
    assert ok is True
    assert "not required" in reason


def test_rocm_is_detected_from_torch_version_hip(monkeypatch, cudnn8):
    """The HIP branch reads ``torch.version.hip`` *before* ``cuda.is_available()``
    — which is True on ROCm — so pin the ordering, not just the outcome."""
    torch = type(sys)("torch")
    torch.version = type(sys)("version")
    torch.version.hip = "6.2.0"
    torch.cuda = type(sys)("cuda")
    torch.cuda.is_available = lambda: True
    monkeypatch.setitem(sys.modules, "torch", torch)
    needed, why = cudnn8._torch_wants_cudnn8()
    assert needed is False
    assert "ROCm" in why


# ── the engines route around it ────────────────────────────────────────────


@pytest.mark.parametrize("engine", ["WhisperXBackend", "FasterWhisperBackend"])
def test_ctranslate2_engines_report_unavailable(engine, ab, cudnn8_missing, ctranslate2_importable):
    """Before the fix both returned ``(True, "ready")`` here — the import
    succeeds, because CTranslate2 only reaches for cuDNN when it builds a CUDA
    model. That is precisely why the failure landed as a process kill."""
    ok, reason = getattr(ab, engine).is_available()
    assert ok is False
    assert "cudnn_ops_infer" in reason


def test_auto_detect_falls_back_to_pytorch_whisper(
    monkeypatch, ab, cudnn8_missing, ctranslate2_importable
):
    monkeypatch.setattr(
        ab.MLXWhisperBackend, "is_available", classmethod(lambda cls: (False, "n/a"))
    )
    assert ab._auto_detect() == "pytorch-whisper"


def test_the_isolated_sidecar_engine_reports_the_same_reason(cudnn8_missing, ctranslate2_importable):
    """Crash isolation makes the missing library a failed job instead of a dead
    backend — which is worse than it sounds: it fails on every transcribe, with
    nothing explaining why. Model Catalogue gets the reason."""
    from services import subprocess_asr

    ok, reason = subprocess_asr.IsolatedFasterWhisperBackend.is_available()
    assert ok is False
    assert "cudnn_ops_infer" in reason


def test_a_broken_probe_never_blocks_asr(monkeypatch, ab, cudnn8, ctranslate2_importable):
    """The gate is a safety net, not a new single point of failure."""
    def _explode():
        raise RuntimeError("probe itself is broken")

    monkeypatch.setattr(cudnn8, "ctranslate2_cudnn_status", _explode)
    assert ab._ctranslate2_cudnn_ok() == (True, "ready")
    assert ab.WhisperXBackend.is_available()[0] is True


# ── the preload reaches the venv that is actually running ──────────────────


def test_compat_dirs_covers_the_running_interpreter(cudnn8):
    """The old inline preload hardcoded ``<project root>/.venv``, so a
    differently named venv, a Docker image or a system Python resolved to a
    directory that does not exist — the preload silently did nothing and the
    process died anyway."""
    dirs = cudnn8.compat_dirs()
    assert any(d.startswith(sys.prefix) for d in dirs), dirs
    assert all("cudnn8_compat" in d for d in dirs)


def test_the_asr_sidecar_preloads_cudnn8(monkeypatch, ab, cudnn8):
    """The sidecar is a *child process* with a clean import path, so the
    parent's preload does not reach it. An install whose side-load made the
    in-process engine work still had the isolated engine fail every transcribe.
    """
    called = []
    monkeypatch.setattr(cudnn8, "preload", lambda: called.append(1))
    path = (
        pytest.importorskip("pathlib").Path(ab.__file__).resolve().parents[1]
        / "engines" / "_asr_sidecar" / "main.py"
    )
    spec = importlib.util.spec_from_file_location("_sidecar_under_test", path)
    module = importlib.util.module_from_spec(spec)
    saved = list(sys.path)
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path[:] = saved
    assert called, "the sidecar did not preload cuDNN 8 on startup"


def test_the_windows_dll_directory_handle_is_retained(monkeypatch, tmp_path, cudnn8):
    """`os.add_dll_directory` returns a context-manager cookie whose close (or
    garbage collection) UN-registers the directory again.

    Discarding it therefore makes the call a silent no-op — reintroducing, one
    line lower, the exact failure this module exists to prevent: CTranslate2
    does a bare-name LoadLibrary, misses, and aborts the process
    (CodeRabbit, #1401).
    """
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(cudnn8, "compat_dirs", lambda: [str(tmp_path)])
    monkeypatch.setattr(cudnn8, "_dll_dir_cookies", [])

    class Cookie:
        closed = False

        def close(self):
            self.closed = True

    cookie = Cookie()
    monkeypatch.setattr(os, "add_dll_directory", lambda d: cookie, raising=False)

    cudnn8.preload()

    assert cookie in cudnn8._dll_dir_cookies, (
        "the add_dll_directory cookie was dropped, so the directory is "
        "un-registered as soon as it is collected and the call does nothing"
    )
    assert cookie.closed is False
