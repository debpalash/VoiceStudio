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


def test_crashed_owner_releases_the_directory(tmp_path):
    backend = Path(__file__).resolve().parents[1] / 'backend'
    script = ('import os,sys; from core.runtime_owner import DataDirectoryLease; '
              'DataDirectoryLease(sys.argv[1]).__enter__(); os._exit(0)')
    subprocess.run([sys.executable, '-c', script, str(tmp_path)], check=True,
                   env={**os.environ, 'PYTHONPATH': str(backend)})
    with DataDirectoryLease(tmp_path):
        pass


def test_waiting_backend_acquires_after_its_predecessor_exits(tmp_path):
    backend = Path(__file__).resolve().parents[1] / 'backend'
    script = ('import sys,time; from core.runtime_owner import DataDirectoryLease\n'
              'with DataDirectoryLease(sys.argv[1]):\n print("held", flush=True); time.sleep(0.5)')
    holder = subprocess.Popen([sys.executable, '-c', script, str(tmp_path)], stdout=subprocess.PIPE,
                              env={**os.environ, 'PYTHONPATH': str(backend)})
    try:
        assert holder.stdout.readline().strip() == b'held'
        with DataDirectoryLease(tmp_path, timeout=10):
            assert holder.wait(timeout=10) == 0
    finally:
        holder.wait(timeout=10)
