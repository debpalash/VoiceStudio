"""Control-flow tests with fake native runtimes, NOT evidence of GPU execution."""

from contextlib import nullcontext
import importlib.util
import json
from pathlib import Path
import subprocess
import struct
import sys
from types import SimpleNamespace
from unittest.mock import Mock, MagicMock
import wave

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "smoke_windows_rocm.py"
SPEC = importlib.util.spec_from_file_location("windows_rocm_smoke", SCRIPT)
smoke = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(smoke)


class FakeDevice:
    def __init__(self, device_type="cuda", index=0):
        self.type = device_type
        self.index = index

    def __str__(self):
        return f"{self.type}:{self.index}"


@pytest.fixture
def runtime(monkeypatch, tmp_path):
    """Block every native import, including on a developer's actual GPU host."""
    modules = {}

    def import_module(name):
        assert all(smoke.os.environ[key] == value for key, value in smoke.OFFLINE_ENV.items())
        if name not in modules:
            raise ModuleNotFoundError("private dependency path must not be reported")
        return modules[name]

    loader = Mock(side_effect=import_module)
    find_spec = Mock(return_value=None)
    monkeypatch.setattr(smoke, "importlib", SimpleNamespace(import_module=loader, util=SimpleNamespace(find_spec=find_spec)))
    monkeypatch.setattr(smoke, "_version", lambda name: {"torch": "2.9.1+rocm7.2.1", "ctranslate2": "4.8.2",
                                                       "faster-whisper": "1.2.1"}.get(name))
    monkeypatch.setattr(smoke, "sys", SimpleNamespace(platform="win32", executable=sys.executable))
    for key in smoke.OFFLINE_ENV:
        monkeypatch.setenv(key, "0")
    matrix = MagicMock()
    matrix.device = FakeDevice()
    result = MagicMock()
    result.device = FakeDevice()
    result.sum.return_value.item.return_value = 8192.0
    comparison = MagicMock()
    comparison.all.return_value.item.return_value = True
    result.__eq__.return_value = comparison
    matrix.__matmul__.return_value = result
    torch = SimpleNamespace(
        __version__="2.9.1+rocm7.2.1", __file__=str(tmp_path / "torch" / "__init__.py"),
        version=SimpleNamespace(hip="7.2", cuda=None), float32="float32",
        inference_mode=nullcontext, full=Mock(return_value=matrix),
        cuda=SimpleNamespace(is_available=Mock(return_value=True), device_count=Mock(return_value=1),
                             synchronize=Mock(), get_device_properties=Mock(return_value=SimpleNamespace(
                                 name="Mock Radeon (not hardware evidence)", gcnArchName="gfx1201", total_memory=16384))),
    )
    modules["torch"] = torch
    return SimpleNamespace(args=smoke._parser().parse_args([]), modules=modules, loader=loader,
                           find_spec=find_spec, torch=torch, matrix=matrix, result=result, comparison=comparison)


@pytest.fixture
def asr(runtime, monkeypatch, tmp_path):
    """Synthetic file headers and fake model objects, never real model weights."""
    model_dir = tmp_path / "private-model"
    model_dir.mkdir()
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        (model_dir / filename).write_bytes(b"{}")
    audio_path = tmp_path / "private-recording.wav"
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 16000)
    runtime.args.model_dir = model_dir
    runtime.args.wav = audio_path
    package_dir = tmp_path / "ctranslate2"
    package_dir.mkdir()
    library = package_dir / "ctranslate2.dll"
    library.write_bytes(b"fake fixture: hipblas.dll\x00amdhip64_7.dll\x00")
    runtime.find_spec.return_value = SimpleNamespace(submodule_search_locations=[str(package_dir)])
    handles = []

    def add_directory(directory):
        handle = MagicMock()
        handles.append(handle)
        return handle

    add_dll = Mock(side_effect=add_directory)
    monkeypatch.setattr(smoke.os, "add_dll_directory", add_dll, raising=False)
    native = SimpleNamespace(device="cuda", device_index=[0], compute_type="float16")
    model = SimpleNamespace(model=native, transcribe=Mock())
    consumed = []

    def segments():
        assert handles and all(not handle.__exit__.called for handle in handles)
        consumed.append(True)
        yield SimpleNamespace(text="private spoken content", start=0.0, end=1.0)

    model.transcribe.return_value = (segments(), SimpleNamespace(duration=1.0))
    constructor = Mock(return_value=model)
    ct2 = SimpleNamespace(__version__="4.8.2", get_cuda_device_count=Mock(return_value=1),
                          get_supported_compute_types=Mock(return_value={"float16", "float32"}))
    runtime.modules.update(ctranslate2=ct2, faster_whisper=SimpleNamespace(WhisperModel=constructor))
    return SimpleNamespace(runtime=runtime, ct2=ct2, constructor=constructor, model=model, native=native,
                           library=library, consumed=consumed, handles=handles, add_dll=add_dll)


