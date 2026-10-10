#!/usr/bin/env python3
"""Backend startup baseline: per-step boot timings and idle footprint.

Boots the backend twice on one throwaway data dir (first launch, then a
relaunch with the database and bytecode warm) and prints a markdown table.
Run from the repo root after `uv sync`:

    .venv/bin/python scripts/perf/backend_startup.py
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get(port: int, path: str) -> dict | None:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=1) as response:
            return json.load(response)
    except Exception:
        return None


def _rss_mb(pid: int) -> float:
    out = subprocess.run(["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True)
    return int(out.stdout.strip() or 0) / 1024


def boot(data_dir: str) -> dict:
    env = {**os.environ, "OMNIVOICE_DATA_DIR": data_dir}
    # A fresh port per boot, and a liveness check below, so a response can only
    # come from the backend this run launched, never from one already running.
    port = _free_port()
    proc = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(port)],
        cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    try:
        started = time.monotonic()
        progress = None
        while time.monotonic() - started < 300:
            if proc.poll() is not None:
                raise RuntimeError(f"backend exited with code {proc.returncode} before it was ready")
            progress = _get(port, "/startup/progress")
            if progress and progress["status"] in ("ready", "failed"):
                break
            time.sleep(0.5)
        assert progress and progress["status"] == "ready", f"boot did not finish: {progress}"
        time.sleep(20)  # settle before reading the idle footprint
        if proc.poll() is not None:
            raise RuntimeError(f"backend exited with code {proc.returncode} while idling")
        return {
            "total": progress["elapsed_s"],
            "steps": {s["id"]: s.get("t") for s in progress["steps"]},
            "rss": _rss_mb(proc.pid),
        }
    finally:
        proc.terminate()
        proc.wait(timeout=30)


if __name__ == "__main__":
    with tempfile.TemporaryDirectory() as data_dir:
        runs = {"first launch": boot(data_dir), "relaunch": boot(data_dir)}
    steps = list(next(iter(runs.values()))["steps"])
    print("| Step | " + " | ".join(runs) + " |")
    print("|---|" + "---|" * len(runs))
    for step in steps:
        print(f"| {step} | " + " | ".join(f"{r['steps'][step]:.1f} s" for r in runs.values()) + " |")
    print("| **Routes live** | " + " | ".join(f"**{r['total']:.1f} s**" for r in runs.values()) + " |")
    print("| Idle RSS (20 s after ready) | " + " | ".join(f"{r['rss']:.0f} MB" for r in runs.values()) + " |")
