"""Opt-in native Windows ROCm smoke; no installs, downloads or CPU fallback.

Run with the Python environment being evaluated, not via uv sync. Python 3.12
x64 and a supported AMD Windows driver are prerequisites for this recipe:
  torch==2.9.1+rocm7.2.1, rocm[libraries]==7.2.1
  https://repo.radeon.com/rocm/windows/rocm-rel-7.2.1/
Optional ASR: faster-whisper==1.2.1 and the CTranslate2 4.8.2 HIP wheel from
  https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip
  archive SHA256: 43da4baa5feaee49f77e176277a9647f99c493173c81a0bc60f491cac97532c2
  member: temp-windows/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl
  wheel SHA256: d5b9b0d34b23584638fc087bf99a85b0ba9917b84860d5fbddcc73b0aedd4fd4
Provision these separately; an ordinary PyPI CTranslate2 wheel is NOT HIP.
These pins describe the candidate recipe, not a guarantee that it passes.
Installed versions are reported, not silently replaced or assumed compatible.

Examples (PowerShell; stdout is the content-free JSON report):
  & <python.exe> scripts/smoke_windows_rocm.py
  & <python.exe> scripts/smoke_windows_rocm.py --model-dir <local-model> --wav <speech.wav>

Without BOTH ASR inputs, success proves only a synchronized torch kernel, not
speech inference. Audio decoding and feature preparation still use the CPU;
the required GPU check applies to model inference, not those preprocessing steps.
Supply a complete converted faster-whisper model directory
(including tokenizer.json) and real speech in a PCM WAV, at most 60 seconds,
128 MiB, 192 kHz and 32-bit samples. Header validation reads bounded chunks.
No transcript, filenames, machine/user identifiers or raw exceptions are
reported. Speech quality must be evaluated separately by the operator.

This script has no backend dependencies or local CT2 compatibility wrappers.
It retains DLL-directory handles for the wheel's ROCm SDK directories; use
--dll-dir for an explicitly installed SDK's bin directory if needed. NVIDIA
core.cudnn8 preloading is intentionally NOT used for HIP. An incompatible
faster-whisper/CT2 API fails instead of being patched or retried on CPU.
Native output is captured in a bounded-lifetime child and never forwarded;
a native crash/timeout fails the smoke without promising a partial report.
"""

from __future__ import annotations

import argparse
from contextlib import ExitStack
import importlib
import importlib.metadata
import importlib.util
import json
import math
import mmap
import os
from pathlib import Path
import platform
import subprocess
import sys
import time
import wave


REPORT_PREFIX = "WINDOWS_ROCM_SMOKE="
MAX_WAV_BYTES = 128 * 1024 * 1024
WAV_READ_BYTES = 1024 * 1024
OFFLINE_ENV = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_HUB_DISABLE_TELEMETRY": "1",
    "DO_NOT_TRACK": "1",
}


