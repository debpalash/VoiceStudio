"""Tests for the export router (`backend/api/routers/exports.py`).

Covers the full route surface — /export, /export/record, /export/history,
/export/reveal — which previously had zero dedicated tests (only two smoke
checks in test_router_smoke.py):

* happy paths: copy-to-destination + history recording, record-only, history
  listing order, reveal (with subprocess mocked so no Finder/Explorer opens);
* the security guards: source traversal (`_safe_source` accepts basenames
  only, resolved inside OUTPUTS_DIR), destination validation (absolute path
  required, parent must exist);
* the regression for the dead `isabs` check: `os.path.realpath()` absolutizes
  a relative destination against the server's cwd, so relative destinations
  used to silently export to a cwd-dependent location instead of the
  documented 400 (fail-before/pass-after: `_safe_destination` now checks
  `isabs` before realpath);
* the access guards on /export/record and /export/history, which previously
  carried no dependency at all: a non-loopback caller is rejected on a desktop
  install, loopback still succeeds, and server mode (Docker / remote backend,
  where the bridge makes every caller look non-loopback) stays reachable;
* error mapping: copy failure → 500, reveal of a missing path → 404,
  reveal spawn failure → 500;
* the mp4 branch: visible watermark disabled → plain copy; ffmpeg overlay
  failure → plain-copy fallback (the user still gets their file).
"""
import os
import json
import secrets
import subprocess

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


@pytest.fixture(scope="module")
def client():
    # Same self-sufficient pattern as test_router_smoke.py: lazy import, and
    # seed the schema explicitly because a bare TestClient never runs the app
    # lifespan (where init_db() lives).
    from fastapi.testclient import TestClient
    from main import app
    import core.db

    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture
def outputs_dir(tmp_path, monkeypatch):
    """A throwaway OUTPUTS_DIR wired into the router's frozen import."""
    import api.routers.exports as exports

    out = tmp_path / "outputs"
    out.mkdir()
    monkeypatch.setattr(exports, "OUTPUTS_DIR", str(out))
    return out


@pytest.fixture
def authorize_destination(tmp_path, monkeypatch):
    """Mint the same one-shot file capability written by the Tauri process."""
    from core import path_authorization

    auth_dir = tmp_path / "authorizations"
    auth_dir.mkdir()
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))

    def authorize(path):
        token = secrets.token_hex(32)
        (auth_dir / f"{token}.json").write_text(
            json.dumps({"token": token, "kind": "dub_export", "path": str(path)}),
            encoding="utf-8",
        )
        return token

    return authorize


@pytest.fixture
def server_data_dir(tmp_path, monkeypatch):
    import api.routers.exports as exports

    root = tmp_path / "server-data"
    root.mkdir()
    monkeypatch.setattr(exports, "DATA_DIR", str(root))
    return root


def _make_source(outputs_dir, name="clip.wav", data=b"RIFFxxxxWAVE-test-audio"):
    p = outputs_dir / name
    p.write_bytes(data)
    return p


# ── route shapes ─────────────────────────────────────────────────────────────
def test_route_shapes(client):
    from main import app

    routes = {r.path: r.methods for r in app.routes if hasattr(r, "methods")}
    assert "POST" in routes["/export"]
    assert "POST" in routes["/export/record"]
    assert "GET" in routes["/export/history"]
    assert "POST" in routes["/export/reveal"]


