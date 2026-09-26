"""Regression tests for host-filesystem and persisted-path trust boundaries."""

from __future__ import annotations

import asyncio
import inspect
import json
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException


def _request(host: str | None):
    return SimpleNamespace(client=SimpleNamespace(host=host) if host else None)


@pytest.mark.parametrize("host", ["127.0.0.1", "::1", "localhost"])
def test_native_filesystem_capabilities_allow_true_loopback(host):
    from api.dependencies import require_native_access
    require_native_access(_request(host))


@pytest.mark.parametrize("host", ["172.17.0.1", "192.168.1.4", None])
def test_native_filesystem_capabilities_reject_remote_even_in_server_mode(monkeypatch, host):
    from api.dependencies import require_native_access
    monkeypatch.setenv("OMNIVOICE_SERVER_MODE", "1")
    monkeypatch.setenv("OMNIVOICE_API_KEY", "operator-secret")
    with pytest.raises(HTTPException) as exc:
        require_native_access(_request(host))
    assert exc.value.status_code == 403


@pytest.mark.parametrize(
    "name",
    ["../secret.wav", "folder/voice.wav", r"folder\voice.wav", r"C:\secret.wav", ".", "..", ""],
)
def test_safe_filename_rejects_posix_and_windows_escapes(name):
    from core.path_security import UnsafePath, safe_filename
    with pytest.raises(UnsafePath):
        safe_filename(name)


def test_resolve_within_accepts_relative_and_existing_absolute_paths(tmp_path):
    from core.path_security import resolve_within
    root = tmp_path / "root"
    root.mkdir()
    item = root / "voice.wav"
    assert resolve_within(root, "voice.wav") == item
    assert resolve_within(root, item) == item


def test_stored_subpaths_split_on_both_separator_families():
    """The component split is host-independent.

    Asserted on the splitter itself, not through ``resolve_within``: the
    Python suite runs on Linux only, where ``os.sep`` splitting already
    handled ``/``. A behavioural test would pass here whether or not the
    Windows path is fixed, so it would not guard the regression.
    """
    from core.path_security import _PATH_SEPARATORS
    assert _PATH_SEPARATORS.split("sub/voice.wav") == ["sub", "voice.wav"]
    assert _PATH_SEPARATORS.split(r"sub\voice.wav") == ["sub", "voice.wav"]


@pytest.mark.parametrize("stored_path", ["sub/voice.wav", r"sub\voice.wav"])
def test_resolve_within_reads_a_stored_subpath(tmp_path, stored_path):
    """A persisted sub-path resolves to the same file on Windows and POSIX.

    Rows written on Windows, POSIX, or Docker must resolve identically after
    the same data directory is opened on another supported host.
    """
    from core.path_security import resolve_within
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    assert resolve_within(root, stored_path) == root / "sub" / "voice.wav"


def test_resolve_within_rejects_traversal_through_either_separator(tmp_path):
    """Splitting on both separators must not open a traversal path."""
    from core.path_security import UnsafePath, resolve_within
    root = tmp_path / "root"
    (root / "sub").mkdir(parents=True)
    with pytest.raises(UnsafePath):
        resolve_within(root, "sub/../../secret.wav")


def test_resolve_within_rejects_parent_and_absolute_escape(tmp_path):
    from core.path_security import UnsafePath, resolve_within
    root = tmp_path / "root"
    root.mkdir()
    with pytest.raises(UnsafePath):
        resolve_within(root, "../secret.wav")
    with pytest.raises(UnsafePath):
        resolve_within(root, tmp_path / "secret.wav")
    with pytest.raises(UnsafePath):
        resolve_within(root, r"..\secret.wav")
    with pytest.raises(UnsafePath):
        resolve_within(root, r"C:\secret.wav")


def test_resolve_within_rejects_symlink_escape(tmp_path):
    from core.path_security import UnsafePath, resolve_within
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    # Match the reason, not just the type: when ``/`` was not treated as a
    # separator on Windows this call failed at component validation instead,
    # so the containment check below it was never exercised there.
    with pytest.raises(UnsafePath, match="escapes its allowed root"):
        resolve_within(root, "link/secret.wav")


def test_duration_probe_rejects_absolute_traversal_and_symlink_escapes(tmp_path, monkeypatch):
    from services import ffmpeg_utils

    root = tmp_path / "dub"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret.mp4"
    secret.write_bytes(b"not media")
    monkeypatch.setattr(ffmpeg_utils, "find_ffprobe", lambda: "/should/not/run")
    spawned = []

    async def forbidden_spawn(*args, **kwargs):
        spawned.append((args, kwargs))
        raise AssertionError("rejected media path reached ffprobe")

    monkeypatch.setattr(ffmpeg_utils, "spawn_subprocess", forbidden_spawn)
    for value in (secret, "../outside/secret.mp4", r"..\outside\secret.mp4"):
        assert asyncio.run(ffmpeg_utils.probe_duration(str(value), allowed_root=str(root))) is None
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        return
    assert asyncio.run(
        ffmpeg_utils.probe_duration(str(root / "link" / "secret.mp4"), allowed_root=str(root))
    ) is None
    assert spawned == []