def assert_failure(report, code):
    assert report["ok"] is False
    assert report["error"]["code"] == code
    encoded = json.dumps(report, allow_nan=False)
    assert "private" not in encoded
    return report


def test_help_needs_no_native_packages_or_gpu(runtime, capsys):
    with pytest.raises(SystemExit) as stopped:
        smoke.main(["--help"])
    assert stopped.value.code == 0
    assert "tokenizer.json" in capsys.readouterr().out
    runtime.loader.assert_not_called()


@pytest.mark.parametrize("platform_name", ["linux", "darwin"])
def test_not_native_windows(runtime, platform_name):
    smoke.sys.platform = platform_name
    assert_failure(smoke._run_smoke(runtime.args), "windows_required")
    runtime.loader.assert_not_called()


@pytest.mark.parametrize("hip", [None, ""])
def test_cuda_alias_is_not_proof_of_hip(runtime, hip):
    runtime.torch.version.hip = hip
    runtime.torch.version.cuda = "12.8"
    assert_failure(smoke._run_smoke(runtime.args), "torch_not_hip")
    runtime.torch.cuda.is_available.assert_not_called()
    runtime.torch.full.assert_not_called()


@pytest.mark.parametrize("available,count,index,code", [
    (False, 0, 0, "torch_no_gpu"), (True, 0, 0, "torch_device_missing"),
    (True, 1, 1, "torch_device_missing"), (True, 1, -1, "invalid_device"),
])
def test_gpu_absence_or_invalid_index_never_falls_back(runtime, available, count, index, code):
    runtime.torch.cuda.is_available.return_value = available
    runtime.torch.cuda.device_count.return_value = count
    runtime.args.device_index = index
    assert_failure(smoke._run_smoke(runtime.args), code)
    runtime.torch.full.assert_not_called()


def test_mocked_torch_only_is_explicitly_not_an_asr_pass(runtime):
    report = smoke._run_smoke(runtime.args)
    assert report["ok"] is True
    assert report["scope"] == "torch_only"
    assert report["asr"]["status"] == "not_requested"
    assert report["ctranslate2"]["status"] == "not_tested"
    assert report["torch"]["device"]["architecture"] == "gfx1201"
    assert report["torch"]["tensor"]["checksum"] == 8192.0
    runtime.torch.full.assert_called_once_with((32, 32), 0.5, dtype="float32", device="cuda:0")
    assert runtime.torch.cuda.synchronize.call_count == 3
    assert all(call.args == (0,) for call in runtime.torch.cuda.synchronize.call_args_list)
    assert [call.args[0] for call in runtime.loader.call_args_list] == ["torch"]


@pytest.mark.parametrize("tensor_name,device_type,index", [("matrix", "cpu", 0), ("result", "cpu", 0), ("result", "cuda", 1)])
def test_tensor_device_must_remain_selected_gpu(runtime, tensor_name, device_type, index):
    getattr(runtime, tensor_name).device = FakeDevice(device_type, index)
    assert_failure(smoke._run_smoke(runtime.args), "tensor_not_gpu")


@pytest.mark.parametrize("checksum,matches", [(0.0, True), (float("nan"), True), (float("inf"), True), (8192.0, False)])
def test_tensor_result_is_checked_not_just_imported(runtime, checksum, matches):
    runtime.result.sum.return_value.item.return_value = checksum
    runtime.comparison.all.return_value.item.return_value = matches
    assert_failure(smoke._run_smoke(runtime.args), "tensor_mismatch")


@pytest.mark.parametrize("operation", ["allocate", "synchronize", "import"])
def test_torch_runtime_failures_are_private_and_terminal(runtime, operation):
    if operation == "import":
        runtime.modules.clear()
    else:
        target = runtime.torch.full if operation == "allocate" else runtime.torch.cuda.synchronize
        target.side_effect = RuntimeError("private GPU diagnostics")
    report = assert_failure(smoke._run_smoke(runtime.args), "runtime_error")
    assert report["stage"] == "torch"
    assert runtime.torch.full.call_count <= 1


