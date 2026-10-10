"""Explicit local audio.cpp Sortformer adapter for the shared diarisation flow."""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
import subprocess
import time
import threading
from tempfile import TemporaryDirectory

logger = logging.getLogger("omnivoice.diarisation.native")
_process_lock = threading.Lock()
_processes: set = set()
MAX_V1_AUDIO_SECONDS = 120.0
SORTFORMER_FRAME_SAMPLES = 1280  # 80 ms at the required 16 kHz input rate.


def _sortformer_command(binary: Path, model: Path, device, source: Path, output: Path):
    return [
        str(binary), "--task", "diar", "--family", "sortformer_diar",
        "--model", str(model), "--backend", device.backend,
        "--device", str(device.index), "--audio", str(source),
        "--turns-out", str(output),
        # Accelerator builds otherwise keep the default 20-second fixed graph
        # and reject ordinary clips. Grow remains bounded by the v1 limit below.
        "--session-option", "graph_capacity_mode=grow",
    ]


def _validated_turn(turn: dict, audio_frames: int) -> tuple[int, int, str]:
    start, end = turn.get("start_sample"), turn.get("end_sample")
    speaker = turn.get("speaker_id")
    if (
        type(start) is not int
        or type(end) is not int
        or start < 0
        or start >= end
        or end > audio_frames + SORTFORMER_FRAME_SAMPLES
        or not isinstance(speaker, str)
        or not speaker
    ):
        raise ValueError("Invalid native speaker-turn boundaries")
    # The decoder works in 80 ms frames and can pad its final turn one frame
    # beyond a non-aligned WAV boundary. Keep the timeline inside the media.
    return start, min(end, audio_frames), speaker


def is_running() -> bool:
    with _process_lock:
        return bool(_processes)


class NativeSortformer:
    """Stateless native invocation; the GGUF is never downloaded implicitly."""

    def __init__(self):
        from services.diarization_runtime import SORTFORMER_REPO, sortformer_model_path
        from services.model_acceptance import ensure_accepted

        # Built per use, so this also applies a revoked acceptance (#2689).
        # A user-configured GGUF is a local asset with no registry identity.
        if not os.environ.get("OMNIVOICE_DIARIZATION_MODEL", "").strip():
            ensure_accepted([SORTFORMER_REPO])
        from engines.audiocpp.bootstrap import resolve_server_binary

        try:
            self.model = sortformer_model_path()
        except Exception as exc:
            raise FileNotFoundError(
                "Install the audio.cpp Sortformer model in Settings > Models > Diarisation"
            ) from exc
        if not self.model.is_file() or self.model.suffix.lower() != ".gguf":
            raise FileNotFoundError("The configured Sortformer GGUF is missing")
        with self.model.open("rb") as model_file:
            if model_file.read(4) != b"GGUF":
                raise ValueError("The configured Sortformer model is not a GGUF file")
        server = resolve_server_binary()
        self.binary = server.with_name("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
        if not self.binary.is_file():
            raise FileNotFoundError("The installed audio.cpp directory has no audiocpp_cli")

    def __call__(self, audio_path, *, num_speakers=None, job_id=None, cancel_check=None):
        if num_speakers is not None:
            raise ValueError("Sortformer v1 detects up to four speakers but cannot enforce an exact speaker count")
        import soundfile as sf
        from pyannote.core import Annotation, Segment
        from core.contained_subprocess import spawn_owned
        from engines.audiocpp.bootstrap import resolve_compute_selection
        from services.proc_registry import register_proc, unregister_proc

        def check_cancelled():
            if cancel_check is not None and cancel_check():
                raise RuntimeError("Native diarisation cancelled")

        check_cancelled()

        audio_info = sf.info(str(audio_path))
        if audio_info.duration > MAX_V1_AUDIO_SECONDS:
            raise ValueError(
                "Sortformer v1 supports recordings up to 120 seconds; "
                "select pyannote for longer recordings"
            )
        device = resolve_compute_selection().device
        with TemporaryDirectory(prefix="voicestudio-sortformer-") as directory:
            source = Path(audio_path).resolve()
            output = Path(directory) / "turns.json"
            command = _sortformer_command(
                self.binary, self.model, device, source, output
            )
            def run_owned(command, log_name):
                with (Path(directory) / log_name).open("wb") as log:
                    check_cancelled()
                    process = spawn_owned(command, stdout=log, stderr=subprocess.STDOUT)
                    with _process_lock:
                        _processes.add(process)
                    try:
                        if job_id is not None:
                            register_proc(job_id, process)
                        deadline = time.monotonic() + 600
                        while True:
                            check_cancelled()
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                raise subprocess.TimeoutExpired(command, 600)
                            try:
                                code = process.wait(timeout=min(0.25, remaining))
                                break
                            except subprocess.TimeoutExpired:
                                continue
                    except BaseException:
                        process.kill()
                        process.wait()
                        raise
                    finally:
                        with _process_lock:
                            _processes.discard(process)
                        if job_id is not None:
                            unregister_proc(job_id, process)
                check_cancelled()
                if code != 0:
                    with (Path(directory) / log_name).open("rb") as diagnostic:
                        diagnostic.seek(0, 2)
                        diagnostic.seek(max(0, diagnostic.tell() - 8192))
                        tail = diagnostic.read().decode("utf-8", errors="replace")
                    logger.error("Sortformer exited with %s; native log tail:\n%s", code, tail)
                    raise RuntimeError(f"Native Sortformer failed (exit {code})")
            if (audio_info.samplerate != 16000 or audio_info.channels != 1
                or audio_info.format != "WAV" or audio_info.subtype != "PCM_16"):
                from services.ffmpeg_utils import find_ffmpeg, local_inputs_only
                normalized = Path(directory) / "input.wav"
                run_owned(local_inputs_only([
                    find_ffmpeg(), "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
                    "-i", str(source), "-vn", "-ac", "1", "-ar", "16000",
                    "-c:a", "pcm_s16le", str(normalized),
                ], tool="ffmpeg"), "normalize.log")
                source = normalized
                audio_info = sf.info(str(source))
                command[command.index("--audio") + 1] = str(source)
            run_owned(command, "native.log")
            turns = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(turns, list):
                raise ValueError("Invalid native speaker-turn output")
            annotation = Annotation()
            for index, turn in enumerate(turns):
                start, end, speaker = _validated_turn(turn, audio_info.frames)
                annotation[Segment(start / 16000, end / 16000), index] = speaker
            return annotation
