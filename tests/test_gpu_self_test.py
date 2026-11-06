"""A GPU can pass every check and still fail its first real work (#3804).

`torch.cuda.is_available()` was True on gfx1034 while the first matmul died with
"invalid device function". Only running work finds that, and a separate process
means a native crash costs the test rather than the app.
"""
from __future__ import annotations

import json
import subprocess
import sys
import types

import pytest

from core import gpu_self_test


def _torch(version="2.8.0+cu128", cuda="12.8", hip=None):
    return types.SimpleNamespace(
        __version__=version,
        version=types.SimpleNamespace(cuda=cuda, hip=hip),
    )


def _child(stdout="", returncode=0, stderr=""):
    return lambda *a, **k: subprocess.CompletedProcess([], returncode, stdout, stderr)


@pytest.fixture(autouse=True)
def _isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(gpu_self_test, "RESULT_PATH", str(tmp_path / "gpu_self_test.json"))
    monkeypatch.setattr(gpu_self_test, "_thread", None)
    monkeypatch.setattr(gpu_self_test, "_verdict", None)
    monkeypatch.delenv("OMNIVOICE_DEVICE", raising=False)
    monkeypatch.delenv("OMNIVOICE_FORCE_CUDA", raising=False)
    monkeypatch.setattr("sys.platform", "linux")


STARTED = gpu_self_test.STARTED_MARK + "\n"


def test_a_gpu_that_runs_the_work_passes(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _child(STARTED + gpu_self_test.PASSED_MARK))
    assert gpu_self_test.evaluate(_torch()).status == "passed"


def test_the_reported_hip_error_fails_with_its_message(monkeypatch):
    output = STARTED + gpu_self_test.FAILED_MARK + "RuntimeError: HIP error: invalid device function"
    monkeypatch.setattr(subprocess, "run", _child(output, returncode=1))
    verdict = gpu_self_test.evaluate(_torch(hip="6.4", cuda=None))
    assert verdict.status == "failed"
    assert "invalid device function" in verdict.detail


def test_a_native_crash_fails_without_taking_the_app_down(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _child(STARTED, returncode=3221225477))
    verdict = gpu_self_test.evaluate(_torch())
    assert verdict.status == "failed"
    assert "3221225477" in verdict.detail


def test_no_gpu_is_not_a_failure(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _child(STARTED + gpu_self_test.NO_GPU_MARK))
    assert gpu_self_test.evaluate(_torch()).status == "no_gpu"


@pytest.mark.parametrize(
    "child",
    [
        # Torch never loaded here though it loaded in the app: says nothing about the GPU.
        _child("", returncode=1),
        # Another program holds the GPU: true today, false after it closes.
        _child(STARTED + gpu_self_test.FAILED_MARK + "RuntimeError: CUDA out of memory", returncode=1),
    ],
)
def test_a_result_that_says_nothing_about_the_gpu_is_inconclusive(monkeypatch, child):
    monkeypatch.setattr(subprocess, "run", child)
    assert gpu_self_test.evaluate(_torch()).status == "inconclusive"


def test_a_slow_machine_is_inconclusive(monkeypatch):
    def slow(*a, **k):
        raise subprocess.TimeoutExpired("python", 1)

    monkeypatch.setattr(subprocess, "run", slow)
    assert gpu_self_test.evaluate(_torch()).status == "inconclusive"


@pytest.mark.parametrize(
    "torch_build, platform",
    [
        (_torch("2.8.0+cpu", cuda=None), "linux"),
        (_torch("2.8.0", cuda=None), "darwin"),
        # Its Python needs a DLL search path set first, and has its own smoke test.
        (_torch("2.8.0", cuda=None, hip="7.2"), "win32"),
    ],
)
def test_builds_with_nothing_to_test_start_no_process(monkeypatch, torch_build, platform):
    monkeypatch.setattr("sys.platform", platform)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no process expected"))
    assert gpu_self_test.evaluate(torch_build).status == "skipped"


@pytest.mark.parametrize(
    "name, value", [("OMNIVOICE_DEVICE", "cpu"), ("OMNIVOICE_FORCE_CUDA", "1")]
)
def test_an_explicit_user_choice_skips_the_test(monkeypatch, name, value):
    monkeypatch.setenv(name, value)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("no process expected"))
    assert gpu_self_test.evaluate(_torch()).status == "skipped"


def test_a_pass_is_remembered_until_the_build_changes(monkeypatch):
    runs = []

    def child(*a, **k):
        runs.append(1)
        return subprocess.CompletedProcess([], 0, STARTED + gpu_self_test.PASSED_MARK, "")

    monkeypatch.setattr(subprocess, "run", child)
    gpu_self_test.evaluate(_torch())
    gpu_self_test.evaluate(_torch())
    assert len(runs) == 1
    gpu_self_test.evaluate(_torch("2.9.0+cu128"))
    assert len(runs) == 2


def test_a_failure_is_retried_so_a_driver_update_clears_it(monkeypatch):
    monkeypatch.setattr(subprocess, "run", _child(STARTED, returncode=1))
    assert gpu_self_test.evaluate(_torch()).status == "failed"
    monkeypatch.setattr(subprocess, "run", _child(STARTED + gpu_self_test.PASSED_MARK))
    assert gpu_self_test.evaluate(_torch()).status == "passed"


def test_an_unreadable_result_file_just_tests_again(monkeypatch, tmp_path):
    (tmp_path / "gpu_self_test.json").write_text("{nope", encoding="utf-8")
    monkeypatch.setattr(subprocess, "run", _child(STARTED + gpu_self_test.PASSED_MARK))
    assert gpu_self_test.evaluate(_torch()).status == "passed"
    assert json.loads((tmp_path / "gpu_self_test.json").read_text())["status"] == "passed"


