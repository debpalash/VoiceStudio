"""Load an installed audio.cpp model in the stock release runtime and speak.

This smoke is intentionally opt-in: it never downloads a runtime or model. It
uses the binary and GGUF the app would resolve (the managed runtime or
``OMNIVOICE_AUDIOCPP_BIN``, and the installed package or its model env var),
starts the managed server, generates one short sentence and writes the WAV
beneath the operating-system temp dir.
"""

from __future__ import annotations

import argparse
import sys
import tempfile
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from engines.audiocpp import bootstrap  # noqa: E402
from services import tts_backend  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "engine",
        nargs="?",
        default="audiocpp",
        help="audio.cpp engine id, e.g. audiocpp or audiocpp-dots",
    )
    parser.add_argument("--text", default="This is a short audio.cpp smoke test.")
    parser.add_argument("--ref-audio", type=Path, help="Optional reference clip for cloning")
    parser.add_argument("--ref-text", help="Transcript of --ref-audio")
    args = parser.parse_args()

    if not args.engine.startswith("audiocpp"):
        parser.error("choose an audio.cpp engine id")
    backend_cls = tts_backend.get_backend_class(args.engine)
    ok, reason = backend_cls.is_available()
    if not ok:
        print(f"not ready: {reason}", file=sys.stderr)
        return 2
    binary = bootstrap.resolve_server_binary()
    legacy = bootstrap.legacy_runtime_version(binary)
    print(f"runtime: {binary} ({legacy or bootstrap.VERSION + ' or user-managed'})")
    print(f"model: {bootstrap.resolve_model_file(backend_cls._package())}")

    backend = backend_cls()
    try:
        started = time.monotonic()
        kwargs = {"seed": 123}
        if args.ref_audio:
            kwargs["ref_audio"] = str(args.ref_audio.expanduser().resolve())
            kwargs["ref_text"] = args.ref_text
        wav = backend.generate(args.text, **kwargs)
        elapsed = time.monotonic() - started
        import soundfile as sf

        out = Path(tempfile.gettempdir()) / f"voicestudio-{args.engine}-smoke.wav"
        samples = wav.squeeze(0).numpy()
        sf.write(out, samples, backend.sample_rate)
        seconds = len(samples) / backend.sample_rate
        print(f"ok: {seconds:.2f}s of audio at {backend.sample_rate} Hz in {elapsed:.1f}s -> {out}")
        return 0 if seconds > 0 else 1
    finally:
        backend.unload()


if __name__ == "__main__":
    raise SystemExit(main())