@pytest.mark.parametrize("field", ["model_dir", "wav"])
def test_asr_requires_both_explicit_inputs(runtime, field, tmp_path):
    setattr(runtime.args, field, tmp_path / "private-input")
    assert_failure(smoke._run_smoke(runtime.args), "paired_inputs")
    runtime.loader.assert_not_called()


@pytest.mark.parametrize("filename", ["model.bin", "config.json", "tokenizer.json"])
def test_incomplete_local_model_cannot_trigger_hub_tokenizer_download(asr, filename):
    (asr.runtime.args.model_dir / filename).unlink()
    assert_failure(smoke._run_smoke(asr.runtime.args), "incomplete_model")
    asr.runtime.loader.assert_not_called()


def test_model_id_is_not_accepted_in_place_of_local_directory(asr):
    asr.runtime.args.model_dir = asr.runtime.args.model_dir / "Systran" / "faster-whisper-tiny"
    assert_failure(smoke._run_smoke(asr.runtime.args), "missing_model")
    asr.runtime.loader.assert_not_called()


@pytest.mark.parametrize("kind,code", [("missing", "missing_wav"), ("corrupt", "runtime_error"), ("truncated", "truncated_wav")])
def test_invalid_local_audio_fails_before_native_imports(asr, kind, code):
    path = asr.runtime.args.wav
    if kind == "missing":
        path.unlink()
    elif kind == "corrupt":
        path.write_bytes(b"private non-WAV contents")
    else:
        path.write_bytes(path.read_bytes()[:-2])
    assert_failure(smoke._run_smoke(asr.runtime.args), code)
    asr.runtime.loader.assert_not_called()


@pytest.mark.parametrize("markers", [b"cublas64_12.dll\x00", b"hipblas.dll\x00", b"amdhip64_7.dll\x00"])
def test_rejects_non_hip_ct2_before_native_load(asr, markers):
    asr.library.write_bytes(markers)
    assert_failure(smoke._run_smoke(asr.runtime.args), "ct2_not_hip")
    assert [call.args[0] for call in asr.runtime.loader.call_args_list] == ["torch"]
    asr.constructor.assert_not_called()


def test_missing_ct2_package_is_explicit_failure(asr):
    asr.runtime.find_spec.return_value = None
    assert_failure(smoke._run_smoke(asr.runtime.args), "ct2_missing")


@pytest.mark.parametrize("count,types,code", [(0, {"float16"}, "ct2_no_gpu"), (1, {"float32"}, "ct2_compute_type")])
def test_ct2_gpu_and_compute_support_are_required(asr, count, types, code):
    asr.ct2.get_cuda_device_count.return_value = count
    asr.ct2.get_supported_compute_types.return_value = types
    assert_failure(smoke._run_smoke(asr.runtime.args), code)
    asr.constructor.assert_not_called()


@pytest.mark.parametrize("error_type", [RuntimeError, MemoryError, TypeError])
def test_model_oom_or_api_mismatch_never_retries_on_cpu(asr, error_type):
    asr.constructor.side_effect = error_type("private transcript or dependency path")
    report = assert_failure(smoke._run_smoke(asr.runtime.args), "runtime_error")
    assert report["error"]["exception_type"] == error_type.__name__
    assert asr.constructor.call_count == 1
    assert asr.constructor.call_args.kwargs["device"] == "cuda"
    asr.model.transcribe.assert_not_called()


@pytest.mark.parametrize("field,value", [("device", "cpu"), ("device_index", [1]), ("compute_type", "float32")])
def test_loaded_model_must_confirm_gpu_index_and_compute_type(asr, field, value):
    setattr(asr.native, field, value)
    assert_failure(smoke._run_smoke(asr.runtime.args), "asr_not_gpu")
    asr.model.transcribe.assert_not_called()


def test_lazy_inference_failure_is_not_a_successful_model_load(asr):
    def segments():
        yield SimpleNamespace(text="private partial result", start=0.0, end=0.5)
        raise RuntimeError("private native failure")

    asr.model.transcribe.return_value = (segments(), SimpleNamespace(duration=1.0))
    assert_failure(smoke._run_smoke(asr.runtime.args), "runtime_error")
    assert asr.constructor.call_count == 1
    assert asr.model.transcribe.call_count == 1


