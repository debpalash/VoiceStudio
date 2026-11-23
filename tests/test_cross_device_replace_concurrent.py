"""Overlapping cross-device replacements must stage independent copies."""

from concurrent.futures import ThreadPoolExecutor
import errno
import threading


def test_concurrent_cross_device_replace_uses_private_staging(tmp_path, monkeypatch):
    from utils import fsops

    source_a = tmp_path / "a.wav"
    source_b = tmp_path / "b.wav"
    source_a.write_bytes(b"audio-a")
    source_b.write_bytes(b"audio-b")
    target = tmp_path / "target.wav"
    staged = []
    barrier = threading.Barrier(2)
    replace = fsops.os.replace

    def cross_device(source, destination):
        if str(source) in {str(source_a), str(source_b)}:
            raise OSError(errno.EXDEV, "cross-device move")
        staged.append(str(source))
        barrier.wait(timeout=5)
        replace(source, destination)

    monkeypatch.setattr(fsops.os, "replace", cross_device)
    with ThreadPoolExecutor(max_workers=2) as executor:
        jobs = [
            executor.submit(fsops.safe_replace, str(source), str(target))
            for source in (source_a, source_b)
        ]
        for job in jobs:
            job.result(timeout=10)

    assert len(set(staged)) == 2
    assert target.read_bytes() in {b"audio-a", b"audio-b"}
    assert list(tmp_path.iterdir()) == [target]


def test_cross_device_replace_keeps_the_source_file_mode(tmp_path, monkeypatch):
    import os
    import pytest

    if os.name == "nt":
        pytest.skip("POSIX file modes")
    from utils import fsops

    source = tmp_path / "a.wav"
    source.write_bytes(b"audio")
    os.chmod(source, 0o644)
    target = tmp_path / "target.wav"
    replace = fsops.os.replace

    def cross_device(src, dst):
        if str(src) == str(source):
            raise OSError(errno.EXDEV, "cross-device move")
        replace(src, dst)

    monkeypatch.setattr(fsops.os, "replace", cross_device)
    fsops.safe_replace(str(source), str(target))
    assert target.stat().st_mode & 0o777 == 0o644
