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