class SmokeError(Exception):
    """A content-free error safe to include in the report."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _require(condition: bool, code: str, message: str) -> None:
    if not condition:
        raise SmokeError(code, message)


def _version(distribution: str) -> str | None:
    try:
        return importlib.metadata.version(distribution)
    except importlib.metadata.PackageNotFoundError:
        return None


def _report(args: argparse.Namespace) -> dict:
    """Initialize a content-free report for torch-only or explicitly requested ASR."""
    return {
        "schema_version": 1,
        "ok": False,
        "platform": sys.platform,
        "python": platform.python_version(),
        "scope": "torch_and_asr" if args.model_dir or args.wav else "torch_only",
        "stage": "preflight",
        "torch": {"version": _version("torch")},
        "ctranslate2": {"version": _version("ctranslate2"), "status": "not_tested"},
        "asr": {"status": "not_tested" if args.model_dir or args.wav else "not_requested"},
    }


def _local_inputs(args: argparse.Namespace) -> None:
    """Validate local inputs and bounded PCM payloads before loading native runtimes.

    Reject WAV metadata declaring more data than the file contains or the
    128-MiB limit allows, then check the payload using at most 1-MiB reads.
    """
    _require(sys.platform == "win32", "windows_required", "Native Windows is required; WSL is not this smoke.")
    _require(args.device_index >= 0, "invalid_device", "Device index must be nonnegative.")
    _require(args.timeout > 0, "invalid_timeout", "Timeout must be positive.")
    _require(bool(args.model_dir) == bool(args.wav), "paired_inputs", "Supply both --model-dir and --wav, or neither.")
    for directory in args.dll_dir:
        _require(directory.is_dir(), "missing_dll_dir", "An explicit DLL directory does not exist.")
    if args.model_dir is None:
        return
    _require(args.model_dir.is_dir(), "missing_model", "ASR needs an existing local model directory, not a Hub ID.")
    for filename in ("model.bin", "config.json", "tokenizer.json"):
        source = args.model_dir / filename
        _require(source.is_file() and source.stat().st_size > 0, "incomplete_model",
                 "Local model needs nonempty model.bin, config.json and tokenizer.json; nothing will be downloaded.")
    _require(args.wav.is_file(), "missing_wav", "ASR needs an existing local PCM WAV containing speech.")
    file_size = args.wav.stat().st_size
    _require(file_size <= MAX_WAV_BYTES, "invalid_wav", "The WAV file exceeds the 128 MiB smoke limit.")
    with wave.open(str(args.wav.resolve()), "rb") as audio:
        frame_count = audio.getnframes()
        sample_rate = audio.getframerate()
        sample_width = audio.getsampwidth()
        frame_bytes = sample_width * audio.getnchannels()
        _require(audio.getcomptype() == "NONE" and audio.getnchannels() in (1, 2)
                 and sample_width in (1, 2, 3, 4) and 0 < sample_rate <= 192000
                 and 0 < frame_count / sample_rate <= 60, "invalid_wav",
                 "Supply mono/stereo PCM speech up to 60 seconds, 192 kHz and 32-bit samples.")
        payload_bytes = frame_count * frame_bytes
        _require(payload_bytes <= file_size, "truncated_wav", "The declared WAV data exceeds the file size.")
        _require(payload_bytes <= MAX_WAV_BYTES, "invalid_wav", "The WAV payload exceeds the 128 MiB smoke limit.")
        remaining = frame_count
        while remaining:
            requested = min(remaining, WAV_READ_BYTES // frame_bytes)
            _require(len(audio.readframes(requested)) == requested * frame_bytes,
                 "truncated_wav", "The WAV data is truncated.")
            remaining -= requested


def _torch_smoke(args: argparse.Namespace, report: dict):
    """Verify synchronized HIP matmul on the requested GPU and update the report.

    Reject CPU or wrong-device tensors and incorrect outputs; return the
    imported torch module for the optional ASR check.
    """
    torch = importlib.import_module("torch")
    details = report["torch"]
    details.update(version=str(torch.__version__), hip=getattr(torch.version, "hip", None),
                   cuda=getattr(torch.version, "cuda", None))
    _require(bool(details["hip"]), "torch_not_hip", "PyTorch has no HIP runtime; install a Windows ROCm build explicitly.")
    _require(torch.cuda.is_available(), "torch_no_gpu", "HIP PyTorch cannot see a GPU; CPU fallback is forbidden.")
    count = torch.cuda.device_count()
    details["device_count"] = count
    _require(args.device_index < count, "torch_device_missing", "The requested HIP GPU index is unavailable.")
    properties = torch.cuda.get_device_properties(args.device_index)
    details["device"] = {
        "index": args.device_index,
        "name": properties.name,
        "architecture": getattr(properties, "gcnArchName", None),
        "total_memory_bytes": properties.total_memory,
    }
    device = f"cuda:{args.device_index}"
    torch.cuda.synchronize(args.device_index)
    started = time.perf_counter()
    with torch.inference_mode():
        matrix = torch.full((32, 32), 0.5, dtype=torch.float32, device=device)
        result = matrix @ matrix
        _require(all(tensor.device.type == "cuda" and tensor.device.index == args.device_index
                     for tensor in (matrix, result)), "tensor_not_gpu", "A tensor is not on the requested HIP GPU.")
        torch.cuda.synchronize(args.device_index)
        checksum = float(result.sum().item())
        matches = bool((result == 8.0).all().item())
    torch.cuda.synchronize(args.device_index)
    _require(math.isfinite(checksum) and checksum == 8192.0 and matches,
             "tensor_mismatch", "The synchronized GPU matrix result is incorrect or nonfinite.")
    details["tensor"] = {
        "device": str(result.device), "shape": [32, 32], "dtype": "float32",
        "operation": "matmul", "checksum": checksum, "expected_checksum": 8192.0,
        "all_elements_match": matches, "synchronized": True,
        "elapsed_seconds": round(time.perf_counter() - started, 6),
    }
    return torch


def _ct2_directory() -> Path:
    """Locate CT2 without importing it and require its HIP DLL dependency markers."""
    spec = importlib.util.find_spec("ctranslate2")
    _require(spec is not None and bool(spec.submodule_search_locations), "ct2_missing",
             "Install the Windows HIP CTranslate2 wheel explicitly before requesting ASR.")
    directory = Path(next(iter(spec.submodule_search_locations)))
    library = directory / "ctranslate2.dll"
    _require(library.is_file(), "ct2_dll_missing", "CTranslate2 has no native Windows DLL.")
    with library.open("rb") as source, mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as binary:
        _require(all(binary.find(marker) >= 0 for marker in (b"hipblas.dll\x00", b"amdhip64_7.dll\x00")),
                 "ct2_not_hip", "CTranslate2 lacks the pinned HIP DLL markers; ordinary CUDA wheels are not valid.")
    return directory


def _asr_smoke(args: argparse.Namespace, report: dict, torch) -> None:
    """Run local-only ASR on the exact requested GPU and compute type.

    Keep SDK DLL handles alive while consuming all segments. Record counts
    and timing, not transcript text; reject CPU fallback and invalid results.
    """
    directory = _ct2_directory()
    with ExitStack() as stack:
        directories = [directory, directory.parent / "_rocm_sdk_core" / "bin",
                       directory.parent / "_rocm_sdk_libraries_custom" / "bin",
                       Path(torch.__file__).parent / "lib"]
        for dll_dir in dict.fromkeys(path.resolve() for path in directories):
            if dll_dir.is_dir():
                stack.enter_context(os.add_dll_directory(str(dll_dir)))
        ct2 = importlib.import_module("ctranslate2")
        details = report["ctranslate2"]
        details.update(version=str(ct2.__version__), hip_dll_markers=True,
                       device_count=ct2.get_cuda_device_count())
        _require(args.device_index < details["device_count"], "ct2_no_gpu",
                 "HIP CTranslate2 cannot see the requested GPU; CPU fallback is forbidden.")
        supported = sorted(ct2.get_supported_compute_types("cuda", args.device_index))
        details["supported_compute_types"] = supported
        _require(args.compute_type in supported, "ct2_compute_type",
                 "CTranslate2 does not support the requested GPU compute type; no fallback was attempted.")
        faster_whisper = importlib.import_module("faster_whisper")
        report["asr"]["faster_whisper_version"] = _version("faster-whisper")
        started = time.perf_counter()
        model = faster_whisper.WhisperModel(
            str(args.model_dir.resolve()), device="cuda", device_index=args.device_index,
            compute_type=args.compute_type, local_files_only=True, num_workers=1,
        )
        native = model.model
        details["device"] = native.device
        details["device_index"] = list(native.device_index)
        details["compute_type"] = native.compute_type
        _require(native.device == "cuda" and list(native.device_index) == [args.device_index]
                 and native.compute_type == args.compute_type, "asr_not_gpu",
                 "The loaded ASR model does not match the requested GPU/compute type.")
        segments, info = model.transcribe(str(args.wav.resolve()), beam_size=1, vad_filter=False,
                                          word_timestamps=False, condition_on_previous_text=False)
        segments = list(segments)
        torch.cuda.synchronize(args.device_index)
        character_count = sum(len(segment.text.strip()) for segment in segments)
        _require(character_count > 0, "asr_empty", "ASR returned no speech text; use a clear real speech recording.")
        _require(math.isfinite(info.duration) and info.duration > 0
                 and all(math.isfinite(segment.start) and math.isfinite(segment.end)
                         and 0 <= segment.start <= segment.end for segment in segments),
                 "asr_invalid_result", "ASR returned invalid durations or timestamps.")
        details["status"] = "passed"
        report["asr"].update(status="passed", segment_count=len(segments), text_characters=character_count,
                             audio_seconds=info.duration, elapsed_seconds=round(time.perf_counter() - started, 6))


def _run_smoke(args: argparse.Namespace) -> dict:
    """Force model-library offline mode and return a sanitized success/error report."""
    report = _report(args)
    os.environ.update(OFFLINE_ENV)
    try:
        _local_inputs(args)
        with ExitStack() as stack:
            for directory in dict.fromkeys(path.resolve() for path in args.dll_dir):
                stack.enter_context(os.add_dll_directory(str(directory)))
            report["stage"] = "torch"
            torch = _torch_smoke(args, report)
            if args.model_dir is not None:
                report["stage"] = "asr"
                _asr_smoke(args, report, torch)
        report.update(ok=True, stage="complete")
    except SmokeError as error:
        report["error"] = {"code": error.code, "message": str(error)}
    except Exception as error:
        report["error"] = {
            "code": "runtime_error", "exception_type": type(error).__name__,
            "message": "Smoke failed at the reported stage. Check local inputs, the pinned wheel APIs and DLL dependencies; no CPU retry occurred.",
        }
    if not report["ok"] and report["stage"] == "asr":
        report["asr"]["status"] = "failed"
    return report


def _run_isolated(args: argparse.Namespace, argv: list[str]) -> dict:
    """Run a deadline-bound child and validate its report against the exit status.

    Capture native output without forwarding it. Report timeouts, crashes and
    malformed worker output as structured failures instead of partial success.
    """
    report = _report(args)
    report["stage"] = "worker"
    try:
        _require(args.timeout > 0, "invalid_timeout", "Timeout must be positive.")
        result = subprocess.run(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--_worker", *argv],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=args.timeout,
            env={**os.environ, **OFFLINE_ENV}, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        reports = [line[len(REPORT_PREFIX):] for line in result.stdout.splitlines() if line.startswith(REPORT_PREFIX)]
        if result.returncode in (0, 1) and reports:
            child = json.loads(reports[-1])
            if child.get("schema_version") == 1 and child.get("ok") is (result.returncode == 0):
                return child
        report["error"] = {"code": "worker_failed", "message": "Native worker exited without a valid smoke report.",
                           "returncode": result.returncode}
    except subprocess.TimeoutExpired:
        report["error"] = {"code": "worker_timeout", "message": "Native smoke exceeded the requested timeout."}
    except SmokeError as error:
        report["error"] = {"code": error.code, "message": str(error)}
    except (OSError, ValueError, AttributeError):
        report["error"] = {"code": "worker_failed", "message": "Could not run or decode the native smoke worker."}
    return report


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--model-dir", type=Path, help="Explicit local converted faster-whisper model directory")
    parser.add_argument("--wav", type=Path, help="Explicit local real speech PCM WAV (up to 60 seconds)")
    parser.add_argument("--device-index", type=int, default=0)
    parser.add_argument("--compute-type", choices=("float16", "float32"), default="float16")
    parser.add_argument("--dll-dir", action="append", type=Path, default=[], help="Additional trusted local DLL directory")
    parser.add_argument("--timeout", type=int, default=180, help="Worker deadline in seconds (default: 180)")
    parser.add_argument("--_worker", action="store_true", help=argparse.SUPPRESS)
    return parser


def main(argv: list[str] | None = None) -> int:
    """Emit one JSON report and return 0 for success or 1 for runtime failure.

    Argument parsing retains argparse's help and usage exits. Internal workers
    prefix their report so the parent can separate it from native output.
    """
    argv = list(sys.argv[1:] if argv is None else argv)
    args = _parser().parse_args(argv)
    report = _run_smoke(args) if args._worker else _run_isolated(args, argv)
    print((REPORT_PREFIX if args._worker else "") + json.dumps(report, indent=None, allow_nan=False))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
