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
        assert bootstrap.VERSION.removeprefix("v") in filename
        assert len(digest) == 64 and set(digest) <= set("0123456789abcdef")
        assert bootstrap._ASSET_SIZES[slug] > 20_000_000


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
