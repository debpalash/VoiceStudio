import errno
import hashlib
import io
import os
from pathlib import Path
import time
import zipfile

import pytest

from engines.audiocpp import bootstrap
from services import audiocpp_runtime_install as installer


def test_managed_runtime_participates_in_binary_resolution(monkeypatch, tmp_path):
    monkeypatch.setattr(bootstrap, "managed_runtime_dir", lambda: tmp_path)

    assert tmp_path / bootstrap.binary_name() in bootstrap._probe_paths()


def test_runtime_status_recognizes_matching_managed_cli(monkeypatch, tmp_path):
    server = tmp_path / bootstrap.binary_name()
    cli = tmp_path / ("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    server.write_bytes(b"server")
    cli.write_bytes(b"cli")
    cli.chmod(0o700)
    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: server)
    monkeypatch.setattr(bootstrap, "managed_runtime_dir", lambda: tmp_path)

    status = installer.status()

    assert status["installed"] is True
    assert status["managed"] is True
    assert status["version"] == bootstrap.VERSION


def test_runtime_status_does_not_guess_user_managed_version(monkeypatch, tmp_path):
    user_dir = tmp_path / "user-runtime"
    managed_dir = tmp_path / "managed-runtime"
    user_dir.mkdir()
    server = user_dir / bootstrap.binary_name()
    cli = user_dir / ("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    server.write_bytes(b"server")
    cli.write_bytes(b"cli")
    cli.chmod(0o700)
    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: server)
    monkeypatch.setattr(bootstrap, "managed_runtime_dir", lambda: managed_dir)

    status = installer.status()

    assert status["installed"] is True
    assert status["managed"] is False
    assert status["version"] is None


def _legacy_runtime(monkeypatch, tmp_path):
    """An app-managed runtime from the previous pin, current pin absent."""
    legacy_dir = tmp_path / "audio-cpp" / "0.7.4" / bootstrap.platform_slug()
    legacy_dir.mkdir(parents=True)
    server = legacy_dir / bootstrap.binary_name()
    cli = legacy_dir / ("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    for path in (server, cli):
        path.write_bytes(b"legacy")
        path.chmod(0o700)
    monkeypatch.delenv(bootstrap.BIN_ENV, raising=False)
    monkeypatch.delenv(bootstrap.DIR_ENV, raising=False)
    monkeypatch.setattr(bootstrap, "_PKG_BIN_DIR", tmp_path / "no-pkg-bin")
    monkeypatch.setattr(
        bootstrap, "managed_runtime_dir", lambda: tmp_path / "audio-cpp" / "current"
    )
    monkeypatch.setattr(bootstrap, "legacy_runtime_dirs", lambda: [legacy_dir])
    monkeypatch.setattr(bootstrap, "LEGACY_VERSIONS", ("v0.7.4",))
    return server


def test_previous_pin_keeps_existing_engines_running_after_a_bump(monkeypatch, tmp_path):
    # #3129: bumping VERSION moves the managed directory. A user's working
    # v0.7.4 runtime must keep serving installed models until they update.
    server = _legacy_runtime(monkeypatch, tmp_path)

    assert bootstrap.resolve_server_binary() == server
    assert bootstrap.is_installed() is True


def test_previous_pin_is_offered_as_an_update(monkeypatch, tmp_path):
    _legacy_runtime(monkeypatch, tmp_path)

    status = installer.status()

    assert status["installed"] is False
    assert status["legacy_version"] == "v0.7.4"
    assert status["managed"] is False


def test_current_pin_wins_over_the_previous_pin(monkeypatch, tmp_path):
    _legacy_runtime(monkeypatch, tmp_path)
    current = tmp_path / "audio-cpp" / "current"
    current.mkdir()
    (current / bootstrap.binary_name()).write_bytes(b"current")
    (current / bootstrap.binary_name()).chmod(0o700)

    assert bootstrap.resolve_server_binary() == current / bootstrap.binary_name()
    assert bootstrap.legacy_runtime_version(bootstrap.resolve_server_binary()) is None


def test_runtime_download_verifies_size_and_digest(monkeypatch, tmp_path):
    payload = b"checksummed runtime"

    class Response(io.BytesIO):
        headers = {"Content-Length": str(len(payload))}

    monkeypatch.setattr(
        installer.urllib.request,
        "urlopen",
        lambda *_args, **_kwargs: Response(payload),
    )
    destination = tmp_path / "runtime.zip"

    installer._download(
        "https://github.com/example/runtime.zip",
        destination,
        hashlib.sha256(payload).hexdigest(),
        len(payload),
    )

    assert destination.read_bytes() == payload


def test_runtime_zip_rejects_parent_traversal(tmp_path):
    archive = tmp_path / "runtime.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.exe", b"unsafe")

    with pytest.raises(RuntimeError, match="unsafe path"):
        installer._extract(archive, tmp_path / "target")

    assert not (tmp_path / "outside.exe").exists()


def test_pinned_runtime_assets_have_published_sizes_and_sha256():
    assert set(bootstrap._ASSETS) == set(bootstrap._ASSET_SIZES)
    for slug, (filename, digest) in bootstrap._ASSETS.items():
        assert filename.startswith(f"audio-{bootstrap.VERSION}-bin-")
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        assert bootstrap._ASSET_SIZES[slug] > 20_000_000