# ── /export happy path ───────────────────────────────────────────────────────
def test_export_copies_file_and_records_history(client, outputs_dir, tmp_path, authorize_destination):
    src = _make_source(outputs_dir)
    dest_dir = tmp_path / "dest"
    dest_dir.mkdir()
    dest = dest_dir / "exported.wav"

    r = client.post("/export", json={
        "source_filename": "clip.wav",
        "authorization": authorize_destination(dest),
        "mode": "history",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True
    assert len(body["id"]) == 8

    assert dest.read_bytes() == src.read_bytes()

    hist = client.get("/export/history").json()
    entry = next(h for h in hist if h["id"] == body["id"])
    assert entry["filename"] == "clip.wav"
    assert entry["destination_path"] == os.path.realpath(str(dest))
    assert entry["mode"] == "history"


def test_export_never_accepts_http_destination_path(client, outputs_dir, tmp_path):
    _make_source(outputs_dir)
    dest = tmp_path / "attacker-selected.wav"
    response = client.post(
        "/export",
        json={"source_filename": "clip.wav", "destination_path": str(dest)},
    )
    assert response.status_code == 422
    assert not dest.exists()


def test_export_capability_is_one_shot(client, outputs_dir, tmp_path, authorize_destination):
    _make_source(outputs_dir)
    token = authorize_destination(tmp_path / "once.wav")
    payload = {"source_filename": "clip.wav", "authorization": token}
    assert client.post("/export", json=payload).status_code == 200
    retry = client.post("/export", json=payload)
    assert retry.status_code == 403
    assert "expired" in retry.json()["detail"]


def test_export_rejects_remote_caller_before_consuming_capability(
    outputs_dir, tmp_path, authorize_destination
):
    from fastapi.testclient import TestClient
    from main import app

    _make_source(outputs_dir)
    token = authorize_destination(tmp_path / "remote.wav")
    remote = TestClient(app, client=("192.168.1.20", 50000))
    response = remote.post(
        "/export", json={"source_filename": "clip.wav", "authorization": token}
    )
    assert response.status_code == 403
    # Rejection happened before the route consumed native authority.
    local = TestClient(app, client=("127.0.0.1", 50000))
    assert local.post(
        "/export", json={"source_filename": "clip.wav", "authorization": token}
    ).status_code == 200


def test_export_destination_may_be_an_existing_directory(client, outputs_dir, tmp_path, authorize_destination):
    # shutil.copy2 into a directory keeps the source basename.
    _make_source(outputs_dir, name="take2.wav")
    dest_dir = tmp_path / "outbox"
    dest_dir.mkdir()

    r = client.post("/export", json={
        "source_filename": "take2.wav",
        "authorization": authorize_destination(dest_dir),
    })
    assert r.status_code == 200
    assert (dest_dir / "take2.wav").is_file()


# ── /export source guards ────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_name", [
    "../../../etc/passwd",       # classic traversal
    "..",                        # bare parent hop
    "sub/dir.wav",               # any separator is rejected (basename-only)
    "/etc/passwd",               # absolute path
    "",                          # empty
])
def test_export_rejects_non_basename_sources(client, outputs_dir, tmp_path, bad_name, authorize_destination):
    r = client.post("/export", json={
        "source_filename": bad_name,
        "authorization": authorize_destination(tmp_path),
    })
    assert r.status_code == 400
    assert "unexpected name" in r.json()["detail"]


def test_export_404_when_source_gone(client, outputs_dir, tmp_path, authorize_destination):
    r = client.post("/export", json={
        "source_filename": "never-generated.wav",
        "authorization": authorize_destination(tmp_path),
    })
    assert r.status_code == 404
    assert "isn't on disk" in r.json()["detail"]


def test_export_symlink_inside_outputs_pointing_outside_is_rejected(
    client, outputs_dir, tmp_path, authorize_destination, symlink_or_skip
):
    # A symlink planted in OUTPUTS_DIR must not let /export read arbitrary
    # files: realpath resolves it outside the root, failing containment.
    secret = tmp_path / "secret.txt"
    secret.write_bytes(b"credentials")
    symlink_or_skip(outputs_dir / "innocent.wav", secret)

    r = client.post("/export", json={
        "source_filename": "innocent.wav",
        "authorization": authorize_destination(tmp_path / "out.wav"),
    })
    assert r.status_code == 404


# ── /export destination guards ───────────────────────────────────────────────
@pytest.mark.parametrize("bad_dest", ["", "   "])
def test_export_rejects_empty_destination(client, outputs_dir, bad_dest, authorize_destination):
    _make_source(outputs_dir)
    r = client.post("/export", json={
        "source_filename": "clip.wav",
        "authorization": authorize_destination(bad_dest),
    })
    assert r.status_code == 400
    assert "destination is invalid" in r.json()["detail"]


def test_export_rejects_relative_destination_even_when_cwd_resolvable(
    client, outputs_dir, tmp_path, monkeypatch, authorize_destination
):
    """Regression: the isabs check ran on realpath()'s output, which is
    always absolute — so a relative destination fell through and exported to
    a directory relative to the server's cwd. Pass a relative path whose
    parent EXISTS under cwd: before the fix this returned 200 and wrote a
    cwd-dependent file; now it's the documented 400."""
    _make_source(outputs_dir)
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel_out").mkdir()

    r = client.post("/export", json={
        "source_filename": "clip.wav",
        "authorization": authorize_destination(os.path.join("rel_out", "exported.wav")),
    })
    assert r.status_code == 400
    assert "destination is invalid" in r.json()["detail"]
    assert not (tmp_path / "rel_out" / "exported.wav").exists()


