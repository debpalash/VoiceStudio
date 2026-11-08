"""Tests for services.media_tools + the /media-tools router.

The media engine (ffmpeg/ffprobe) is an internal, self-provisioning
dependency — these tests pin the contract: origin classification, checksum
+ probe validation on acquisition, override persistence via the existing
env-prefs convention, and the yt-dlp overlay update/restore cycle. All
network I/O is faked; no test downloads anything.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
import time
import zipfile
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def mt(monkeypatch, tmp_path):
    """media_tools with its filesystem + prefs redirected into tmp_path and
    op-state reset (module state is process-global)."""
    import core.prefs as prefs
    import services.media_tools as mt_mod

    original_path = os.environ.get("PATH")
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.setattr(mt_mod, "media_tools_dir", lambda: str(tmp_path / "media_tools"))
    auth_dir = tmp_path / "path-authorizations"
    auth_dir.mkdir()
    from core import path_authorization
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))
    for op in mt_mod._ops.values():
        op.update(state="idle", progress=0.0, error=None)
    mt_mod._version_cache.clear()
    # Never let a test inherit a real user override.
    _OVERRIDE_KEYS = ("FFMPEG_PATH", "FFPROBE_PATH", "OMNIVOICE_FFPROBE_PATH")
    for key in _OVERRIDE_KEYS:
        monkeypatch.delenv(key, raising=False)
    yield mt_mod
    # set_custom_path()/use_system() write os.environ directly (that's their
    # production contract), and monkeypatch.delenv on an *unset* key records
    # nothing to restore — so without this, a test's fake FFMPEG_PATH leaks
    # into later suites and poisons find_ffmpeg() for real-ffmpeg tests
    # (test_pitch_stretch_async was the victim). Explicitly drop them.
    for key in _OVERRIDE_KEYS:
        os.environ.pop(key, None)
    if original_path is None:
        os.environ.pop("PATH", None)
    else:
        os.environ["PATH"] = original_path


def _client():
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


def _make_zip(names) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for name in names:
            zf.writestr(name, "#!/bin/sh\necho fake\n")
    return buf.getvalue()


class _FakeResponse(io.BytesIO):
    def __init__(self, payload: bytes):
        super().__init__(payload)
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


# ── status / origin classification ──────────────────────────────────────────

def test_status_shape(mt):
    st = mt.status()
    assert set(st) >= {"ready", "tools", "ops", "platform_key"}
    assert set(st["tools"]) == {"ffmpeg", "ffprobe", "ytdlp"}
    for tool in ("ffmpeg", "ffprobe"):
        assert set(st["tools"][tool]) >= {"tool", "ok", "path", "version", "origin"}
    assert set(st["ops"]) == {"acquire", "ytdlp_update"}
    assert isinstance(st["ready"], bool)


def test_origin_bundled_for_acquired_and_imageio_paths(mt):
    acquired = os.path.join(mt.bundled_dir(), "ffmpeg")
    assert mt._classify_origin("ffmpeg", acquired) == "bundled"
    pkg = mt._imageio_pkg_dir()
    if pkg:  # venv ships imageio-ffmpeg
        assert mt._classify_origin("ffmpeg", os.path.join(pkg, "binaries", "ffmpeg")) == "bundled"


def test_origin_system_when_no_override(mt):
    assert mt._classify_origin("ffmpeg", "/usr/bin/ffmpeg") == "system"


def test_origin_custom_vs_sidecar_disambiguated_by_pref(mt, monkeypatch):
    import core.prefs as prefs
    path = "/some/where/ffmpeg"
    monkeypatch.setenv("FFMPEG_PATH", path)
    # Env set by the Tauri host at spawn (no pref) → sidecar.
    assert mt._classify_origin("ffmpeg", path) == "sidecar"
    # Same env var persisted through the Settings override → custom.
    prefs.set_("env.FFMPEG_PATH", path)
    assert mt._classify_origin("ffmpeg", path) == "custom"


def test_ytdlp_reports_module_version_without_binary(mt):
    st = mt._ytdlp_status()
    # yt-dlp is a locked module — always importable in a healthy install.
    assert st["ok"] is True
    assert st["origin"] == "bundled"
    assert st["version"]


# ── download validation ─────────────────────────────────────────────────────

def test_download_rejects_checksum_mismatch(mt, tmp_path):
    payload = b"not the pinned bytes"
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with pytest.raises(RuntimeError, match="checksum"):
            mt._download("https://example.test/x.zip", str(tmp_path / "x.zip"),
                         hashlib.sha256(b"something else").hexdigest(),
                         len(payload), op="acquire")


def test_download_rejects_size_mismatch(mt, tmp_path):
    payload = b"abc"
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        with pytest.raises(RuntimeError, match="size"):
            mt._download("https://example.test/x.zip", str(tmp_path / "x.zip"),
                         hashlib.sha256(payload).hexdigest(), 9999, op="acquire")


def test_download_refuses_plain_http(mt, tmp_path):
    with pytest.raises(ValueError, match="https"):
        mt._download("http://example.test/x.zip", str(tmp_path / "x.zip"), "0" * 64, 1, op="acquire")


# ── acquisition ─────────────────────────────────────────────────────────────

def _patched_bundle(mt, monkeypatch, payload: bytes):
    monkeypatch.setattr(mt, "_expected_bundle", lambda: (
        "https://example.test/bundle.zip",
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    ))


def test_acquire_installs_when_checksum_and_probe_pass(mt, monkeypatch):
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}", f"plat/{mt._exe('ffprobe')}"])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state["state"] == "done", state
    for tool in ("ffmpeg", "ffprobe"):
        p = mt.bundled_tool_path(tool)
        assert p and os.path.isfile(p)
        if os.name == "posix":
            assert os.access(p, os.X_OK)


def test_acquire_publishes_new_binaries_to_the_live_process(mt, monkeypatch):
    """A first-run background download must become usable without restart."""
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}", f"plat/{mt._exe('ffprobe')}"])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    published = []
    monkeypatch.setattr(mt, "_publish_bundled_on_path", lambda: published.append(True))

    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        assert mt.acquire_bundled(wait=True)["state"] == "done"

    assert published == [True]


def test_publish_bundled_on_path_prepends_once(mt, monkeypatch):
    monkeypatch.setenv("PATH", os.pathsep.join(["system-a", "system-b"]))

    mt._publish_bundled_on_path()
    mt._publish_bundled_on_path()

    entries = os.environ["PATH"].split(os.pathsep)
    assert entries[0] == os.path.abspath(mt.bundled_dir())
    assert entries.count(entries[0]) == 1
    assert entries[1:] == ["system-a", "system-b"]


def test_acquire_rejects_binary_that_fails_version_probe(mt, monkeypatch):
    """A checksum-valid download whose binary won't run (wrong arch, corrupt)
    must NOT be installed — the WinError-193 class, caught at install time."""
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}", f"plat/{mt._exe('ffprobe')}"])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: False)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state["state"] == "error"
    assert "probe" in (state["error"] or "")
    assert mt.bundled_tool_path("ffmpeg") is None


def test_acquire_errors_on_checksum_mismatch_and_installs_nothing(mt, monkeypatch):
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}", f"plat/{mt._exe('ffprobe')}"])
    monkeypatch.setattr(mt, "_expected_bundle", lambda: (
        "https://example.test/bundle.zip", "0" * 64, len(payload),
    ))
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state["state"] == "error"
    assert "checksum" in state["error"]
    assert mt.bundled_tool_path("ffmpeg") is None


def test_acquire_errors_when_bundle_lacks_ffprobe(mt, monkeypatch):
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}"])  # no ffprobe in the zip
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state["state"] == "error"
    assert "ffprobe" in state["error"]


def test_acquired_bundle_joins_the_resolution_chain(mt, monkeypatch):
    """ffmpeg_utils must pick up an acquired build without env/system help."""
    payload = _make_zip([f"plat/{mt._exe('ffmpeg')}", f"plat/{mt._exe('ffprobe')}"])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    with patch("urllib.request.urlopen", return_value=_FakeResponse(payload)):
        assert mt.acquire_bundled(wait=True)["state"] == "done"

    import services.ffmpeg_utils as fu
    # The service and the chain share bundled_tool_path; only the probe is
    # stubbed (the fake "binaries" are shell stubs, not real ffmpeg).
    monkeypatch.setattr(fu, "_binary_runs", lambda p: True)
    with patch("services.media_tools.bundled_tool_path", side_effect=mt.bundled_tool_path):
        assert fu._acquired_bundled("ffprobe") == mt.bundled_tool_path("ffprobe")


# ── overrides: custom / system / restore ────────────────────────────────────

def test_set_custom_path_persists_via_env_prefs_convention(mt, monkeypatch, tmp_path):
    import core.prefs as prefs
    from services import ffmpeg_utils
    # Both acquisition and resolution validate executables. These shell fixtures
    # are intentionally fake and cannot execute on Windows.
    monkeypatch.setattr(ffmpeg_utils, "_binary_runs", lambda p: True)
    fake = tmp_path / "myffmpeg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)

    info = mt.set_custom_path("ffmpeg", str(fake))
    assert os.environ["FFMPEG_PATH"] == str(fake)
    assert prefs.get("env.FFMPEG_PATH") == str(fake)
    assert info["origin"] == "custom"

    # ffprobe persists under its own (already-PERSISTENT) key.
    fakeprobe = tmp_path / "myffprobe"
    fakeprobe.write_text("#!/bin/sh\n")
    fakeprobe.chmod(0o755)
    mt.set_custom_path("ffprobe", str(fakeprobe))
    assert prefs.get("env.FFPROBE_PATH") == str(fakeprobe)


def test_set_custom_path_rejects_missing_and_non_running_files(mt, tmp_path, monkeypatch):
    with pytest.raises(ValueError, match="not found|File not found"):
        mt.set_custom_path("ffmpeg", str(tmp_path / "nope"))
    bad = tmp_path / "bad"
    bad.write_text("MZ")
    monkeypatch.setattr(mt, "_binary_runs", lambda p: False)
    with pytest.raises(ValueError, match="does not run"):
        mt.set_custom_path("ffmpeg", str(bad))
    assert os.environ.get("FFMPEG_PATH") != str(bad)


def test_set_custom_path_rejects_control_characters(mt):
    with pytest.raises(ValueError, match="control characters"):
        mt.set_custom_path("ffmpeg", "/usr/bin/ff\nmpeg")


def test_use_system_pins_detected_copy_and_404s_when_absent(mt, monkeypatch, tmp_path):
    import core.prefs as prefs
    sysbin = tmp_path / "sys-ffmpeg"
    sysbin.write_text("#!/bin/sh\n")
    sysbin.chmod(0o755)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    monkeypatch.setattr(mt, "_detect_system", lambda tool: str(sysbin))
    info = mt.use_system("ffmpeg")
    assert prefs.get("env.FFMPEG_PATH") == str(sysbin)
    assert info["path"] == str(sysbin) or info["ok"]

    monkeypatch.setattr(mt, "_detect_system", lambda tool: None)
    with pytest.raises(LookupError):
        mt.use_system("ffprobe")


def test_restore_bundled_clears_override_and_is_always_safe(mt, monkeypatch, tmp_path):
    import core.prefs as prefs
    fake = tmp_path / "custom-ffmpeg"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(mt, "_binary_runs", lambda p: True)
    mt.set_custom_path("ffmpeg", str(fake))
    assert prefs.get("env.FFMPEG_PATH")

    acquired = []
    monkeypatch.setattr(mt, "acquire_bundled", lambda wait=False: acquired.append(1) or {"state": "running"})
    mt.restore_bundled("ffmpeg")
    assert prefs.get("env.FFMPEG_PATH") is None
    assert "FFMPEG_PATH" not in os.environ


def test_unknown_tool_rejected_everywhere(mt):
    for fn in (mt.set_custom_path, ):
        with pytest.raises(ValueError):
            fn("nano", "/bin/sh")
    with pytest.raises(ValueError):
        mt.use_system("nano")
    with pytest.raises(ValueError):
        mt.restore_bundled("nano")


# ── yt-dlp overlay ──────────────────────────────────────────────────────────

def _fake_wheel(version: str) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("yt_dlp/__init__.py", "")
        zf.writestr("yt_dlp/version.py", f"__version__ = '{version}'\n")
        zf.writestr(f"yt_dlp-{version}.dist-info/METADATA", "Name: yt-dlp\n")
    return buf.getvalue()


def test_update_ytdlp_builds_overlay_and_records_baseline(mt, monkeypatch):
    import core.prefs as prefs
    wheel = _fake_wheel("2099.01.01")
    monkeypatch.setattr(mt, "_fetch_pypi_ytdlp", lambda: (
        "2099.01.01", "https://example.test/yt_dlp.whl",
        hashlib.sha256(wheel).hexdigest(),
    ))
    with patch("urllib.request.urlopen", return_value=_FakeResponse(wheel)):
        state = mt.update_ytdlp(wait=True)
    assert state["state"] == "done"
    assert state["version"] == "2099.01.01"
    overlay_pkg = os.path.join(mt._ytdlp_overlay_dir(), "yt_dlp")
    assert os.path.isfile(os.path.join(overlay_pkg, "version.py"))
    assert mt._read_ytdlp_version(overlay_pkg) == "2099.01.01"
    # dist-info never lands in the overlay (only the package itself).
    assert not any("dist-info" in n for n in os.listdir(mt._ytdlp_overlay_dir()))
    # The pre-update locked version was recorded as the restore target.
    assert prefs.get("media_tools.ytdlp_baseline")
    st = mt.status()["tools"]["ytdlp"]
    assert st["overlay_version"] == "2099.01.01"


def test_update_ytdlp_rejects_checksum_mismatch(mt, monkeypatch):
    wheel = _fake_wheel("2099.01.01")
    monkeypatch.setattr(mt, "_fetch_pypi_ytdlp", lambda: (
        "2099.01.01", "https://example.test/yt_dlp.whl", "0" * 64,
    ))
    with patch("urllib.request.urlopen", return_value=_FakeResponse(wheel)):
        state = mt.update_ytdlp(wait=True)
    assert state["state"] == "error"
    assert "checksum" in state["error"]
    assert not os.path.isdir(mt._ytdlp_overlay_dir())


def test_restore_ytdlp_deletes_overlay(mt, monkeypatch):
    wheel = _fake_wheel("2099.01.01")
    monkeypatch.setattr(mt, "_fetch_pypi_ytdlp", lambda: (
        "2099.01.01", "https://example.test/yt_dlp.whl",
        hashlib.sha256(wheel).hexdigest(),
    ))
    with patch("urllib.request.urlopen", return_value=_FakeResponse(wheel)):
        mt.update_ytdlp(wait=True)
    assert os.path.isdir(mt._ytdlp_overlay_dir())
    mt.restore_ytdlp()
    assert not os.path.isdir(mt._ytdlp_overlay_dir())


def test_activate_overlay_prepends_sys_path(mt, monkeypatch):
    import sys as _sys
    overlay = mt._ytdlp_overlay_dir()
    os.makedirs(os.path.join(overlay, "yt_dlp"), exist_ok=True)
    monkeypatch.setattr(_sys, "path", list(_sys.path))
    assert mt.activate_ytdlp_overlay() is True
    assert _sys.path[0] == overlay
    # Idempotent.
    assert mt.activate_ytdlp_overlay() is False


def test_ytdlp_invocation_prefers_module_over_path(mt):
    argv, env = mt.ytdlp_invocation()
    import sys as _sys
    assert argv[:3] == [_sys.executable, "-m", "yt_dlp"]
    assert env is None  # no overlay → inherit environment


# ── router ──────────────────────────────────────────────────────────────────

def test_router_status_and_acquire_endpoints(mt, monkeypatch):
    c = _client()
    r = c.get("/media-tools/status")
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"ready", "tools", "ops"}

    monkeypatch.setattr(mt, "acquire_bundled", lambda wait=False: {"state": "running", "progress": 0.0, "error": None})
    r = c.post("/media-tools/acquire")
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_router_custom_path_rejects_raw_http_path(mt):
    c = _client()
    r = c.post("/media-tools/ffmpeg/custom-path", json={"path": "/no/such/binary"})
    assert r.status_code == 422


def test_router_custom_path_consumes_native_authorization(mt, monkeypatch, tmp_path):
    binary = tmp_path / "ffmpeg"
    binary.write_bytes(b"native-authorized")
    monkeypatch.setattr(mt, "_binary_runs", lambda _path: True)
    token = "b" * 64
    from core import path_authorization
    auth_file = os.path.join(path_authorization._AUTH_DIR, f"{token}.json")
    with open(auth_file, "w", encoding="utf-8") as handle:
        json.dump({"token": token, "kind": "ffmpeg", "path": str(binary)}, handle)
    c = _client()
    response = c.post(
        "/media-tools/ffmpeg/custom-path", json={"authorization": token}
    )
    assert response.status_code == 200
    assert os.environ["FFMPEG_PATH"] == str(binary)
    assert not os.path.exists(auth_file)


def test_router_use_system_maps_lookup_to_404(mt, monkeypatch):
    monkeypatch.setattr(mt, "_detect_system", lambda tool: None)
    c = _client()
    r = c.post("/media-tools/ffprobe/use-system")
    assert r.status_code == 404


def test_router_ytdlp_routes_not_shadowed_by_tool_param(mt, monkeypatch):
    """/media-tools/ytdlp/restore must hit the overlay-restore handler, not
    the parametrized {tool}/restore (which would 400 on 'ytdlp')."""
    c = _client()
    r = c.post("/media-tools/ytdlp/restore")
    assert r.status_code == 200
    assert r.json()["tool"] == "yt-dlp"

    monkeypatch.setattr(mt, "update_ytdlp", lambda wait=False: {"state": "running", "progress": 0.0, "error": None, "version": None})
    r = c.post("/media-tools/ytdlp/update")
    assert r.status_code == 200
    assert r.json()["state"] == "running"


def test_router_is_admin_gated(mt):
    from main import app
    c = TestClient(app)  # client.host = 'testclient' → non-loopback
    for method, path in [
        ("get", "/media-tools/status"),
        ("post", "/media-tools/acquire"),
        ("post", "/media-tools/ffmpeg/use-system"),
        ("post", "/media-tools/ytdlp/update"),
    ]:
        r = getattr(c, method)(path)
        assert r.status_code == 403, f"{path} must be admin-only"


def test_server_mode_media_tool_mutations_require_api_key(mt, monkeypatch):
    from main import app

    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.delenv("OMNIVOICE_API_KEY", raising=False)
    remote = TestClient(app, client=("172.17.0.1", 50000))

    assert remote.get("/media-tools/status").status_code == 200
    for path in (
        "/media-tools/acquire",
        "/media-tools/ytdlp/update",
        "/media-tools/ytdlp/restore",
        "/media-tools/ffmpeg/use-system",
    ):
        assert remote.post(path).status_code == 403, path


@pytest.mark.parametrize('locked_operation', ['rename', 'backup'])
def test_acquire_recovers_from_transient_publish_locks(mt, monkeypatch, locked_operation):
    import errno
    import time
    payload = _make_zip([mt._exe('ffmpeg'), mt._exe('ffprobe')])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, '_binary_runs', lambda path: True)
    target = mt.bundled_dir()
    os.makedirs(target, exist_ok=True)  # incomplete earlier acquisition
    calls = []
    original_replace = mt.os.replace

    def replace(src, dst):
        if (dst == target and locked_operation == 'rename') or (src == target and locked_operation == 'backup'):
            calls.append('rename')
            if len(calls) <= 2:
                raise PermissionError(errno.EACCES, 'probe image still mapped', src)
        return original_replace(src, dst)

    monkeypatch.setattr(mt.os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    with patch('urllib.request.urlopen', return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state['state'] == 'done', state
    assert len(calls) >= 3
    assert all(mt.bundled_tool_path(tool) for tool in mt.TOOLS)


def test_acquire_preserves_destination_backup_error(mt, monkeypatch):
    import errno
    import time
    payload = _make_zip([mt._exe('ffmpeg'), mt._exe('ffprobe')])
    _patched_bundle(mt, monkeypatch, payload)
    monkeypatch.setattr(mt, '_binary_runs', lambda path: True)
    target = mt.bundled_dir()
    os.makedirs(target, exist_ok=True)
    calls = []
    original_replace = mt.os.replace

    def replace(src, dst):
        if src == target:
            calls.append(src)
            raise PermissionError(errno.EACCES, 'destination permanently locked', src)
        return original_replace(src, dst)

    monkeypatch.setattr(mt.os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    with patch('urllib.request.urlopen', return_value=_FakeResponse(payload)):
        state = mt.acquire_bundled(wait=True)
    assert state['state'] == 'error'
    assert 'destination permanently locked' in state['error']
    assert 1 < len(calls) <= 10  # bounded, no background retry loop


def test_ytdlp_update_recovers_from_locked_overlay(mt, monkeypatch):
    import errno
    import time
    wheel = _fake_wheel('2099.01.01')
    monkeypatch.setattr(mt, '_fetch_pypi_ytdlp', lambda: (
        '2099.01.01', 'https://example.test/yt_dlp.whl', hashlib.sha256(wheel).hexdigest(),
    ))
    overlay = mt._ytdlp_overlay_dir()
    original_replace = mt.os.replace
    attempts = []

    def replace(src, dst):
        if dst == overlay:
            attempts.append(dst)
            if len(attempts) < 3:
                raise PermissionError(errno.EACCES, 'scanner holds overlay', src)
        return original_replace(src, dst)

    monkeypatch.setattr(mt.os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    with patch('urllib.request.urlopen', return_value=_FakeResponse(wheel)):
        state = mt.update_ytdlp(wait=True)
    assert state['state'] == 'done', state
    assert mt._read_ytdlp_version(os.path.join(overlay, 'yt_dlp')) == '2099.01.01'


def test_restore_ytdlp_reports_locked_overlay_instead_of_claiming_success(mt, monkeypatch):
    import errno
    import time
    overlay = mt._ytdlp_overlay_dir()
    os.makedirs(overlay)
    original_rmtree = mt.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if path == overlay:
            if kwargs.get('ignore_errors'):
                return
            raise PermissionError(errno.EACCES, 'overlay still in use', path)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mt.shutil, 'rmtree', rmtree)
    monkeypatch.setattr(time, 'sleep', lambda seconds: None)
    mt._ops['ytdlp_update'].update(state='done', version='2099.01.01')
    with pytest.raises(PermissionError, match='overlay still in use'):
        mt.restore_ytdlp()
    assert mt._ops['ytdlp_update']['version'] == '2099.01.01'


def test_media_install_does_not_retry_non_lock_errors(mt, monkeypatch):
    import errno
    calls = []

    def full_disk():
        calls.append(True)
        raise OSError(errno.ENOSPC, 'disk full')

    with pytest.raises(OSError, match='disk full'):
        mt._retry_tool_filesystem(full_disk)
    assert calls == [True]


def test_failed_publication_restores_working_directory(mt, monkeypatch, tmp_path):
    import errno
    staged, target = tmp_path / 'staged', tmp_path / 'current'
    staged.mkdir()
    target.mkdir()
    (staged / 'version').write_text('new')
    (target / 'version').write_text('working')
    original_replace = mt.os.replace

    def replace(src, dst):
        if os.fspath(src) == str(staged):
            raise PermissionError(errno.EACCES, 'publication locked')
        return original_replace(src, dst)

    monkeypatch.setattr(mt.os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    with pytest.raises(PermissionError, match='publication locked'):
        mt._install_staged_directory(str(staged), str(target))
    assert (target / 'version').read_text(encoding="utf-8") == 'working'
    assert (staged / 'version').read_text(encoding="utf-8") == 'new'


def test_failed_rollback_keeps_recoverable_backup(mt, monkeypatch, tmp_path):
    import errno
    staged, target = tmp_path / 'staged', tmp_path / 'current'
    staged.mkdir()
    target.mkdir()
    (target / 'version').write_text('working')
    original_replace = mt.os.replace

    def replace(src, dst):
        if os.fspath(dst) == str(target):
            raise PermissionError(errno.EACCES, 'target locked')
        return original_replace(src, dst)

    monkeypatch.setattr(mt.os, 'replace', replace)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    with pytest.raises(OSError, match='Previous installation preserved at'):
        mt._install_staged_directory(str(staged), str(target))
    backups = list(tmp_path.glob('.media-backup-*/previous/version'))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == 'working'


def test_locked_backup_cleanup_does_not_fail_successful_update(mt, monkeypatch, tmp_path):
    import errno
    staged, target = tmp_path / 'staged', tmp_path / 'current'
    staged.mkdir()
    target.mkdir()
    (staged / 'version').write_text('new')
    original_rmtree = mt.shutil.rmtree

    def rmtree(path, *args, **kwargs):
        if '.media-backup-' in str(path):
            raise PermissionError(errno.EACCES, 'old binary still running')
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(mt.shutil, 'rmtree', rmtree)
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    mt._install_staged_directory(str(staged), str(target))
    assert (target / 'version').read_text(encoding="utf-8") == 'new'


def test_restore_ytdlp_propagates_overlay_stat_denial(mt, monkeypatch):
    import errno
    overlay = mt._ytdlp_overlay_dir()
    os.makedirs(overlay)
    original_stat = mt.os.stat
    def stat(path, *args, **kwargs):
        if os.fspath(path) == overlay:
            raise PermissionError(errno.EACCES, 'overlay access denied', path)
        return original_stat(path, *args, **kwargs)
    monkeypatch.setattr(mt.os, 'stat', stat)
    mt._ops['ytdlp_update'].update(state='done', version='2099.01.01')
    with pytest.raises(PermissionError, match='overlay access denied'):
        mt.restore_ytdlp()
    assert mt._ops['ytdlp_update']['version'] == '2099.01.01'
