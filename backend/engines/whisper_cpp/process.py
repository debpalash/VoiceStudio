"""Run the local CLI within one deadline, with an old-CLI JSON fallback."""
import json
import re
import subprocess
import time
from pathlib import Path

from services.asr_backend import ASRTimeoutError


def run_transcription(cmd: list[str], output: Path, timeout: float) -> dict:
    deadline = time.monotonic() + timeout
    for attempt in range(2):
        try:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired('whisper-cli', timeout)
            proc = subprocess.run(
                cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                timeout=remaining, check=False,
            )
        except subprocess.TimeoutExpired:
            raise ASRTimeoutError(f'whisper.cpp transcription timed out after {timeout}s') from None
        except OSError:
            raise RuntimeError("Could not start whisper.cpp transcription") from None
        if proc.returncode == 0 and output.is_file():
            try:
                return json.loads(output.read_bytes().decode('utf-8', errors='replace'))
            except (OSError, ValueError, UnicodeError):
                raise RuntimeError('Invalid whisper.cpp JSON transcript') from None
        unsupported_full_json = re.search(
            r'(?:unknown|unrecognized|unrecognised|unsupported|invalid) (?:argument|option)[^\r\n]*output-json-full',
            proc.stderr or '', re.IGNORECASE,
        )
        if attempt == 0 and '--output-json-full' in cmd and unsupported_full_json:
            cmd = ['-oj' if arg == '--output-json-full' else arg for arg in cmd]
            output.unlink(missing_ok=True)
            continue
        # CLI output may contain customer text, model paths and input paths.
        raise RuntimeError(f'whisper.cpp failed to generate transcript (exit code {proc.returncode})')
    raise RuntimeError('whisper.cpp failed to generate transcript')
