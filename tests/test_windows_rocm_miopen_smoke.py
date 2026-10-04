"""Model-free MIOpen control-flow tests; mocks are not GPU execution evidence."""

from contextlib import nullcontext
import ctypes
import json
import os
import signal
import subprocess
import sys
import time
from types import SimpleNamespace
from unittest.mock import MagicMock, Mock

import pytest

from test_windows_rocm_smoke import FakeDevice, runtime, smoke


@pytest.fixture
def miopen(runtime, monkeypatch):
    runtime.args.miopen = True
    monkeypatch.delenv("MIOPEN_DISABLE_CACHE", raising=False)
    tensor = MagicMock()
    tensor.device = FakeDevice()
    tensor.shape = (1, 293, 256)
    tensor.transpose.return_value = tensor
    normalizer = Mock(return_value=tensor)
    normalizer.to.return_value = normalizer
    recurrent = Mock(return_value=(tensor, (tensor, tensor)))
    recurrent.to.return_value = recurrent
    recurrent.eval.return_value = recurrent
    recurrent.parameters.return_value = [tensor]
    trace = SimpleNamespace(function_events=[SimpleNamespace(name="aten::miopen_rnn")])
    finite = Mock()
    finite.all.return_value.item.return_value = True
    runtime.torch.backends = SimpleNamespace(cudnn=SimpleNamespace(enabled=True))
    runtime.torch.nn = SimpleNamespace(InstanceNorm1d=Mock(return_value=normalizer), LSTM=Mock(return_value=recurrent))
    runtime.torch.ones = Mock(return_value=tensor)
    runtime.torch.isfinite = Mock(return_value=finite)
    runtime.torch.autograd = SimpleNamespace(profiler=SimpleNamespace(profile=lambda **kwargs: nullcontext(trace)))
    return SimpleNamespace(runtime=runtime, tensor=tensor, normalizer=normalizer, recurrent=recurrent,
                           trace=trace, finite=finite)


def test_miopen_is_explicit_opt_in_and_not_an_asr_claim(runtime):
    assert not runtime.args.miopen
    report = smoke._run_smoke(runtime.args)
    assert report["miopen"]["status"] == "not_requested"
    assert report["ok"] is True


def test_miopen_checks_native_dispatch_gpu_outputs_and_cold_cache(miopen):
    report = smoke._run_smoke(miopen.runtime.args)
    assert report["ok"] is True
    assert report["miopen"]["status"] == "passed"
    assert report["miopen"]["native_rnn_dispatch"] is True
    assert report["asr"]["status"] == "not_requested"
    assert smoke.os.environ["MIOPEN_DISABLE_CACHE"] == "1"
    miopen.recurrent.to.assert_called_once_with("cuda:0")
    miopen.normalizer.to.assert_called_once_with("cuda:0")
    miopen.runtime.torch.nn.LSTM.assert_called_once_with(
        60, 128, num_layers=4, dropout=0.5, batch_first=True, bidirectional=True,
    )
    assert report["miopen"]["output_shape"] == [1, 293, 256]


@pytest.mark.parametrize("problem,code", [
    ("disabled", "miopen_disabled"), ("cpu", "miopen_tensor_not_gpu"),
    ("wrong_gpu", "miopen_tensor_not_gpu"), ("nan", "miopen_nonfinite"),
    ("not_native", "miopen_not_executed"), ("kernel_error", "runtime_error"),
])
def test_miopen_cannot_pass_on_fallback_or_failed_execution(miopen, problem, code):
    if problem == "disabled":
        miopen.runtime.torch.backends.cudnn.enabled = False
    elif problem == "cpu":
        miopen.tensor.device = FakeDevice("cpu")
    elif problem == "wrong_gpu":
        miopen.tensor.device = FakeDevice(index=1)
    elif problem == "nan":
        miopen.finite.all.return_value.item.return_value = False
    elif problem == "not_native":
        miopen.trace.function_events = [SimpleNamespace(name="aten::lstm")]
    else:
        miopen.recurrent.side_effect = RuntimeError("private SDK path")
    report = smoke._run_smoke(miopen.runtime.args)
    assert report["ok"] is False
    assert report["stage"] == "miopen"
    assert report["error"]["code"] == code
    assert "private" not in str(report)
    assert report["miopen"]["status"] != "passed"