def test_the_real_script_gives_a_real_answer(monkeypatch):
    """No mock: the script is a string, and the process options differ per platform."""
    torch = pytest.importorskip("torch")
    if not torch.version.cuda and not getattr(torch.version, "hip", None):
        monkeypatch.setattr(gpu_self_test, "_nothing_to_test", lambda _torch: False)
    assert gpu_self_test.evaluate(torch).status in {"passed", "no_gpu"}


def test_failure_warning_names_the_cause_and_the_way_out(monkeypatch):
    monkeypatch.setattr(
        gpu_self_test,
        "_verdict",
        gpu_self_test.Verdict("failed", "RuntimeError: HIP error: invalid device function"),
    )
    warning = gpu_self_test.failure_warning()
    assert "invalid device function" in warning
    assert "CPU" in warning
    assert "OMNIVOICE_FORCE_CUDA=1" in warning


def test_no_warning_unless_it_failed(monkeypatch):
    monkeypatch.setattr(gpu_self_test, "_verdict", gpu_self_test.Verdict("passed"))
    assert gpu_self_test.failure_warning() is None


def test_the_answer_waits_for_a_test_still_running(monkeypatch):
    import threading

    release = threading.Event()

    def child(*a, **k):
        release.wait(5)
        return subprocess.CompletedProcess(
            [], 1, STARTED + gpu_self_test.FAILED_MARK + "RuntimeError: boom", ""
        )

    monkeypatch.setattr(subprocess, "run", child)
    gpu_self_test.start(_torch())
    threading.Timer(0.05, release.set).start()
    assert "boom" in gpu_self_test.failure_warning()


def _running_test(monkeypatch):
    """A test that stays running until the returned event is set."""
    import threading

    release = threading.Event()
    thread = threading.Thread(target=release.wait, args=(10,), daemon=True)
    thread.start()
    monkeypatch.setattr(gpu_self_test, "_thread", thread)
    return release


def test_a_request_on_the_event_loop_does_not_wait_for_the_test(monkeypatch):
    import asyncio
    import time

    release = _running_test(monkeypatch)
    try:
        async def ask():
            started = time.monotonic()
            answer = gpu_self_test.failure_warning()
            return answer, time.monotonic() - started

        answer, waited = asyncio.run(ask())
    finally:
        release.set()
    assert answer is None
    assert waited < 1


def test_the_notice_does_not_wait_or_hide_a_later_failure(monkeypatch):
    from api.routers import system

    monkeypatch.setattr(system, "_GPU_ARCH_WARNING", [])
    release = _running_test(monkeypatch)
    try:
        assert system._gpu_arch_warning_cached() is None
        assert system._GPU_ARCH_WARNING == []
    finally:
        release.set()


@pytest.mark.parametrize(
    "name, value",
    [("CUDA_VISIBLE_DEVICES", "1"), ("HSA_OVERRIDE_GFX_VERSION", "10.3.0")],
)
def test_a_pass_does_not_outlive_a_change_of_gpu_settings(monkeypatch, name, value):
    monkeypatch.delenv(name, raising=False)
    before = gpu_self_test._build_key(_torch())
    monkeypatch.setenv(name, value)
    assert gpu_self_test._build_key(_torch()) != before


def test_a_pass_does_not_outlive_a_change_of_card(monkeypatch):
    monkeypatch.setattr(gpu_self_test, "_gpu_identity", lambda: "gfx1030")
    before = gpu_self_test._build_key(_torch())
    monkeypatch.setattr(gpu_self_test, "_gpu_identity", lambda: "gfx1100")
    assert gpu_self_test._build_key(_torch()) != before


def test_a_failed_self_test_routes_the_loader_to_cpu(monkeypatch):
    import services.model_manager as mm

    monkeypatch.setattr(
        gpu_self_test,
        "_verdict",
        gpu_self_test.Verdict("failed", "RuntimeError: HIP error: invalid device function"),
    )
    torch = types.SimpleNamespace(
        cuda=types.SimpleNamespace(is_available=lambda: True),
    )
    monkeypatch.setattr(mm, "_lazy_torch", lambda: torch)
    monkeypatch.setattr("core.device_caps.arch_unsupported", lambda _t: None)

    compatible, warning = mm.check_device_compatibility()

    assert compatible is False
    assert "invalid device function" in warning


def test_a_failed_self_test_keeps_whisperx_off_the_gpu(monkeypatch):
    from services import asr_backend

    monkeypatch.setattr(asr_backend, "_ctranslate2_cuda_ok", lambda: True)
    assert asr_backend.WhisperXBackend._pick_device() == ("cuda", "float16")

    monkeypatch.setattr(gpu_self_test, "_verdict", gpu_self_test.Verdict("failed", "boom"))
    assert asr_backend.WhisperXBackend._pick_device() == ("cpu", "int8")


def test_startup_starts_the_test_before_the_gpu_is_used():
    import ast
    from pathlib import Path

    source = (Path(__file__).parent.parent / "backend" / "main.py").read_text(encoding="utf-8")
    startup = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef) and node.name == "_phase_a_build_inner"
    )
    nodes = list(ast.walk(startup))
    calls = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_gpu_self_test"
    ]
    imports = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Import) and any(a.name == "torchaudio" for a in node.names)
    ]
    assert calls and imports and min(calls) < min(imports)
    # The child must inherit the HSA override, or it tests the wrong kernels.
    overrides = [
        node.lineno
        for node in nodes
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "apply_rocm_override_before_init"
    ]
    assert overrides and min(overrides) < min(calls)