@pytest.mark.parametrize("segments,duration,code", [
    ([], 1.0, "asr_empty"),
    ([SimpleNamespace(text="   ", start=0.0, end=1.0)], 1.0, "asr_empty"),
    ([SimpleNamespace(text="private", start=0.0, end=1.0)], float("nan"), "asr_invalid_result"),
    ([SimpleNamespace(text="private", start=1.0, end=0.0)], 1.0, "asr_invalid_result"),
])
def test_asr_requires_nonempty_finite_real_results(asr, segments, duration, code):
    asr.model.transcribe.return_value = (iter(segments), SimpleNamespace(duration=duration))
    assert_failure(smoke._run_smoke(asr.runtime.args), code)


def test_mocked_asr_consumes_generator_and_keeps_dll_handles_without_backend(asr, tmp_path):
    extra = tmp_path / "sdk-bin"
    extra.mkdir()
    asr.runtime.args.dll_dir = [extra, extra]
    report = smoke._run_smoke(asr.runtime.args)
    assert report["ok"] is True
    assert report["scope"] == "torch_and_asr"
    assert asr.consumed == [True]
    assert report["asr"]["segment_count"] == 1
    assert report["asr"]["audio_seconds"] == 1.0
    assert report["asr"]["faster_whisper_version"] == "1.2.1"
    assert report["ctranslate2"]["device"] == "cuda"
    assert report["ctranslate2"]["compute_type"] == "float16"
    assert report["ctranslate2"]["status"] == "passed"
    asr.constructor.assert_called_once_with(str(asr.runtime.args.model_dir.resolve()), device="cuda",
                                           device_index=0, compute_type="float16", local_files_only=True, num_workers=1)
    asr.model.transcribe.assert_called_once_with(str(asr.runtime.args.wav.resolve()), beam_size=1,
                                                vad_filter=False, word_timestamps=False, condition_on_previous_text=False)
    asr.ct2.get_supported_compute_types.assert_called_once_with("cuda", 0)
    assert asr.add_dll.call_count == 2
    assert all(handle.__exit__.call_count == 1 for handle in asr.handles)
    assert [call.args[0] for call in asr.runtime.loader.call_args_list] == ["torch", "ctranslate2", "faster_whisper"]
    encoded = json.dumps(report, allow_nan=False)
    assert "private" not in encoded
    assert str(tmp_path) not in encoded


def test_explicit_second_gpu_and_float32_are_used_without_substitution(asr):
    asr.runtime.args.device_index = 1
    asr.runtime.args.compute_type = "float32"
    asr.runtime.torch.cuda.device_count.return_value = 2
    asr.runtime.matrix.device.index = 1
    asr.runtime.result.device.index = 1
    asr.ct2.get_cuda_device_count.return_value = 2
    asr.native.device_index = [1]
    asr.native.compute_type = "float32"
    report = smoke._run_smoke(asr.runtime.args)
    assert report["ok"] is True
    assert report["torch"]["tensor"]["device"] == "cuda:1"
    assert report["ctranslate2"]["device_index"] == [1]
    assert asr.constructor.call_args.kwargs["compute_type"] == "float32"
    assert asr.constructor.call_args.kwargs["device_index"] == 1
    asr.ct2.get_supported_compute_types.assert_called_once_with("cuda", 1)
    assert all(call.args == (1,) for call in asr.runtime.torch.cuda.synchronize.call_args_list)


def test_explicit_dll_directory_is_active_before_torch_load(asr, tmp_path):
    asr.runtime.args.model_dir = None
    asr.runtime.args.wav = None
    asr.runtime.args.dll_dir = [tmp_path]
    original_loader = asr.runtime.loader.side_effect

    def load_with_dll_directory(name):
        assert asr.handles and not asr.handles[0].__exit__.called
        return original_loader(name)

    asr.runtime.loader.side_effect = load_with_dll_directory
    assert smoke._run_smoke(asr.runtime.args)["ok"] is True
    asr.add_dll.assert_called_once_with(str(tmp_path.resolve()))
    assert asr.handles[0].__exit__.call_count == 1


@pytest.mark.parametrize("seconds", [0, 61])
def test_empty_or_overlong_wav_is_rejected_before_imports(asr, seconds):
    with wave.open(str(asr.runtime.args.wav), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * (16000 * seconds))
    assert_failure(smoke._run_smoke(asr.runtime.args), "invalid_wav")
    asr.runtime.loader.assert_not_called()