@pytest.fixture
def worker(monkeypatch):
    process = Mock(pid=12345, returncode=0)
    process.poll.return_value = 0
    process.communicate.return_value = ("report", "diagnostic")
    job = Mock()
    constructor = Mock(return_value=job)
    launcher = Mock(return_value=process)
    monkeypatch.setattr(smoke, "_WindowsJob", constructor, raising=False)
    monkeypatch.setattr(smoke.subprocess, "Popen", launcher)
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=AssertionError("No taskkill fallback")))
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform="win32"))
    return SimpleNamespace(process=process, job=job, constructor=constructor, launcher=launcher)


def test_worker_starts_suspended_and_assigns_before_communication(worker):
    events = []
    worker.launcher.side_effect = lambda *args, **kwargs: events.append("spawn") or worker.process
    worker.job.assign_and_resume.side_effect = lambda pid: events.append("assign_resume")
    worker.process.communicate.side_effect = lambda **kwargs: (events.append("communicate") or ("report", "diagnostic"))
    worker.job.close.side_effect = lambda: events.append("close_job")
    result = smoke._run_worker(["python", "probe"], timeout=1, env={"SAFE": "1"})
    assert events == ["spawn", "assign_resume", "communicate", "close_job", "communicate"]
    assert worker.launcher.call_args.kwargs["creationflags"] == 0x08000004
    assert worker.launcher.call_args.kwargs["env"] == {"SAFE": "1"}
    assert worker.launcher.call_args.kwargs["stdout"] == subprocess.PIPE
    assert worker.launcher.call_args.kwargs["stderr"] == subprocess.PIPE
    assert worker.launcher.call_args.kwargs["close_fds"] is True
    worker.job.assign_and_resume.assert_called_once_with(12345)
    worker.process.kill.assert_not_called()
    assert (result.returncode, result.stdout, result.stderr) == (0, "report", "diagnostic")


@pytest.mark.parametrize("root_exited", [False, True])
@pytest.mark.parametrize("failure", [subprocess.TimeoutExpired("probe", 1), KeyboardInterrupt(), OSError("private error")])
def test_worker_always_closes_job_before_reaping_even_after_root_exit(worker, root_exited, failure):
    events = []
    worker.process.poll.return_value = 0 if root_exited else None
    worker.process.communicate.side_effect = [failure, ("partial", "private")]
    worker.job.close.side_effect = lambda: events.append("close_job")
    worker.process.kill.side_effect = lambda: events.append("kill_root")
    with pytest.raises(type(failure)):
        smoke._run_worker(["python", "probe"], timeout=1, env={})
    assert events[0] == "close_job"
    worker.job.close.assert_called_once_with()
    assert worker.process.communicate.call_args_list[-1].kwargs == {"timeout": 15}
    assert worker.process.communicate.call_count == 2


@pytest.mark.parametrize("failure", [OSError("private isolation error"), KeyboardInterrupt()])
def test_job_assignment_failure_kills_suspended_worker_without_execution(worker, failure):
    worker.job.assign_and_resume.side_effect = failure
    worker.process.poll.return_value = None
    with pytest.raises(type(failure)):
        smoke._run_worker(["python", "probe"], timeout=1, env={})
    worker.job.close.assert_called_once_with()
    worker.process.kill.assert_called_once_with()
    worker.process.communicate.assert_called_once_with(timeout=15)


def test_job_creation_failure_never_spawns(worker):
    worker.constructor.side_effect = OSError("private job error")
    with pytest.raises(OSError):
        smoke._run_worker(["python", "probe"], timeout=1, env={})
    worker.launcher.assert_not_called()


