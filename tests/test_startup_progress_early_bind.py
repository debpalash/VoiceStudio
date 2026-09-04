"""Early-bind startup: the socket answers while heavy init still runs.

The class under test: ~1 in 5 of every issue ever filed was "can't reach the
local backend", and a chunk of it was a backend that was merely *starting* —
torch import, router fan-out, alembic — with nothing listening to say so.
main.py now defers the heavy phases behind an already-bound socket; these
tests pin the three contracts that fix depends on:

1. `/startup/progress` (and `/health` 503) answer within seconds of spawn —
   long before full readiness — carrying the `x-omnivoice-backend` marker.
   FAILED before the refactor: nothing listened until import completed.
2. The startup gate 503s real routes with the `[starting]` marker until
   ready, and is inert after.
3. The #963 ordering invariant (legacy-translate migration strictly before
   the prefs→environ restore, before the yt-dlp overlay) survived the move
   into `_phase_a_build`.
"""

from __future__ import annotations

import ast
import json
import os
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

import pytest

BACKEND_DIR = Path(__file__).resolve().parents[1] / "backend"


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.slow
def test_progress_endpoint_answers_long_before_readiness(tmp_path):
    """Spawn the real server the way every deployment does (uvicorn CLI) and
    require a marker-stamped /startup/progress answer within 8s — a bound the
    old import-everything-first startup could not meet on a cold torch."""
    port = _free_port()
    env = os.environ | {
        "OMNIVOICE_DATA_DIR": str(tmp_path / "data"),
        "OMNIVOICE_DISABLE_FILE_LOG": "1",
        "OMNIVOICE_PRELOAD_CAPTURE_ASR": "0",
        "OMNIVOICE_EAGER_INIT": "0",  # the server path, even under pytest
    }
    proc = subprocess.Popen(
        [
            sys.executable, "-m", "uvicorn", "main:app",
            "--app-dir", str(BACKEND_DIR),
            "--host", "127.0.0.1", "--port", str(port),
        ],
        env=env,
        # DEVNULL, not PIPE: nothing drains the pipe, and a cold uvicorn +
        # torch boot writes enough to fill the OS buffer and wedge the child.
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + 8.0
        last_err = None
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/startup/progress", timeout=1
                ) as resp:
                    assert resp.status == 200
                    assert resp.headers.get("x-omnivoice-backend"), (
                        "progress body must carry the backend marker header"
                    )
                    body = json.loads(resp.read())
                    assert body["status"] in ("starting", "ready")
                    if body["status"] == "starting":
                        assert body["step"] in (
                            "env_prefs", "native_preload", "ml_imports",
                            "api_routes", "db_migrate", "services_start",
                        )
                    # /health mirrors the not-ready state as a 503 with step
                    # context (or 200 once ready) — never connection-refused.
                    try:
                        with urllib.request.urlopen(
                            f"http://127.0.0.1:{port}/health", timeout=2
                        ) as h:
                            assert h.status == 200
                    except urllib.error.HTTPError as he:
                        assert he.code == 503
                        assert json.loads(he.read())["status"] == "starting"
                    return
            except (urllib.error.URLError, ConnectionError, OSError) as exc:
                last_err = exc
                time.sleep(0.2)
        pytest.fail(
            f"/startup/progress did not answer within 8s of spawn "
            f"(last error: {last_err}) — the early-bind contract is broken"
        )
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()


def test_startup_gate_503s_with_starting_marker_then_goes_inert():
    from fastapi.testclient import TestClient

    sys.path.insert(0, str(BACKEND_DIR))
    from core import startup_progress
    from main import app

    # Lifespan-less on purpose (the eager app is ready at import); the gate
    # is middleware, so server-side errors from the DB-less test context are
    # returned, not raised — the assertions only care about gate behavior.
    client = TestClient(app, raise_server_exceptions=False)
    try:
        startup_progress._reset_for_tests()
        startup_progress.begin_step("ml_imports")
        r = client.get("/profiles")
        assert r.status_code == 503
        assert r.json()["detail"].startswith("[starting]"), (
            "the [starting] marker is what keeps the UI from offering "
            "'Report' for a not-ready backend (same convention as "
            "[shutting_down])"
        )
        assert r.json()["step"] == "ml_imports"
        assert r.headers.get("retry-after") == "2"
        # The two probe paths stay reachable while gated.
        assert client.get("/startup/progress").status_code == 200
        h = client.get("/health")
        assert h.status_code == 503
        assert h.json()["step"] == "ml_imports"
    finally:
        startup_progress._reset_for_tests()
        startup_progress.mark_ready()
    # Ready again → the gate is inert: the request reaches the real route
    # (whatever the DB-less test context makes of it) instead of a 503 gate.
    r = client.get("/profiles")
    assert r.status_code != 503