def test_export_rejects_destination_with_missing_parent(client, outputs_dir, tmp_path, authorize_destination):
    _make_source(outputs_dir)
    r = client.post("/export", json={
        "source_filename": "clip.wav",
        "authorization": authorize_destination(tmp_path / "no-such-dir" / "out.wav"),
    })
    assert r.status_code == 400
    assert "doesn't exist yet" in r.json()["detail"]


def test_export_copy_failure_maps_to_500(client, outputs_dir, tmp_path, monkeypatch, authorize_destination):
    import api.routers.exports as exports

    _make_source(outputs_dir)

    def _boom(src, dest):
        raise OSError("disk full")

    monkeypatch.setattr(exports.shutil, "copy2", _boom)
    r = client.post("/export", json={
        "source_filename": "clip.wav",
        "authorization": authorize_destination(tmp_path / "out.wav"),
    })
    assert r.status_code == 500
    assert "disk full" in r.json()["detail"]


# ── /export mp4 watermark branch ─────────────────────────────────────────────
def test_export_mp4_plain_copy_when_visible_watermark_disabled(
    client, outputs_dir, tmp_path, monkeypatch, authorize_destination
):
    from services import watermark

    src = _make_source(outputs_dir, name="dub.mp4", data=b"\x00\x00\x00\x1cftyp-fake-mp4")
    monkeypatch.setattr(watermark, "is_visible_video_enabled", lambda: False)

    called = []
    monkeypatch.setattr(
        subprocess, "run",
        lambda *a, **k: called.append(a) or (_ for _ in ()).throw(AssertionError("ffmpeg must not run")),
    )

    dest = tmp_path / "dub-export.mp4"
    r = client.post("/export", json={
        "source_filename": "dub.mp4",
        "authorization": authorize_destination(dest),
    })
    assert r.status_code == 200
    assert dest.read_bytes() == src.read_bytes()
    assert not called


def test_export_mp4_overlay_spawns_the_resolved_ffmpeg(
    client, outputs_dir, tmp_path, monkeypatch, authorize_destination
):
    """The overlay must run the binary `find_ffmpeg()` resolved, not the bare
    name. imageio-ffmpeg — the app's default source — ships its binary as
    `ffmpeg-<platform>-v<version>`, so spawning "ffmpeg" raised
    FileNotFoundError and the watermark was silently dropped from the export.
    """
    from services import watermark
    from services import ffmpeg_utils

    _make_source(outputs_dir, name="dub.mp4", data=b"\x00\x00\x00\x1cftyp-fake-mp4")
    bundled = str(tmp_path / "bundle" / "ffmpeg-linux-x86_64-v7.1")
    monkeypatch.setattr(watermark, "is_visible_video_enabled", lambda: True)
    monkeypatch.setattr(
        watermark, "get_ffmpeg_overlay_args", lambda logo: ["-filter_complex", "overlay"]
    )
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: bundled)

    spawned = []
    dest = tmp_path / "dub-export.mp4"

    def _record(cmd, **kw):
        spawned.append(cmd)
        dest.write_bytes(b"overlaid")
        return subprocess.CompletedProcess(cmd, 0, b"", b"")

    monkeypatch.setattr(subprocess, "run", _record)

    r = client.post("/export", json={
        "source_filename": "dub.mp4",
        "authorization": authorize_destination(dest),
    })
    assert r.status_code == 200
    assert spawned and spawned[0][0] == bundled
    assert dest.read_bytes() == b"overlaid"


def test_export_mp4_plain_copies_when_no_ffmpeg_resolves(
    client, outputs_dir, tmp_path, monkeypatch, authorize_destination
):
    """Nothing resolved → the user still gets their file, and no spawn is
    attempted just to watch it fail."""
    from services import watermark
    from services import ffmpeg_utils

    src = _make_source(outputs_dir, name="dub.mp4", data=b"\x00\x00\x00\x1cftyp-fake-mp4")
    monkeypatch.setattr(watermark, "is_visible_video_enabled", lambda: True)
    monkeypatch.setattr(
        watermark, "get_ffmpeg_overlay_args", lambda logo: ["-filter_complex", "overlay"]
    )
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: None)

    spawned = []
    monkeypatch.setattr(subprocess, "run", lambda cmd, **kw: spawned.append(cmd))

    dest = tmp_path / "dub-export.mp4"
    r = client.post("/export", json={
        "source_filename": "dub.mp4",
        "authorization": authorize_destination(dest),
    })
    assert r.status_code == 200
    assert dest.read_bytes() == src.read_bytes()
    assert not spawned