def test_spawn_failure_closes_empty_job(worker):
    worker.launcher.side_effect = OSError("private spawn error")
    with pytest.raises(OSError):
        smoke._run_worker(["python", "probe"], timeout=1, env={})
    worker.job.close.assert_called_once_with()
    worker.job.assign_and_resume.assert_not_called()


def test_non_windows_mocked_worker_does_not_require_windows_apis(worker, monkeypatch):
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform="linux"))
    smoke._run_worker(["python", "probe"], timeout=1, env={})
    worker.constructor.assert_not_called()
    assert worker.launcher.call_args.kwargs["creationflags"] == 0


@pytest.mark.parametrize("phase", ["creation", "assignment", "cleanup"])
def test_ctrl_c_during_handle_acquisition_or_cleanup_is_deferred(worker, phase):
    previous = signal.getsignal(signal.SIGINT)

    def interrupt(*args, **kwargs):
        signal.raise_signal(signal.SIGINT)
        signal.raise_signal(signal.SIGINT)
        return worker.process

    if phase == "creation":
        worker.launcher.side_effect = interrupt
    elif phase == "assignment":
        worker.job.assign_and_resume.side_effect = interrupt
    else:
        worker.job.close.side_effect = interrupt
    with pytest.raises(KeyboardInterrupt):
        smoke._run_worker(["python", "probe"], timeout=1, env={})
    worker.job.close.assert_called_once_with()
    assert worker.process.communicate.call_args.kwargs == {"timeout": 15}
    assert signal.getsignal(signal.SIGINT) == previous


@pytest.fixture
def windows_api(monkeypatch):
    api = Mock()
    api.CreateJobObjectW.return_value = 101
    api.SetInformationJobObject.return_value = 1
    api.CloseHandle.return_value = 1
    api.OpenProcess.return_value = 102
    api.AssignProcessToJobObject.return_value = 1
    api.CreateToolhelp32Snapshot.return_value = 103
    api.OpenThread.return_value = 104
    api.GetProcessIdOfThread.return_value = 12345
    api.ResumeThread.return_value = 1
    api.last_error.return_value = 18
    api.Thread32Next.return_value = 0

    def first(snapshot, pointer):
        entry = ctypes.cast(pointer, ctypes.POINTER(smoke._ThreadEntry)).contents
        entry.th32OwnerProcessID = 12345
        entry.th32ThreadID = 67890
        return 1

    api.Thread32First.side_effect = first
    monkeypatch.setattr(smoke, "_windows_api", lambda: api)
    return api


def test_job_configures_noninheritable_kill_on_close_and_resumes_owned_thread(windows_api):
    flags = []

    def set_limits(handle, information_class, pointer, size):
        limits = ctypes.cast(pointer, ctypes.POINTER(smoke._JobExtendedLimits)).contents
        flags.append(limits.BasicLimitInformation.LimitFlags)
        assert information_class == 9
        assert size == ctypes.sizeof(smoke._JobExtendedLimits)
        return 1

    windows_api.SetInformationJobObject.side_effect = set_limits
    job = smoke._WindowsJob()
    job.assign_and_resume(12345)
    job.close()
    job.close()
    windows_api.CreateJobObjectW.assert_called_once_with(None, None)
    assert flags == [0x00002000]
    windows_api.OpenProcess.assert_called_once_with(0x0101, False, 12345)
    windows_api.AssignProcessToJobObject.assert_called_once_with(101, 102)
    windows_api.OpenThread.assert_called_once_with(0x0802, False, 67890)
    windows_api.ResumeThread.assert_called_once_with(104)
    assert [call.args[0] for call in windows_api.CloseHandle.call_args_list] == [102, 103, 104, 101]
    names = [call[0] for call in windows_api.mock_calls]
    assert names.index("AssignProcessToJobObject") < names.index("ResumeThread")


