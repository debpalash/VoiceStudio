"""
PR-blocking smoke test (GATE-01).

Boots the FastAPI app in-process against the frozen regression fixture at
`tests/fixtures/omnivoice_data/` and asserts the lowest-cost endpoints
respond correctly. Designed to run in < 30 s on a warm uv cache so it can
sit on every PR across macOS / Windows / Linux without slowing reviewers.

Pattern source: `tests/test_router_smoke.py` (in-process TestClient + module
fixture). This file extends that pattern by pointing the backend at the
checked-in fixture via `OMNIVOICE_DATA_DIR`, so the smoke also validates
DB-touching paths (profiles list, history list) — not just route imports.

The 2.4 GB OmniVoice model load is short-circuited by `OMNIVOICE_MODEL=test`
(same convention as `test_router_smoke.py`).
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

import pytest

import backend_module_state


# ── env setup BEFORE any backend import ────────────────────────────────────
os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

FIXTURE_SRC = Path(__file__).resolve().parents[1] / "fixtures" / "omnivoice_data"
if not FIXTURE_SRC.exists():
    pytest.fail(
        "Fixture missing — run: uv run python scripts/seed-test-fixture.py",
        pytrace=False,
    )

# Copy the frozen fixture into a per-session temp dir so the smoke test
# never mutates the checked-in artifact. SQLite touches its file-change
# counter just on open, and the backend creates runtime subdirs
# (dub_jobs/, outputs/, preview/) under OMNIVOICE_DATA_DIR — both would
# show up as dirty in `git status` after every test run.
_FIXTURE_COPY = Path(tempfile.mkdtemp(prefix="omnivoice-smoke-"))
shutil.copytree(FIXTURE_SRC, _FIXTURE_COPY, dirs_exist_ok=True)


@pytest.fixture(scope="module")
def client():
    # Point backend.core.config.get_app_data_dir() at the COPY, scoped to
    # THIS module and undone afterwards. This used to be a module-level
    # `os.environ["OMNIVOICE_DATA_DIR"] = ...` — a process-wide leak: in a
    # combined `pytest tests/ backend/tests/` run every later fresh import
    # of core.config resolved DB/voices paths into the smoke fixture copy,
    # breaking backend/tests (personas import wrote voices into one data
    # dir while the test asserted another; audiobook resume read a
    # different DB than it seeded). CI's isolated invocations never see
    # combined-run leaks, so keep this bubble airtight for local runs.
    mp = pytest.MonkeyPatch()
    mp.setenv("OMNIVOICE_DATA_DIR", str(_FIXTURE_COPY))
    # Drop cached backend modules so this import re-reads the smoke env;
    # restore the originals afterwards (tests/backend_module_state.py).
    before = backend_module_state.snapshot()
    backend_module_state.purge()
    from fastapi.testclient import TestClient
    from main import app
    yield TestClient(app, client=("127.0.0.1", 50000))
    # Teardown mirrors setup: drop the modules imported under the smoke env
    # and reinstate the originals FIRST, then restore the env. Later tests
    # then share module objects with their collection-time imports instead
    # of inheriting smoke-bound paths or fresh twins.
    backend_module_state.restore(before)
    mp.undo()


# ── tests ──────────────────────────────────────────────────────────────────
def test_health_returns_ok(client):
    """`/health` is the canonical liveness probe (release.yml uses it too)."""
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert "device" in body


def test_profiles_endpoint_lists_fixture_voice(client):
    """The seeded voice_profiles row must surface via the public `/profiles` API."""
    r = client.get("/profiles")
    assert r.status_code == 200
    data = r.json()
    assert isinstance(data, list)
    assert any(p.get("id") == "test-voice" for p in data), (
        f"expected fixture profile 'test-voice', got {data}"
    )


def test_system_info_includes_data_dir(client):
    """`/system/info` must resolve `data_dir` — smokes the fixture-wiring path."""
    r = client.get("/system/info")
    assert r.status_code == 200
    assert "data_dir" in r.json()


def test_history_endpoint_empty(client):
    """Fixture has zero history rows; the route must reach the DB and return []."""
    r = client.get("/history")
    assert r.status_code == 200
    assert r.json() == []
