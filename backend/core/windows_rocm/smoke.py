"""Optional local CT2 speech inference, separate from model-free readiness."""
import argparse
import json
import math
import os
import subprocess
import sys
import wave
from pathlib import Path

from .probe import probe
from .verify import PROJECT_ROOT, verify_environment


def validate_inputs(model, wav):
    """Require complete local assets, including tokenizer to prevent HF fallback."""
    if not model or not wav:
        raise ValueError("Supply both a local CT2 model directory and a local WAV")
    if any("://" in str(value) for value in (model, wav)):
        raise ValueError("Remote model/audio URLs are not supported")
    model, wav = Path(model).resolve(), Path(wav).resolve()
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        if not (model / name).is_file():
            raise FileNotFoundError(f"Local CT2 model requires {name}")
    if not wav.is_file():
        raise FileNotFoundError("Supply an existing local WAV")
    with wave.open(str(wav), "rb") as stream:
        if stream.getnframes() < 1:
            raise ValueError("WAV contains no samples")
    return model, wav


def infer_local(model, wav):
    """Only the child calls this; iterating segments executes actual CT2 work."""
    probe()  # Initializes DLL handles and checks exact HIP binary before import.
    from faster_whisper import WhisperModel

    engine = WhisperModel(str(model), device="cuda", device_index=0,
                          compute_type="float16", local_files_only=True)
    native = engine.model
    if native.device != "cuda" or list(native.device_index) != [0] or native.compute_type != "float16":
        raise RuntimeError("CTranslate2 did not load the requested HIP GPU/float16 model")
    segments, info = engine.transcribe(str(wav), beam_size=1, vad_filter=False)
    segments = list(segments)
    if not any(segment.text.strip() for segment in segments):
        raise RuntimeError("No speech recognized; supply a clear speech recording")
    if (not math.isfinite(info.duration) or info.duration <= 0
            or any(not math.isfinite(part.start) or not math.isfinite(part.end)
                   or not 0 <= part.start <= part.end for part in segments)):
        raise RuntimeError("CTranslate2 returned invalid speech timing")
    return {"ready": True, "scope": "local-ct2-asr", "segments": len(segments)}


def run_smoke(model=None, wav=None):
    if model is None and wav is None:
        return verify_environment()
    model, wav = validate_inputs(model, wav)
    environment = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    try:
        result = subprocess.run(
            [sys.executable, "-m", "backend.core.windows_rocm.smoke", "--child",
             "--model", str(model), "--wav", str(wav)],
            cwd=PROJECT_ROOT, env=environment, timeout=180,
            capture_output=True, text=True, errors="replace", check=False,
        )
        if result.returncode:
            raise RuntimeError("Local CT2 inference failed; no speech readiness claimed")
        report = json.loads(result.stdout.strip().splitlines()[-1])
        if (not isinstance(report, dict) or report.get("ready") is not True
                or report.get("scope") != "local-ct2-asr"
                or type(report.get("segments")) is not int or report["segments"] <= 0):
            raise ValueError("Invalid local speech probe report")
        # Never return transcripts, user paths, or arbitrary native stdout.
        return {"ready": True, "scope": "local-ct2-asr", "segments": report["segments"]}
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError) as exc:
        raise RuntimeError("Local CT2 inference could not complete; no speech readiness claimed") from exc


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--model", help="Existing local CT2 Whisper model directory")
    parser.add_argument("--wav", help="Existing local WAV audio")
    parser.add_argument("--child", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    try:
        if args.child:
            model, wav = validate_inputs(args.model, args.wav)
            report = infer_local(model, wav)
        else:
            report = run_smoke(args.model, args.wav)
        print(json.dumps(report))
        return 0
    except Exception as exc:  # noqa: BLE001 - content-free CLI error boundary.
        print(json.dumps({"ready": False, "scope": "local-ct2-asr" if args.model or args.wav else "model-free",
                          "error": type(exc).__name__}))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