def test_export_mp4_falls_back_to_plain_copy_when_ffmpeg_fails(
    client, outputs_dir, tmp_path, monkeypatch, authorize_destination
):
    from services import watermark
    from services import ffmpeg_utils

    src = _make_source(outputs_dir, name="dub.mp4", data=b"\x00\x00\x00\x1cftyp-fake-mp4")
    monkeypatch.setattr(watermark, "is_visible_video_enabled", lambda: True)
    monkeypatch.setattr(
        watermark, "get_ffmpeg_overlay_args", lambda logo: ["-filter_complex", "overlay"]
    )
    # Pinned so the real resolver never runs its `-version` probe through the
    # stub below (which would cache the probe's failure for the whole session).
    monkeypatch.setattr(ffmpeg_utils, "find_ffmpeg", lambda: "/opt/ffmpeg")

    def _ffmpeg_dies(cmd, **kw):
        raise subprocess.CalledProcessError(1, cmd)

    monkeypatch.setattr(subprocess, "run", _ffmpeg_dies)

    dest = tmp_path / "dub-export.mp4"
    r = client.post("/export", json={
        "source_filename": "dub.mp4",
        "authorization": authorize_destination(dest),
    })
    # The user still gets their file (unwatermarked) — never a hard failure.
    assert r.status_code == 200
    assert dest.read_bytes() == src.read_bytes()


