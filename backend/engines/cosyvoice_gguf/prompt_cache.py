"""Content-addressed cache for CosyVoice's speaker-prompt encoder output."""

from __future__ import annotations

import hashlib
import os
import subprocess  # nosec B404 -- resolved pinned executable, fixed arguments
import tempfile
from pathlib import Path

from core.config import DATA_DIR

from engines.cosyvoice_gguf import bootstrap


def prompt_cache_key(audio: str | Path, transcript: str) -> str:
    digest = hashlib.sha256()
    with Path(audio).open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    digest.update(b"\0")
    digest.update(transcript.encode("utf-8"))
    return digest.hexdigest()


def cached_prompt(audio: str | Path, transcript: str) -> Path:
    if not transcript.strip():
        raise ValueError("CosyVoice voice cloning requires the reference transcript")
    key = prompt_cache_key(audio, transcript)
    root = Path(DATA_DIR) / "engines" / bootstrap.ENGINE_ID / "prompts"
    target = root / f"{key}.gguf"
    if target.is_file() and target.stat().st_size:
        return target
    root.mkdir(parents=True, exist_ok=True)
    frontend = bootstrap.frontend_dir()
    fd, temporary = tempfile.mkstemp(prefix=f"{key}-", suffix=".gguf", dir=root)
    os.close(fd)
    temp_path = Path(temporary)
    temp_path.unlink()
    args = [
        str(bootstrap.resolve_cli()),
        "--frontend-only",
        "--speech-tokenizer",
        str(frontend / bootstrap.FRONTEND_FILES[1]),
        "--campplus",
        str(frontend / bootstrap.FRONTEND_FILES[0]),
        "--prompt-audio",
        str(Path(audio).resolve()),
        "--prompt-text",
        transcript,
        "--prompt-speech-output",
        str(temp_path),
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300, check=False
        )
        if result.returncode or not temp_path.is_file() or not temp_path.stat().st_size:
            raise RuntimeError(
                f"CosyVoice prompt encoding failed: {(result.stderr or result.stdout)[-1200:]}"
            )
        os.replace(temp_path, target)
        return target
    finally:
        temp_path.unlink(missing_ok=True)