def test_marketplace_filename_cannot_escape_store(tmp_path, monkeypatch):
    from api.routers import marketplace

    monkeypatch.setattr(marketplace, "MARKETPLACE_DIR", tmp_path / "store")
    (tmp_path / "store").mkdir()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(marketplace.install_from_marketplace("../secret.omnivoice"))
    assert exc.value.status_code == 400


def test_marketplace_db_asset_cannot_escape_voices(tmp_path, monkeypatch):
    from api.routers import marketplace

    voices = tmp_path / "voices"
    voices.mkdir()
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"secret")
    monkeypatch.setattr(marketplace, "VOICES_DIR", str(voices))
    with pytest.raises(HTTPException) as exc:
        marketplace._voice_asset(secret)
    assert exc.value.status_code == 400


def test_profile_lock_rejects_history_path_outside_outputs(tmp_path, monkeypatch):
    from api.routers import profiles

    outputs = tmp_path / "outputs"
    voices = tmp_path / "voices"
    outputs.mkdir()
    voices.mkdir()
    secret = tmp_path / "secret.wav"
    secret.write_bytes(b"secret")

    class FakeConnection:
        def execute(self, query, _params):
            if "voice_profiles" in query:
                return SimpleNamespace(fetchone=lambda: {"id": "profile"})
            return SimpleNamespace(
                fetchone=lambda: {"audio_path": str(secret), "text": "private"}
            )

    @contextmanager
    def fake_db_conn():
        yield FakeConnection()

    monkeypatch.setattr(profiles, "OUTPUTS_DIR", str(outputs))
    monkeypatch.setattr(profiles, "VOICES_DIR", str(voices))
    monkeypatch.setattr(profiles, "db_conn", fake_db_conn)
    with pytest.raises(HTTPException) as exc:
        asyncio.run(profiles.lock_profile("profile", history_id="history"))
    assert exc.value.status_code == 400
    assert not any(voices.iterdir())


def test_dub_artifact_rejects_db_path_and_symlink_escapes(tmp_path, monkeypatch):
    from api.routers import dub_export

    root = tmp_path / "dub"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"secret")
    try:
        (root / "link").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    monkeypatch.setattr(dub_export, "DUB_DIR", str(root))

    for value in (outside / "secret.wav", root / "link" / "secret.wav"):
        with pytest.raises(HTTPException) as exc:
            dub_export._dub_artifact(value, "job_123")
        assert exc.value.status_code == 400


def test_dub_artifact_rebases_trusted_path_after_data_relocation(tmp_path, monkeypatch):
    from api.routers import dub_export

    current = tmp_path / "new-data" / "dub_jobs"
    artifact = current / "job_123" / "tracks" / "voice.wav"
    artifact.parent.mkdir(parents=True)
    artifact.write_bytes(b"voice")
    monkeypatch.setattr(dub_export, "DUB_DIR", str(current))

    old_posix = tmp_path / "old-data" / "dub_jobs" / "job_123" / "tracks" / "voice.wav"
    old_windows = r"D:\Old VoiceStudio\dub_jobs\job_123\tracks\voice.wav"
    assert dub_export._dub_artifact(old_posix, "job_123") == str(artifact.resolve())
    assert dub_export._dub_artifact(old_windows, "job_123") == str(artifact.resolve())


def test_dub_artifact_rebase_cannot_cross_job_boundary(tmp_path, monkeypatch):
    from api.routers import dub_export

    current = tmp_path / "new-data" / "dub_jobs"
    other = current / "job_other" / "voice.wav"
    other.parent.mkdir(parents=True)
    other.write_bytes(b"secret")
    monkeypatch.setattr(dub_export, "DUB_DIR", str(current))

    values = [
        other,
        tmp_path / "old-data" / "dub_jobs" / "job_other" / "voice.wav",
        r"D:\Old VoiceStudio\dub_jobs\job_other\voice.wav",
    ]
    for value in values:
        with pytest.raises(HTTPException) as exc:
            dub_export._dub_artifact(value, "job_123")
        assert exc.value.status_code == 400


def test_segment_artifact_discovery_rejects_traversal_and_symlinks(tmp_path, monkeypatch):
    from api.routers import dub_export

    root = tmp_path / "dub"
    job = root / "job_123"
    outside = tmp_path / "outside"
    job.mkdir(parents=True)
    outside.mkdir()
    good = job / "seg_en_7.wav"
    good.write_bytes(b"RIFF")
    secret = outside / "secret.wav"
    secret.write_bytes(b"secret")
    monkeypatch.setattr(dub_export, "DUB_DIR", str(root))

    assert dub_export._existing_segment_artifact("job_123", ["en_7"]) == str(good)
    assert dub_export._existing_segment_artifact("job_123", ["../../secret"]) is None
    link = job / "seg_link.wav"
    try:
        link.symlink_to(secret)
    except OSError:
        return
    assert dub_export._existing_segment_artifact("job_123", ["link"]) is None