def test_runtime_pin_supports_drama3_and_keeps_the_previous_pin_as_fallback():
    # fish_drama3 first ships in v0.9.3 (#3129).
    version = tuple(int(part) for part in bootstrap.VERSION.removeprefix("v").split("."))
    assert version >= (0, 9, 3)
    assert bootstrap.VERSION not in bootstrap.LEGACY_VERSIONS
    assert "v0.7.4" in bootstrap.LEGACY_VERSIONS


def test_documented_runtime_checksums_match_the_pins():
    doc = (Path(__file__).resolve().parents[1] / "docs" / "engines" / "audio-cpp.md").read_text(
        encoding="utf-8"
    )
    assert f"releases/tag/{bootstrap.VERSION})" in doc
    for filename, digest in bootstrap._ASSETS.values():
        assert f"| `{filename}` | `{digest}` |" in doc


def test_failed_self_check_reports_clearly_and_keeps_the_old_runtime(monkeypatch, tmp_path):
    target = _stub_release(monkeypatch, tmp_path)
    target.mkdir(parents=True)
    (target / "previous").write_bytes(b"kept")

    class FailedProbe:
        returncode = 3

    monkeypatch.setattr(installer.subprocess, "run", lambda *_a, **_k: FailedProbe())
    monkeypatch.setattr(installer, "status", lambda: {"installed": False, "supported": True})
    installer.reset_job_for_tests()

    installer.start_install(wait=True)

    job = installer._snapshot()
    assert job["state"] == "error"
    assert f"{bootstrap.VERSION} runtime failed its self-check" in job["error"]
    assert "exit code 3" in job["error"]
    assert (target / "previous").read_bytes() == b"kept"
    installer.reset_job_for_tests()


def test_self_check_that_cannot_start_is_reported(monkeypatch, tmp_path):
    _stub_release(monkeypatch, tmp_path)

    def run(*_args, **_kwargs):
        raise OSError(errno.ENOEXEC, "Exec format error")

    monkeypatch.setattr(installer.subprocess, "run", run)

    with pytest.raises(installer.RuntimeSelfCheckError, match="could not start: OSError"):
        installer._install()


def _stub_release(monkeypatch, tmp_path):
    """Point the installer at a local zip holding the server and CLI."""
    target = tmp_path / "engines" / "audio-cpp" / bootstrap.VERSION / "runtime"
    cli_name = "audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli"
    monkeypatch.setattr(bootstrap, "default_asset", lambda: ("runtime.zip", "0" * 64))
    monkeypatch.setattr(bootstrap, "default_asset_size", lambda: 1)
    monkeypatch.setattr(bootstrap, "managed_runtime_dir", lambda: target)

    def download(_url, destination, _digest, _size):
        with zipfile.ZipFile(destination, "w") as bundle:
            bundle.writestr(f"bin/{bootstrap.binary_name()}", b"server")
            bundle.writestr(f"bin/{cli_name}", b"cli")

    class Probe:
        returncode = 0

    monkeypatch.setattr(installer, "_download", download)
    monkeypatch.setattr(installer.subprocess, "run", lambda *_a, **_k: Probe())
    monkeypatch.setattr(time, "sleep", lambda _seconds: None)
    return target


def _scanner_lock(path):
    error = PermissionError(errno.EACCES, "Access is denied", str(path))
    error.winerror = 5
    return error


def test_install_waits_out_a_scanner_lock_on_the_probed_runtime(monkeypatch, tmp_path):
    # #2685: right after the device probe runs the new binary, Windows
    # antivirus holds it, and publishing the folder failed with WinError 5.
    target = _stub_release(monkeypatch, tmp_path)
    original_replace = os.replace
    attempts = []

    def replace(src, dst):
        if Path(dst) == target:
            attempts.append(dst)
            if len(attempts) < 3:
                raise _scanner_lock(src)
        return original_replace(src, dst)

    monkeypatch.setattr(installer.os, "replace", replace)

    installer._install()

    assert len(attempts) == 3
    assert (target / bootstrap.binary_name()).read_bytes() == b"server"


def test_install_still_reports_a_lock_that_never_clears(monkeypatch, tmp_path):
    target = _stub_release(monkeypatch, tmp_path)
    attempts = []

    def replace(src, dst):
        attempts.append(dst)
        raise _scanner_lock(src)

    monkeypatch.setattr(installer.os, "replace", replace)

    with pytest.raises(PermissionError):
        installer._install()

    assert 1 < len(attempts) <= 10
    assert not target.exists()


def test_install_does_not_retry_errors_that_are_not_locks(monkeypatch, tmp_path):
    _stub_release(monkeypatch, tmp_path)
    attempts = []

    def replace(src, dst):
        attempts.append(dst)
        raise OSError(errno.ENOSPC, "No space left on device")

    monkeypatch.setattr(installer.os, "replace", replace)

    with pytest.raises(OSError, match="No space"):
        installer._install()

    assert len(attempts) == 1