def test_progress_ledger_state_machine():
    sys.path.insert(0, str(BACKEND_DIR))
    import importlib

    from core import startup_progress as sp

    importlib.reload(sp)  # pristine module state regardless of test order
    assert sp.snapshot()["status"] == "starting"
    sp.begin_step("env_prefs")
    sp.begin_step("ml_imports")
    snap = sp.snapshot()
    assert snap["step"] == "ml_imports"
    states = {s["id"]: s["state"] for s in snap["steps"]}
    assert states["env_prefs"] == "done"
    assert states["ml_imports"] == "active"
    assert states["db_migrate"] == "pending"
    sp.fail("boom")
    snap = sp.snapshot()
    assert snap["status"] == "failed"
    assert snap["error"] == {"step": "ml_imports", "message": "boom"}
    assert {s["id"]: s["state"] for s in snap["steps"]}["ml_imports"] == "failed"
    # A fresh ledger marks ready cleanly.
    importlib.reload(sp)
    sp.begin_step("env_prefs")
    sp.mark_ready()
    assert sp.is_ready()
    assert sp.snapshot()["status"] == "ready"


def test_phase_a_thread_join_contract():
    """Shutdown joins the Phase A executor thread via the started/finished
    events. Three properties keep that join sound (review finds on #1550):
    the wrapper sets `finished` on EVERY exit including the already-built
    early return; `started` is set BEFORE submission so shutdown can't
    sample it unset while the callable is queued; and the submission is
    shielded so a cancel can't leave a queued callable that never runs and
    never sets the event."""
    sys.path.insert(0, str(BACKEND_DIR))
    import main

    main._phase_a_started.clear()
    main._phase_a_finished.clear()
    try:
        main._phase_a_build()  # eager test env: already built → early return
        assert main._phase_a_started.is_set()
        assert main._phase_a_finished.is_set(), (
            "the early-return path must still set finished, or a shutdown "
            "that observed started would wait on an event nothing sets"
        )
    finally:
        main._phase_a_started.set()
        main._phase_a_finished.set()

    src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.AsyncFunctionDef) and n.name == "_deferred_startup"
    )
    seg = ast.get_source_segment(src, fn)
    assert seg.index("_phase_a_started.set()") < seg.index("run_in_executor"), (
        "started must be set before submission — a queued callable is "
        "invisible to shutdown otherwise"
    )
    assert "asyncio.shield(loop.run_in_executor" in seg, (
        "the submission must be shielded — a cancelled queued callable "
        "never runs and never sets _phase_a_finished"
    )


def test_phase_a_preserves_the_963_ordering_invariant():
    """migrate_legacy_translate_prefs must run strictly before the prefs→env
    restore, which must run before the yt-dlp overlay — the move from module
    scope into _phase_a_build must not reorder them (#963)."""
    src = (BACKEND_DIR / "main.py").read_text(encoding="utf-8")
    tree = ast.parse(src)
    fn = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.FunctionDef) and n.name == "_phase_a_build_inner"
    )
    seg = ast.get_source_segment(src, fn)
    order = [
        seg.index("migrate_legacy_translate_prefs()"),
        seg.index("restore_env("),
        seg.index("activate_ytdlp_overlay()"),
        seg.index("ensure_media_tools_on_path()"),
        seg.index("_preload_cudnn8()"),
        seg.index("import torchaudio"),
        seg.index("from api.routers import"),
    ]
    assert order == sorted(order), (
        "_phase_a_build reordered the startup sequence — the #963 migration/"
        "prefs ordering and the cudnn-before-torch invariant must hold"
    )