@pytest.mark.parametrize("operation", ["CreateJobObjectW", "SetInformationJobObject"])
def test_job_creation_or_configuration_failure_is_closed_and_private(windows_api, operation):
    getattr(windows_api, operation).return_value = 0
    with pytest.raises(smoke.SmokeError) as failure:
        smoke._WindowsJob()
    assert failure.value.code == "worker_isolation_failed"
    if operation == "SetInformationJobObject":
        windows_api.CloseHandle.assert_called_once_with(101)
    else:
        windows_api.CloseHandle.assert_not_called()
    windows_api.ResumeThread.assert_not_called()


@pytest.mark.parametrize("operation", [
    "OpenProcess", "AssignProcessToJobObject", "CreateToolhelp32Snapshot",
    "Thread32First", "Thread32Next", "OpenThread", "GetProcessIdOfThread", "ResumeThread",
])
def test_job_setup_failures_never_resume_unowned_workers(windows_api, operation):
    if operation == "CreateToolhelp32Snapshot":
        windows_api.CreateToolhelp32Snapshot.return_value = ctypes.c_void_p(-1).value
    elif operation in ("Thread32First", "Thread32Next"):
        getattr(windows_api, operation).side_effect = None
        getattr(windows_api, operation).return_value = 0
        windows_api.last_error.return_value = 5
    elif operation == "ResumeThread":
        windows_api.ResumeThread.return_value = 0xFFFFFFFF
    else:
        getattr(windows_api, operation).return_value = 0
    job = smoke._WindowsJob()
    try:
        with pytest.raises(smoke.SmokeError) as failure:
            job.assign_and_resume(12345)
        assert failure.value.code == "worker_isolation_failed"
        if operation != "ResumeThread":
            windows_api.ResumeThread.assert_not_called()
    finally:
        job.close()
    assert windows_api.CloseHandle.call_args.args == (101,)


@pytest.mark.parametrize("threads", [[], [(123, 999)], [(12345, 1), (12345, 2)]])
def test_missing_or_ambiguous_primary_thread_fails_closed(windows_api, threads):
    remaining = iter(threads)

    def advance(snapshot, pointer):
        try:
            owner, identifier = next(remaining)
        except StopIteration:
            return 0
        entry = ctypes.cast(pointer, ctypes.POINTER(smoke._ThreadEntry)).contents
        entry.th32OwnerProcessID = owner
        entry.th32ThreadID = identifier
        return 1

    windows_api.Thread32First.side_effect = advance
    windows_api.Thread32Next.side_effect = advance
    job = smoke._WindowsJob()
    try:
        with pytest.raises(smoke.SmokeError):
            job.assign_and_resume(12345)
        windows_api.ResumeThread.assert_not_called()
    finally:
        job.close()


def test_isolation_failure_is_sanitized_in_public_report(runtime, monkeypatch):
    runtime.args.timeout = 1
    monkeypatch.setattr(smoke, "_run_worker", Mock(side_effect=smoke.SmokeError(
        "worker_isolation_failed", "Could not safely establish the Windows worker job.",
    )))
    report = smoke._run_isolated(runtime.args, [])
    assert report["ok"] is False
    assert report["error"]["code"] == "worker_isolation_failed"
    assert "private" not in json.dumps(report)


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows job-object integration; no GPU")
def test_real_windows_worker_success():
    result = smoke._run_worker(
        [sys.executable, "-B", "-S", "-c", "import sys; print('ready'); print('diagnostic', file=sys.stderr)"],
        timeout=10, env=dict(os.environ),
    )
    assert result.returncode == 0
    assert result.stdout.strip() == "ready"
    assert result.stderr.strip() == "diagnostic"


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows job-object integration; no GPU")
def test_real_windows_assignment_failure_never_executes_child(tmp_path, monkeypatch):
    marker = tmp_path / "must-not-execute"
    monkeypatch.setattr(smoke._WindowsJob, "assign_and_resume", Mock(side_effect=smoke.SmokeError(
        "worker_isolation_failed", "Injected assignment failure.",
    )))
    with pytest.raises(smoke.SmokeError):
        smoke._run_worker(
            [sys.executable, "-B", "-S", "-c", f"from pathlib import Path; Path({str(marker)!r}).touch()"],
            timeout=10, env=dict(os.environ),
        )
    assert not marker.exists()