# ── /export/record ───────────────────────────────────────────────────────────
def test_record_export_writes_history_row(client):
    r = client.post("/export/record", json={
        "filename": "narration.wav",
        "destination_path": "~/Downloads",
        "mode": "file",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["success"] is True

    hist = client.get("/export/history").json()
    entry = next(h for h in hist if h["id"] == body["id"])
    assert entry["filename"] == "narration.wav"
    assert entry["destination_path"] == "~/Downloads"
    assert entry["mode"] == "file"


def test_record_export_defaults(client):
    # ExportRecordRequest defaults: destination "~/Downloads", mode "file".
    r = client.post("/export/record", json={"filename": "only-name.wav"})
    assert r.status_code == 200
    hist = client.get("/export/history").json()
    entry = next(h for h in hist if h["id"] == r.json()["id"])
    assert entry["destination_path"] == "~/Downloads"
    assert entry["mode"] == "file"


# ── /export/history ──────────────────────────────────────────────────────────
def test_history_is_newest_first(client):
    a = client.post("/export/record", json={"filename": "older.wav"}).json()["id"]
    b = client.post("/export/record", json={"filename": "newer.wav"}).json()["id"]
    hist = client.get("/export/history").json()
    ids = [h["id"] for h in hist]
    assert ids.index(b) < ids.index(a)
    assert len(hist) <= 50


# ── access guards on the history routes ──────────────────────────────────────
@pytest.fixture(scope="module")
def lan_client():
    """A caller that is NOT on the loopback interface — any other host on the
    same network as a desktop install."""
    from fastapi.testclient import TestClient
    from main import app
    import core.db

    core.db.init_db()
    return TestClient(app, client=("192.168.1.50", 50000))


_HISTORY_ROUTES = [
    ("post", "/export/record", {"filename": "regression.wav"}),
    ("get", "/export/history", None),
]


def _call(test_client, method, path, payload):
    """Issue one parametrised request; `payload` is None for the GET route."""
    send = getattr(test_client, method)
    return send(path) if payload is None else send(path, json=payload)


@pytest.mark.parametrize(("method", "path", "payload"), _HISTORY_ROUTES)
def test_history_routes_reject_non_local_callers(lan_client, method, path, payload):
    """Fail-before/pass-after: these two routes carried NO dependency at all.

    Every sibling on this router was gated, these two were not. In the default
    desktop deployment both auth middlewares are inert — `NetworkAccessMiddleware`
    needs a share PIN and `BearerKeyMiddleware` needs `OMNIVOICE_API_KEY`, and a
    desktop install sets neither — so nothing else stood in the way: any host on
    the LAN could forge rows into the user's export history, and read it back.
    The rows carry absolute destination paths, so the GET also leaked the shape
    of the user's filesystem.
    """
    r = _call(lan_client, method, path, payload)
    assert r.status_code == 403
    assert r.json()["detail"] == "loopback origin required"


@pytest.mark.parametrize(("method", "path", "payload"), _HISTORY_ROUTES)
def test_history_routes_allow_loopback(client, method, path, payload):
    """The guard must not lock out the desktop app it is protecting."""
    assert _call(client, method, path, payload).status_code == 200


@pytest.mark.parametrize(("method", "path", "payload"), _HISTORY_ROUTES)
def test_history_routes_stay_reachable_in_server_mode(lan_client, monkeypatch, method, path, payload):
    """An Electron client talking to a remote backend must keep its history.

    Docker's bridge NAT rewrites `client.host` to the gateway, so under
    `OMNIVOICE_SERVER_MODE=1` *every* request looks non-loopback. Gating these
    two with `require_loopback` would 403 the history write that follows a
    successful save: the file lands on disk but never appears in the UI, and the
    history list comes back empty. `require_local` is the consumption-tier gate
    — a no-op in server mode — so the remote deployment keeps working, with
    exposure still governed by the operator's port mapping plus the optional PIN
    or API key.
    """
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    assert _call(lan_client, method, path, payload).status_code == 200


# ── /export/reveal ───────────────────────────────────────────────────────────
@pytest.mark.parametrize("bad_path", ["", "   "])
def test_reveal_rejects_empty_path(client, bad_path):
    r = client.post("/export/reveal", json={"path": bad_path})
    assert r.status_code == 400
    assert "nothing to reveal" in r.json()["detail"].lower()


def test_reveal_404_when_path_missing(client, server_data_dir):
    r = client.post("/export/reveal", json={"path": str(server_data_dir / "gone.wav")})
    assert r.status_code == 404
    assert "no longer on disk" in r.json()["detail"]


@pytest.mark.parametrize("outside", ["../secret.wav", r"..\secret.wav", r"C:\secret.wav"])
def test_reveal_rejects_posix_windows_and_drive_escapes(client, server_data_dir, outside):
    response = client.post("/export/reveal", json={"path": outside})
    assert response.status_code == 403


def test_reveal_rejects_absolute_and_symlink_escapes(client, server_data_dir, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    secret = outside / "secret.wav"
    secret.write_bytes(b"secret")
    assert client.post("/export/reveal", json={"path": str(secret)}).status_code == 403
    link = server_data_dir / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    assert client.post("/export/reveal", json={"path": str(link / "secret.wav")}).status_code == 403


def test_reveal_file_spawns_file_manager_without_shell(client, server_data_dir, monkeypatch):
    import api.routers.exports as exports

    target = server_data_dir / "show-me.wav"
    target.write_bytes(b"x")

    calls = []

    def _fake_popen(cmd, *a, **kw):
        calls.append((cmd, kw))
        return None

    monkeypatch.setattr(exports.subprocess, "Popen", _fake_popen)
    r = client.post("/export/reveal", json={"path": str(target)})
    assert r.status_code == 200
    assert r.json() == {"success": True}

    assert len(calls) == 1
    cmd, kw = calls[0]
    # List argv, no shell interpolation — the security contract of the route.
    assert isinstance(cmd, list)
    assert kw.get("shell") is not True
    # Whatever the platform's opener is, the target (or its folder) is an arg.
    resolved = os.path.realpath(str(target))
    assert any(resolved in str(part) or os.path.dirname(resolved) in str(part)
               for part in cmd)


def test_reveal_directory_opens_the_folder_itself(client, server_data_dir, monkeypatch):
    import api.routers.exports as exports

    calls = []
    monkeypatch.setattr(
        exports.subprocess, "Popen", lambda cmd, *a, **kw: calls.append(cmd)
    )
    child = server_data_dir / "exports"
    child.mkdir()
    r = client.post("/export/reveal", json={"path": str(child)})
    assert r.status_code == 200
    assert len(calls) == 1
    assert any(os.path.realpath(str(child)) in str(part) for part in calls[0])


def test_reveal_spawn_failure_maps_to_500(client, server_data_dir, monkeypatch):
    import api.routers.exports as exports

    target = server_data_dir / "cursed.wav"
    target.write_bytes(b"x")

    def _no_opener(*a, **kw):
        raise OSError("no file manager available")

    monkeypatch.setattr(exports.subprocess, "Popen", _no_opener)
    r = client.post("/export/reveal", json={"path": str(target)})
    assert r.status_code == 500
    assert "no file manager" in r.json()["detail"]