def test_dub_export_security_logs_have_fixed_message_shapes():
    source = Path("backend/api/routers/dub_export.py").read_text(encoding="utf-8")
    assert 'logger.warning("onsets cache write failed")' in source
    assert 'logger.warning("dub QC ASR pass timed out")' in source
    assert 'logger.exception("dub QC ASR pass failed")' in source
    assert 'logger.debug("QC event append failed")' in source
    assert 'logger.info("Dub audio mix completed")' in source
    assert 'logger.info("Dub MP3 encoding completed")' in source
    assert 'timed out for %s' not in source
    assert 'onsets cache write failed for %s' not in source


def test_dub_artifact_rebase_rejects_unanchored_traversal_and_symlink(tmp_path, monkeypatch):
    from api.routers import dub_export

    current = tmp_path / "new-data" / "dub_jobs"
    outside = tmp_path / "outside"
    current.mkdir(parents=True)
    outside.mkdir()
    (outside / "secret.wav").write_bytes(b"secret")
    try:
        (current / "job_123").symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip("symlink creation is unavailable on this host")
    monkeypatch.setattr(dub_export, "DUB_DIR", str(current))

    rejected = [
        tmp_path / "old-data" / "other" / "job_123" / "secret.wav",
        str(tmp_path / "old-data" / "dub_jobs" / ".." / "secret.wav"),
        tmp_path / "old-data" / "dub_jobs" / "job_123" / "secret.wav",
        r"D:\old\dub_jobs\..\secret.wav",
    ]
    for value in rejected:
        with pytest.raises(HTTPException) as exc:
            dub_export._dub_artifact(value, "job_123")
        assert exc.value.status_code == 400


def test_dub_native_save_requires_one_shot_tauri_authorization(tmp_path, monkeypatch):
    from api.routers import dub_export
    from core import path_authorization

    auth_dir = tmp_path / "authorizations"
    auth_dir.mkdir()
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))
    token = "a" * 64
    destination = str(tmp_path / "export.wav")
    (auth_dir / f"{token}.json").write_text(
        json.dumps({"token": token, "kind": "dub_export", "path": destination}),
        encoding="utf-8",
    )

    assert dub_export._consume_native_save("") is None
    assert dub_export._consume_native_save(token) == destination
    with pytest.raises(HTTPException) as exc:
        dub_export._consume_native_save(token)
    assert exc.value.status_code == 403


def test_dub_routes_never_accept_an_http_destination_path():
    from api.routers import dub_export

    for route in (
        dub_export.dub_download,
        dub_export.dub_download_audio,
        dub_export.dub_download_mp3,
    ):
        parameters = inspect.signature(route).parameters
        assert "save_path" not in parameters
        assert "save_authorization" in parameters


def test_dub_authorization_survives_validation_failure(tmp_path, monkeypatch):
    """A one-shot save token is consumed only when an artifact is ready to write."""
    from api.routers import dub_export
    from core import path_authorization
    from fastapi.testclient import TestClient
    from main import app

    auth_dir = tmp_path / "authorizations"
    auth_dir.mkdir()
    monkeypatch.setattr(path_authorization, "_AUTH_DIR", str(auth_dir))
    monkeypatch.setattr(dub_export, "_get_job", lambda _job_id: None)
    token = "b" * 64
    capability = auth_dir / f"{token}.json"
    capability.write_text(
        json.dumps({"token": token, "kind": "dub_export", "path": str(tmp_path / "out.wav")}),
        encoding="utf-8",
    )
    response = TestClient(app, client=("127.0.0.1", 50000)).get(
        "/dub/download/missing-job",
        headers={"X-VoiceStudio-Path-Authorization": token},
    )
    assert response.status_code == 404
    assert capability.is_file()


def test_soni_dub_accepts_capabilities_not_raw_paths(monkeypatch):
    from api.routers import sonitranslate
    from core import path_authorization
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        sonitranslate.DubRequest(video_path="/tmp/input.mp4")

    consumed = []
    monkeypatch.setattr(
        path_authorization,
        "consume",
        lambda token, kind: consumed.append((token, kind)) or f"/authorized/{kind}",
    )
    monkeypatch.setattr(
        sonitranslate.soni,
        "dub_video",
        lambda **_kwargs: None,
    )

    async def fake_dub_video(**kwargs):
        return kwargs

    monkeypatch.setattr(sonitranslate.soni, "dub_video", fake_dub_video)
    body = sonitranslate.DubRequest(
        video_authorization="c" * 64,
        output_authorization="d" * 64,
    )
    result = asyncio.run(sonitranslate.sonitranslate_dub(body))
    assert result["video_path"] == "/authorized/soni_input"
    assert result["output_dir"] == "/authorized/soni_output_dir"
    assert consumed == [
        ("c" * 64, "soni_input"),
        ("d" * 64, "soni_output_dir"),
    ]
