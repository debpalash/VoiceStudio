"""One backend per data directory; the kernel releases it with a crashed owner."""
import os
from pathlib import Path
import subprocess
import sys

import pytest
from core.runtime_owner import DataDirectoryBusy, DataDirectoryLease


def test_second_backend_is_refused_with_an_actionable_message(tmp_path):
    with DataDirectoryLease(tmp_path):
        with pytest.raises(DataDirectoryBusy, match='voicestudio stop'):
            with DataDirectoryLease(tmp_path, timeout=0.2):
                pytest.fail('Two backends acquired the same data directory')
    with DataDirectoryLease(tmp_path):
        pass


def _holder(tmp_path):
    """A separate backend process that holds the lock until stdin closes or it is killed."""
    backend = Path(__file__).resolve().parents[1] / 'backend'
    script = ('import sys; from core.runtime_owner import DataDirectoryLease\n'
              'with DataDirectoryLease(sys.argv[1]):\n print("held", flush=True); sys.stdin.read()')
    holder = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], stdin=subprocess.PIPE,
                              stdout=subprocess.PIPE, env={**os.environ, 'PYTHONPATH': str(backend)})
    assert holder.stdout.readline().strip() == b'held'
    return holder


def test_crashed_owner_releases_the_directory(tmp_path):
    holder = _holder(tmp_path)
    try:
        with pytest.raises(DataDirectoryBusy):
            DataDirectoryLease(tmp_path).__enter__()
        holder.kill()  # a crash: no __exit__, no cleanup
        holder.wait(timeout=10)
        with DataDirectoryLease(tmp_path):
            pass
    finally:
        holder.kill()
        holder.wait(timeout=10)


def test_waiting_backend_acquires_after_its_predecessor_exits(tmp_path, monkeypatch):
    import threading
    from core import runtime_owner

    holder = _holder(tmp_path)
    waiting = threading.Event()
    real_sleep = runtime_owner.time.sleep

    def sleep(seconds):
        waiting.set()  # the first attempt failed: the lease is now waiting
        real_sleep(seconds)

    monkeypatch.setattr(runtime_owner.time, 'sleep', sleep)
    acquired = []

    def acquire():
        with DataDirectoryLease(tmp_path, timeout=30):
            acquired.append(True)

    worker = threading.Thread(target=acquire)
    try:
        worker.start()
        assert waiting.wait(timeout=10)
        assert not acquired
        holder.stdin.close()  # the predecessor exits normally
        assert holder.wait(timeout=10) == 0
        worker.join(timeout=30)
        assert acquired == [True]
    finally:
        holder.kill()
        holder.wait(timeout=10)
        worker.join(timeout=30)
