"""
Smoke tests — one per router — so CI turns green on every touched file.

Each test hits the lightest happy-path endpoint that doesn't touch the TTS
model or hit network. The point is not coverage depth — we have richer tests
for that elsewhere — but to catch "the module doesn't import" / "the route is
gone" regressions on every PR.
"""
import os
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture(scope="module")
def client():
    # Lazy import so test_api.py's session fixtures can mock the model first
    # if both suites run together.
    from fastapi.testclient import TestClient
    from main import app

    # Create the DB schema explicitly against whatever DB is active *now*.
    # A bare `TestClient(app)` (no `with` block) never enters the app
    # lifespan, so the lifespan's `init_db()` — the only place the schema is
    # laid down (main.py) — never runs. These smoke tests therefore used to
    # free-ride on the schema some *earlier* module happened to create on the
    # shared data dir. That made them order-dependent: run first / alone, or
    # after a module that reloads `core.config`/`core.db` and leaves
    # `core.db.DB_PATH` pointed at a fresh, schema-less DB (e.g.
    # test_pronunciation_api's `importlib.reload` teardown), and every
    # DB-backed route 500s with `no such table: jobs` / `voice_profiles`.
    # Seeding the schema here — the same `init_db()` call test_api.py /
    # test_personas_api.py use — makes the suite self-sufficient and
    # leak-proof against full-suite ordering (same class as #878 / #917).
    # Uses the live `core.db` from sys.modules so it targets the exact
    # DB_PATH the app's routers resolve to.
    import core.db
    core.db.init_db()

    # `client=("127.0.0.1", 50000)` so `request.client.host` resolves to a
    # loopback address — the system router is gated by a router-level
    # `require_admin` dependency. Smoke tests are happy-path tests and
    # should pass the gate.
    return TestClient(app, client=("127.0.0.1", 50000))


# ── system ──────────────────────────────────────────────────────────────────
def test_system_info_smoke(client):
    r = client.get("/system/info")
    assert r.status_code == 200
    body = r.json()
    assert "data_dir" in body
    assert "device" in body
    # The Docker/web build has no Tauri getVersion(); Settings → About reads the
    # running version from here so it shows the real version, not a dash (#249).
    from core.version import APP_VERSION
    assert body["app_version"] == APP_VERSION
    # Settings → About → Architecture must reflect the SERVER's machine, not the
    # client browser's navigator.platform (which showed "Win32" in Docker, #262).
    import platform as _pf
    assert body["arch"] == _pf.machine()


def test_system_info_reports_code_fingerprint(client, monkeypatch):
    """#1770: the desktop attach handshake compares this against its own
    build's fingerprint (frontend/src-tauri/src/backend.rs::
    code_fingerprint_is_current) to tell "this build's code" apart from "a
    same-version backend running older code" — a plain version match isn't
    enough because `main` holds one version string for an entire release
    cycle. Blank by default (no OMNIVOICE_BUILD_FINGERPRINT set, e.g. a
    manually started backend); echoed back verbatim when Tauri sets it.
    """
    monkeypatch.delenv("OMNIVOICE_BUILD_FINGERPRINT", raising=False)
    body = client.get("/system/info").json()
    assert body["code_fingerprint"] == ""

    monkeypatch.setenv("OMNIVOICE_BUILD_FINGERPRINT", "deadbeef1234")
    body = client.get("/system/info").json()
    assert body["code_fingerprint"] == "deadbeef1234"


def test_health_exposes_version(client):
    """`/health` is the zero-auth way to confirm the running version (#249)."""
    from core.version import APP_VERSION
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["version"] == APP_VERSION


def test_system_logs_smoke(client):
    r = client.get("/system/logs?tail=10")
    assert r.status_code == 200
    assert "lines" in r.json()


def test_system_logs_tauri_smoke(client):
    r = client.get("/system/logs/tauri?tail=10")
    # 200 whether file exists or not — the endpoint just reports either way.
    assert r.status_code == 200
    body = r.json()
    assert "exists" in body


def test_model_status_smoke(client):
    r = client.get("/model/status")
    assert r.status_code == 200
    assert "status" in r.json()


def test_sysinfo_smoke(client):
    r = client.get("/sysinfo")
    assert r.status_code == 200
    assert "cpu" in r.json()