@pytest.mark.skipif(sys.platform != "win32", reason="Real Windows job-object integration; no GPU")
@pytest.mark.parametrize("scenario", ["timeout", "root_exit", "success_with_descendant", "interrupt"])
def test_real_windows_worker_owns_descendants_after_root_exit(tmp_path, monkeypatch, scenario):
    api = smoke._windows_api()
    api.WaitForSingleObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.WaitForSingleObject.restype = ctypes.c_uint32
    api.TerminateProcess.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    api.TerminateProcess.restype = ctypes.c_int32
    started = tmp_path / "started.json"
    proceed = tmp_path / "proceed"
    detached_output = scenario == "success_with_descendant"
    code = "\n".join([
        "import json, os, pathlib, subprocess, sys, time",
        f"started = pathlib.Path({str(started)!r})",
        f"proceed = pathlib.Path({str(proceed)!r})",
        f"redirect = dict(stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL) if {detached_output!r} else {{}}",
        "child = subprocess.Popen([sys.executable, '-B', '-S', '-c', 'import time; time.sleep(30)'], **redirect)",
        "started.write_text(json.dumps([os.getpid(), child.pid]), encoding='utf-8')",
        "deadline = time.monotonic() + 15",
        "while not proceed.exists() and time.monotonic() < deadline: time.sleep(0.01)",
        "print('ready', flush=True)",
        "time.sleep(30)" if scenario in ("timeout", "interrupt") else "",
    ])
    original_popen = subprocess.Popen
    handles = []
    processes = []
    jobs = []
    original_job = smoke._WindowsJob

    def create_job():
        job = original_job()
        jobs.append(job)
        return job

    def launch(*args, **kwargs):
        process = original_popen(*args, **kwargs)
        processes.append(process)
        original_communicate = process.communicate
        first = True

        def communicate(**options):
            nonlocal first
            if first:
                first = False
                deadline = time.monotonic() + 10
                while not started.exists() and time.monotonic() < deadline:
                    time.sleep(0.01)
                assert started.exists(), "Owned worker did not start"
                while True:
                    try:
                        identifiers = json.loads(started.read_text(encoding="utf-8"))
                        break
                    except json.JSONDecodeError:
                        assert time.monotonic() < deadline
                        time.sleep(0.01)
                for identifier in identifiers:
                    handle = api.OpenProcess(0x00101001, False, identifier)
                    assert handle, "Cannot retain owned test-process handle"
                    handles.append(handle)
                proceed.touch()
                if scenario == "root_exit":
                    assert process.wait(timeout=5) == 0
                if scenario == "interrupt":
                    raise KeyboardInterrupt
            return original_communicate(**options)

        process.communicate = communicate
        return process

    monkeypatch.setattr(smoke, "_WindowsJob", create_job)
    monkeypatch.setattr(smoke.subprocess, "Popen", launch)
    try:
        if scenario in ("timeout", "root_exit", "interrupt"):
            expected = KeyboardInterrupt if scenario == "interrupt" else subprocess.TimeoutExpired
            with pytest.raises(expected):
                smoke._run_worker([sys.executable, "-B", "-S", "-c", code], timeout=0.25, env=dict(os.environ))
        else:
            result = smoke._run_worker([sys.executable, "-B", "-S", "-c", code], timeout=10, env=dict(os.environ))
            assert result.returncode == 0
            assert result.stdout.strip() == "ready"
        assert len(handles) == 2
        assert all(api.WaitForSingleObject(handle, 5000) == 0 for handle in handles)
    finally:
        for job in jobs:
            job.close()
        for handle in handles:
            if api.WaitForSingleObject(handle, 0) == 258:
                api.TerminateProcess(handle, 1)
                api.WaitForSingleObject(handle, 5000)
            api.CloseHandle(handle)
        for process in processes:
            if process.poll() is None:
                process.kill()
            process.communicate(timeout=5)
