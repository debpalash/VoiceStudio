"""Cancellation snapshots retain subprocesses registered during a kill."""
import subprocess
import sys
import threading

from services import proc_registry as registry


def test_registration_during_native_kill_remains_cancellable():
    entered = threading.Event()
    release = threading.Event()
    job = "registry-overlapping-kill"
    children = []

    class BlockingKill:
        def __init__(self, process):
            self.process = process

        @property
        def returncode(self):
            return self.process.returncode

        def kill(self):
            entered.set()
            assert release.wait(5)
            self.process.kill()

    thread = None
    try:
        first = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        children.append(first)
        registry.register_proc(job, BlockingKill(first))
        thread = threading.Thread(target=registry.kill_job_procs, args=(job,))
        thread.start()
        assert entered.wait(5)
        second = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
        children.append(second)
        registry.register_proc(job, second)
        release.set()
        thread.join(5)
        assert not thread.is_alive()
        first.wait(timeout=5)
        assert registry.has_active_procs(job)
        registry.kill_job_procs(job)
        second.wait(timeout=5)
        assert second.returncode != 0
        assert not registry.has_active_procs(job)
    finally:
        release.set()
        if thread:
            thread.join(5)
        for process in children:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
        registry.kill_job_procs(job)


def test_completed_and_absent_processes_are_safe_to_cancel():
    job = "registry-completed-kill"
    completed = subprocess.Popen([sys.executable, "-c", "pass"])
    completed.wait(timeout=5)
    registry.register_proc(job, completed)
    registry.kill_job_procs(job)
    registry.kill_job_procs(job)
    assert not registry.has_active_procs(job)