# ── profiles ────────────────────────────────────────────────────────────────
def test_profiles_list_smoke(client):
    r = client.get("/profiles")
    # Empty list is fine on a fresh DB; the point is that the module imports
    # and the route exists.
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── projects ────────────────────────────────────────────────────────────────
def test_projects_list_smoke(client):
    r = client.get("/projects")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── engines (Phase 3) ──────────────────────────────────────────────────────
def test_engines_list_smoke(client):
    r = client.get("/engines")
    assert r.status_code == 200
    body = r.json()
    assert set(body) == {"tts", "asr", "llm"}
    for family in ("tts", "asr", "llm"):
        assert "active" in body[family]
        assert "backends" in body[family]
        assert isinstance(body[family]["backends"], list)


def test_engines_tts_lists_all_backends(client):
    r = client.get("/engines/tts")
    assert r.status_code == 200
    ids = {b["id"] for b in r.json()["backends"]}
    assert {"omnivoice", "voxcpm2", "moss-tts-nano"}.issubset(ids)


def test_engines_select_refuses_unavailable_backend(client):
    # MOSS-TTS-Nano deps aren't installed on the test host — /engines/select
    # must refuse rather than brick the pipeline with an unavailable pick.
    backends = {b["id"]: b for b in client.get("/engines/tts").json()["backends"]}
    unavailable = next((bid for bid, b in backends.items() if not b["available"]), None)
    if unavailable is None:
        return  # all engines ready on this host — nothing to assert
    r = client.post("/engines/select", json={"family": "tts", "backend_id": unavailable})
    assert r.status_code == 400
    detail = r.json().get("detail", "")
    assert "not ready" in detail or "unavailable" in detail


def test_engines_select_rejects_unknown_family(client):
    r = client.post("/engines/select", json={"family": "xyz", "backend_id": "omnivoice"})
    assert r.status_code == 400


# ── tools (Phase 4.6) ──────────────────────────────────────────────────────
def test_tools_direction_parses(client):
    r = client.post("/tools/direction", json={"text": "urgent and surprised"})
    assert r.status_code == 200
    body = r.json()
    assert "taxonomy" in body
    assert body["instruct_prompt"]
    assert body["rate_bias"] != 1.0


def test_tools_incremental_first_run_everything_stale(client):
    r = client.post("/tools/incremental", json={
        "segments": [{"id": "s1", "text": "hi", "target_lang": "de"}],
    })
    assert r.status_code == 200
    body = r.json()
    assert body["stale"] == ["s1"]


def test_tools_rate_fit_respects_tolerance(client):
    # ~15 chars/s en → 15-char slot exactly.
    r = client.post("/tools/rate-fit", json={
        "text": "A" * 15,
        "slot_seconds": 1.0,
        "target_lang": "en",
    })
    assert r.status_code == 200
    assert r.json()["attempts"] == 0


# ── exports ─────────────────────────────────────────────────────────────────
def test_export_history_smoke(client):
    r = client.get("/export/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_export_reveal_rejects_empty_path(client):
    # Validates the rewritten error message reaches the client cleanly.
    r = client.post("/export/reveal", json={"path": ""})
    assert r.status_code == 400
    assert "nothing to reveal" in r.json()["detail"].lower()


# ── generation ──────────────────────────────────────────────────────────────
def test_history_list_smoke(client):
    r = client.get("/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── dub_core ────────────────────────────────────────────────────────────────
def test_dub_history_list_smoke(client):
    r = client.get("/dub/history")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


# ── jobs (Phase 2.1) ────────────────────────────────────────────────────────
def test_jobs_list_smoke(client):
    r = client.get("/jobs")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_jobs_list_filter_active(client):
    r = client.get("/jobs?status=active")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body, list)
    for j in body:
        assert j["status"] in ("pending", "running")


def test_job_get_404(client):
    r = client.get("/jobs/__nonexistent__")
    assert r.status_code == 404
    assert "no such job" in r.json()["detail"].lower()


def test_job_events_404(client):
    r = client.get("/jobs/__nonexistent__/events")
    assert r.status_code == 404


def test_dub_generate_unknown_job(client):
    # Hitting /dub/generate/{id} with a non-existent id should surface the
    # rewritten 404 copy.
    r = client.post("/dub/generate/__nonexistent__", json={
        "segments": [],
        "language": "Auto",
        "language_code": "und",
        "num_step": 16,
        "guidance_scale": 2.0,
        "speed": 1.0,
    })
    assert r.status_code == 404
    assert "re-upload" in r.json()["detail"].lower()