@pytest.mark.parametrize("rate,payload,code", [
    (1_000_000_000, 4_000_000_000, "invalid_wav"),
    (16000, 160000, "truncated_wav"),
])
def test_declared_wav_payload_is_rejected_before_reading(asr, monkeypatch, rate, payload, code):
    path = asr.runtime.args.wav
    contents = bytearray(path.read_bytes())
    struct.pack_into("<I", contents, 4, payload + 36)
    struct.pack_into("<I", contents, 24, rate)
    struct.pack_into("<I", contents, 28, rate * 2)
    struct.pack_into("<I", contents, 40, payload)
    path.write_bytes(contents)
    monkeypatch.setattr(wave.Wave_read, "readframes", lambda *args: pytest.fail("Invalid declared payload reached sample reading"))
    assert_failure(smoke._run_smoke(asr.runtime.args), code)
    asr.runtime.loader.assert_not_called()


def test_wav_payload_is_validated_in_bounded_chunks(asr, monkeypatch):
    with wave.open(str(asr.runtime.args.wav), "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * (16000 * 40))
    original = wave.Wave_read.readframes
    read_sizes = []

    def bounded_read(audio, frame_count):
        requested_bytes = frame_count * audio.getsampwidth() * audio.getnchannels()
        assert requested_bytes <= 1024 * 1024
        read_sizes.append(requested_bytes)
        return original(audio, frame_count)

    monkeypatch.setattr(wave.Wave_read, "readframes", bounded_read)
    smoke._local_inputs(asr.runtime.args)
    assert len(read_sizes) > 1
    assert sum(read_sizes) == 16000 * 40 * 2


def test_large_wav_file_is_rejected_before_open(asr, monkeypatch):
    import os

    original_stat = Path.stat

    def oversized_stat(path, *args, **kwargs):
        result = original_stat(path, *args, **kwargs)
        if path == asr.runtime.args.wav:
            values = list(result)
            values[6] = 128 * 1024 * 1024 + 1
            return os.stat_result(values)
        return result

    monkeypatch.setattr(Path, "stat", oversized_stat)
    monkeypatch.setattr(wave, "open", lambda *args: pytest.fail("Oversized WAV opened"))
    assert_failure(smoke._run_smoke(asr.runtime.args), "invalid_wav")
    asr.runtime.loader.assert_not_called()


@pytest.mark.parametrize("ok", [True, False])
def test_cli_stdout_is_json_only_and_native_output_is_not_disclosed(runtime, monkeypatch, capsys, ok):
    child = smoke._report(runtime.args)
    child["ok"] = ok
    runner = Mock(return_value=SimpleNamespace(returncode=0 if ok else 1,
                  stdout="private native output\n" + smoke.REPORT_PREFIX + json.dumps(child) + "\n",
                  stderr="private DLL path"))
    monkeypatch.setattr(smoke.subprocess, "run", runner)
    assert smoke.main([]) == (0 if ok else 1)
    captured = capsys.readouterr()
    assert json.loads(captured.out) == child
    assert captured.err == ""
    assert "private" not in captured.out
    command = runner.call_args.args[0]
    assert command[:2] == [sys.executable, "-B"]
    assert "--_worker" in command
    assert runner.call_args.kwargs["capture_output"] is True
    assert runner.call_args.kwargs["timeout"] == 180
    assert all(runner.call_args.kwargs["env"][key] == value for key, value in smoke.OFFLINE_ENV.items())


@pytest.mark.parametrize("returncode,output", [(-1073740791, "private crash"), (0, "private native chatter"),
                                                    (1, smoke.REPORT_PREFIX + '{"schema_version":1,"ok":true}'),
                                                    (0, smoke.REPORT_PREFIX + "not-json")])
def test_crash_missing_or_contradictory_worker_report_cannot_pass(runtime, monkeypatch, returncode, output):
    monkeypatch.setattr(smoke.subprocess, "run", Mock(return_value=SimpleNamespace(
        returncode=returncode, stdout=output, stderr="private diagnostics")))
    assert_failure(smoke._run_isolated(runtime.args, []), "worker_failed")


@pytest.mark.parametrize("error,code", [(subprocess.TimeoutExpired("private command", 1, output="private output"), "worker_timeout"),
                                      (OSError("private executable path"), "worker_failed")])
def test_worker_timeout_or_spawn_failure_is_private(runtime, monkeypatch, error, code):
    monkeypatch.setattr(smoke.subprocess, "run", Mock(side_effect=error))
    assert_failure(smoke._run_isolated(runtime.args, []), code)


def test_invalid_timeout_does_not_spawn(runtime, monkeypatch):
    runner = Mock()
    monkeypatch.setattr(smoke.subprocess, "run", runner)
    runtime.args.timeout = 0
    assert_failure(smoke._run_isolated(runtime.args, []), "invalid_timeout")
    runner.assert_not_called()
