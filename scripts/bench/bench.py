#!/usr/bin/env python3
"""Offline startup and idle benchmark for VoiceStudio.

Measures, with fixed inputs and N runs (median and p90):

- backend: spawn to first HTTP answer, spawn to ``/health`` 200, the
  ``/startup/progress`` step timings, idle RSS of the process tree and its idle
  CPU over a fixed window. Runs relaunch on a throwaway data dir (created by
  one unmeasured boot) with every model cache redirected into it, so no model
  loads and nothing of the user's is read or written.
- imports: one extra boot under ``python -X importtime``; the slowest
  top-level imports by cumulative time.
- bundle: renderer chunk sizes of an existing build (``bundle.mjs``).
- app: Electron launch to first contentful paint and to the Home screen
  (backend ready), plus main/renderer/GPU idle CPU (``app_start.mjs``). The app
  runs on a temp profile against the backend this script started, with the
  updater off and every non-local renderer request aborted.

Nothing here downloads anything. Outbound HTTP from the backend is pointed at
a dead local proxy and Hugging Face is set offline, so an accidental network
call fails fast instead of skewing (or leaking from) a run.

Run from the repo root with the backend's interpreter (stdlib + psutil)::

    .venv/bin/python scripts/bench/bench.py                       # everything
    .venv/bin/python scripts/bench/bench.py --only backend,bundle --runs 3
    .venv/bin/python scripts/bench/bench.py --only app \\
        --app /Applications/VoiceStudio.app/Contents/MacOS/VoiceStudio

``app`` and ``bundle`` need ``bun install`` and ``bun run --cwd electron build``
(the default ``--app`` is that unpackaged build). Results go to
``bench-results/<timestamp>.json`` and ``.md`` unless ``--out`` says otherwise.
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

try:
    import psutil
except ImportError:  # pragma: no cover - the backend venv ships psutil
    psutil = None

ROOT = Path(__file__).resolve().parents[2]
HERE = Path(__file__).resolve().parent


# ── statistics ───────────────────────────────────────────────────────────


def percentile(values: list[float], q: float) -> float:
    """Linear-interpolated percentile (q in 0..100); the median is q=50."""
    if not values:
        raise ValueError("no values")
    ordered = sorted(values)
    rank = (len(ordered) - 1) * q / 100
    low = int(rank)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (rank - low)


def summarize(values: list[float]) -> dict:
    return {
        "median": round(percentile(values, 50), 3),
        "p90": round(percentile(values, 90), 3),
        "min": round(min(values), 3),
        "max": round(max(values), 3),
        "n": len(values),
    }


# ── process helpers ──────────────────────────────────────────────────────


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _tree(pid: int) -> list:
    root = psutil.Process(pid)
    return [root, *root.children(recursive=True)]


def tree_rss_mb(pid: int) -> float:
    total = 0
    for proc in _tree(pid):
        try:
            total += proc.memory_info().rss
        except psutil.Error:
            pass
    return total / 2**20


def tree_cpu_seconds(pid: int) -> float:
    total = 0.0
    for proc in _tree(pid):
        try:
            times = proc.cpu_times()
            total += times.user + times.system
        except psutil.Error:
            pass
    return total


def isolated_env(data_dir: str) -> dict[str, str]:
    """Environment for a backend that cannot see this machine's state or network.

    ``OMNIVOICE_ENV_FILE`` points at a file that does not exist: the backend
    otherwise loads ``~/.config/omnivoice/env``, which can redirect the data
    folder. The model caches move into ``data_dir`` so results do not depend on
    which models this machine has (startup warm-ups load a model only when its
    weights are already on disk).
    """
    dead_proxy = "http://127.0.0.1:9"
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.upper().endswith("_PROXY") and not key.startswith(("OMNIVOICE_", "VOICESTUDIO_"))
    }
    env.update(
        OMNIVOICE_DATA_DIR=data_dir,
        OMNIVOICE_ENV_FILE=os.path.join(data_dir, "isolated.env"),
        OMNIVOICE_ANALYTICS_DISABLED="1",
        HF_HOME=os.path.join(data_dir, "hf"),
        HF_HUB_CACHE=os.path.join(data_dir, "hf", "hub"),
        HF_HUB_OFFLINE="1",
        TRANSFORMERS_OFFLINE="1",
        OMNIVOICE_CACHE_DIR=os.path.join(data_dir, "omnivoice-cache"),
        TORCH_HOME=os.path.join(data_dir, "torch"),
        AUDIOSEAL_CACHE_DIR=os.path.join(data_dir, "audioseal-cache"),
        HTTP_PROXY=dead_proxy,
        HTTPS_PROXY=dead_proxy,
        ALL_PROXY=dead_proxy,
        http_proxy=dead_proxy,
        https_proxy=dead_proxy,
        all_proxy=dead_proxy,
        NO_PROXY="127.0.0.1,localhost",
        no_proxy="127.0.0.1,localhost",
        PYTHONUNBUFFERED="1",
    )
    return env


# Loopback checks must never go through a developer's HTTP(S)_PROXY.
_LOCAL_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def _get(port: int, path: str) -> tuple[int, dict | None]:
    try:
        with _LOCAL_OPENER.open(f"http://127.0.0.1:{port}{path}", timeout=2) as response:
            return response.status, json.load(response)
    except urllib.error.HTTPError as error:
        return error.code, None
    except Exception:
        return 0, None


class Backend:
    """One isolated backend process on a fresh port and data dir."""

    def __init__(self, python: str, backend_dir: Path, extra_args: tuple[str, ...] = (), stderr=None,
                 data_dir: str | None = None):
        self.owns_data = data_dir is None
        self.data_dir = data_dir or tempfile.mkdtemp(prefix="vs-bench-data-")
        self.port = _free_port()
        self.started = time.monotonic()
        self.proc = subprocess.Popen(
            [python, *extra_args, "-m", "uvicorn", "main:app", "--host", "127.0.0.1", "--port", str(self.port)],
            cwd=backend_dir,
            env=isolated_env(self.data_dir),
            stdout=subprocess.DEVNULL,
            stderr=stderr or subprocess.DEVNULL,
        )

    def wait_ready(self, timeout: float = 300) -> dict:
        first_answer = None
        while time.monotonic() - self.started < timeout:
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited with code {self.proc.returncode} before it was ready")
            status, body = _get(self.port, "/health")
            now = time.monotonic() - self.started
            if status and first_answer is None:
                first_answer = now
            if status == 200 and body and body.get("status") == "ok":
                _, progress = _get(self.port, "/startup/progress")
                steps = {s["id"]: s.get("t") for s in (progress or {}).get("steps", []) if s.get("t") is not None}
                return {"first_answer_s": first_answer, "ready_s": now, "steps": steps}
            time.sleep(0.05)
        raise RuntimeError("backend did not become ready in time")

    def idle(self, settle: float, window: float) -> dict:
        """Settle (background reconcile and warm-up timers), then sample RSS and CPU."""
        time.sleep(settle)
        cpu_start = tree_cpu_seconds(self.proc.pid)
        started = time.monotonic()
        rss = []
        while time.monotonic() - started < window:
            time.sleep(min(3, window / 10))
            if self.proc.poll() is not None:
                raise RuntimeError(f"backend exited with code {self.proc.returncode} while idling")
            rss.append(tree_rss_mb(self.proc.pid))
        if not rss:  # a zero or tiny --idle-window: still report one sample
            rss.append(tree_rss_mb(self.proc.pid))
        elapsed = time.monotonic() - started
        cpu = tree_cpu_seconds(self.proc.pid) - cpu_start
        return {
            "rss_mb": percentile(rss, 50),
            "cpu_percent": 100 * cpu / max(elapsed, 1e-6),
            "children": len(_tree(self.proc.pid)) - 1,
        }

    def stop(self) -> None:
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()
        if self.owns_data:
            shutil.rmtree(self.data_dir, ignore_errors=True)


# ── benchmarks ───────────────────────────────────────────────────────────


def bench_backend(args) -> dict:
    """A returning user's backend: one unmeasured boot creates the data dir and
    database, then every measured run relaunches on it."""
    runs = []
    data_dir = tempfile.mkdtemp(prefix="vs-bench-data-")
    try:
        priming = Backend(args.python, args.backend_dir, data_dir=data_dir)
        try:
            priming.wait_ready()
        finally:
            priming.stop()
        for index in range(args.runs):
            backend = Backend(args.python, args.backend_dir, data_dir=data_dir)
            try:
                run = backend.wait_ready()
                run.update(backend.idle(args.settle, args.idle_window))
            finally:
                backend.stop()
            runs.append(run)
            print(f"  backend run {index + 1}/{args.runs}: ready {run['ready_s']:.2f} s, "
                  f"rss {run['rss_mb']:.0f} MB, cpu {run['cpu_percent']:.1f} %", file=sys.stderr)
    finally:
        shutil.rmtree(data_dir, ignore_errors=True)
    steps = list(dict.fromkeys(step for run in runs for step in run["steps"]))  # /startup/progress order
    return {
        "first_answer_s": summarize([r["first_answer_s"] for r in runs]),
        "ready_s": summarize([r["ready_s"] for r in runs]),
        "steps_s": {s: summarize([r["steps"][s] for r in runs if s in r["steps"]]) for s in steps},
        "idle_rss_mb": summarize([r["rss_mb"] for r in runs]),
        "idle_cpu_percent": summarize([r["cpu_percent"] for r in runs]),
        "settle_s": args.settle,
        "idle_window_s": args.idle_window,
        "runs": runs,
    }


_IMPORT_LINE = re.compile(r"^import time:\s+(\d+)\s+\|\s+(\d+)\s+\|(\s*)(\S+)")


def parse_importtime(text: str, top: int = 15) -> list[dict]:
    """Slowest imports by cumulative time, at the outermost nesting level."""
    rows = []
    for line in text.splitlines():
        match = _IMPORT_LINE.match(line)
        if match and len(match.group(3)) <= 1:
            rows.append({"module": match.group(4), "cumulative_ms": int(match.group(2)) / 1000})
    rows.sort(key=lambda row: row["cumulative_ms"], reverse=True)
    return rows[:top]


def bench_imports(args) -> dict:
    with tempfile.NamedTemporaryFile(prefix="vs-bench-importtime-", suffix=".log", delete=False) as log:
        path = log.name
    try:
        with open(path, "w") as stderr:
            backend = Backend(args.python, args.backend_dir, ("-X", "importtime"), stderr=stderr)
            try:
                ready = backend.wait_ready()
                time.sleep(args.settle)  # include imports from background startup work
            finally:
                backend.stop()
        text = Path(path).read_text(errors="replace")
    finally:
        os.unlink(path)
    return {"ready_s_under_importtime": ready["ready_s"], "top": parse_importtime(text)}


def _node(script: str, *extra: str) -> dict:
    out = subprocess.run(["node", str(HERE / script), *extra], capture_output=True, text=True, cwd=ROOT)
    if out.returncode != 0:
        raise RuntimeError(f"{script} failed:\n{out.stderr[-4000:]}")
    return json.loads(out.stdout)


def bench_bundle(args) -> dict:
    return _node("bundle.mjs", "--json", "--dir", str(args.renderer_dir))


def bench_app(args) -> dict:
    backend = Backend(args.python, args.backend_dir)
    try:
        backend.wait_ready()
        print("  backend ready for the app runs", file=sys.stderr)
        extra = ["--port", str(backend.port), "--runs", str(args.runs), "--idle-window", str(args.idle_window)]
        if args.app:
            extra += ["--app", args.app]
        return _node("app_start.mjs", *extra)
    finally:
        backend.stop()


# ── report ───────────────────────────────────────────────────────────────


def machine() -> dict:
    info = {
        "os": platform.system(),
        "os_version": platform.mac_ver()[0] or platform.release(),
        "arch": platform.machine(),
        "python": platform.python_version(),
        "cpus": os.cpu_count(),
    }
    if psutil:
        info["ram_gb"] = round(psutil.virtual_memory().total / 2**30)
    if sys.platform == "darwin":
        chip = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True)
        info["cpu"] = chip.stdout.strip()
    try:
        info["app_version"] = json.loads((ROOT / "package.json").read_text())["version"]
        info["commit"] = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"], capture_output=True, text=True, cwd=ROOT
        ).stdout.strip()
    except Exception:
        pass
    return info


def _fmt(stat: dict, unit: str, digits: int = 2) -> str:
    return f"{stat['median']:.{digits}f} / {stat['p90']:.{digits}f} {unit}"


def markdown(result: dict) -> str:
    m = result["machine"]
    lines = [
        f"Machine: {m.get('cpu', m['arch'])}, {m.get('ram_gb', '?')} GB, {m['os']} {m['os_version']}; "
        f"VoiceStudio {m.get('app_version', '?')} ({m.get('commit', '?')}); {result['date']}; "
        f"{result['runs']} runs",
    ]
    if "backend" in result or "app" in result:
        lines += ["", "| Metric | median / p90 |", "|---|---|"]
    if "backend" in result:
        b = result["backend"]
        lines += [
            f"| Backend: spawn to first HTTP answer | {_fmt(b['first_answer_s'], 's')} |",
            f"| Backend: spawn to `/health` ready | {_fmt(b['ready_s'], 's')} |",
            *(f"| &nbsp;&nbsp;step `{k}` (duration) | {_fmt(v, 's')} |" for k, v in b["steps_s"].items()),
            f"| Backend idle RSS, process tree ({b['settle_s']:.0f} s after ready) | {_fmt(b['idle_rss_mb'], 'MB', 0)} |",
            f"| Backend idle CPU ({b['idle_window_s']:.0f} s window) | {_fmt(b['idle_cpu_percent'], '%', 1)} |",
        ]
    if "app" in result:
        a = result["app"]
        lines += [
            f"| App: launch to first contentful paint | {_fmt(a['first_paint_ms'], 'ms', 0)} |",
            f"| App: launch to Home shown (backend ready) | {_fmt(a['home_ms'], 'ms', 0)} |",
            *(f"| App idle CPU: {k} ({a['idle_window_s']} s window) | {_fmt(v, '%', 1)} |"
              for k, v in a["idle_cpu_percent"].items()),
        ]
    out = "\n".join(lines)
    if "bundle" in result:
        out += "\n\n" + subprocess.run(
            ["node", "--input-type=module", "-e",
             f"import {{ markdown }} from {json.dumps((HERE / 'bundle.mjs').as_uri())};"
             "let s='';process.stdin.on('data',d=>s+=d).on('end',()=>console.log(markdown(JSON.parse(s))));"],
            input=json.dumps(result["bundle"]), capture_output=True, text=True,
        ).stdout.strip()
    if "imports" in result:
        out += "\n\n| Slowest top-level imports during boot | cumulative |\n|---|---|\n" + "\n".join(
            f"| `{row['module']}` | {row['cumulative_ms']:.0f} ms |" for row in result["imports"]["top"]
        )
    return out + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", default="backend,imports,bundle,app",
                        help="comma list of: backend, imports, bundle, app")
    parser.add_argument("--runs", type=int, default=5)
    parser.add_argument("--settle", type=float, default=30,
                        help="seconds after ready before idle sampling (reconcile and warm-up timers finish)")
    parser.add_argument("--idle-window", type=float, default=30, help="seconds of idle CPU/RSS sampling")
    parser.add_argument("--python", default=sys.executable, help="backend interpreter (default: this one)")
    parser.add_argument("--backend-dir", type=Path, default=ROOT / "backend")
    parser.add_argument("--renderer-dir", type=Path, default=ROOT / "electron/out/renderer")
    parser.add_argument("--app", help="Electron executable to launch (default: the unpackaged electron/out build)")
    parser.add_argument("--out", type=Path, help="output path without extension (default: bench-results/<time>)")
    args = parser.parse_args(argv)
    if psutil is None:
        parser.error("psutil is required: run with the backend's .venv/bin/python")
    wanted = [part.strip() for part in args.only.split(",") if part.strip()]

    result: dict = {"date": time.strftime("%Y-%m-%d"), "machine": machine(), "runs": args.runs}
    for name, fn in (("bundle", bench_bundle), ("backend", bench_backend),
                     ("imports", bench_imports), ("app", bench_app)):
        if name in wanted:
            print(f"{name}…", file=sys.stderr)
            result[name] = fn(args)

    out = args.out or ROOT / "bench-results" / time.strftime("%Y%m%d-%H%M%S")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.with_suffix(".json").write_text(json.dumps(result, indent=2) + "\n")
    report = markdown(result)
    out.with_suffix(".md").write_text(report)
    print(report)
    return 0


if __name__ == "__main__":
    sys.exit(main())
