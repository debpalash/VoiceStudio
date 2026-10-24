"""#2632: kill_job_procs must not forget processes registered mid-kill."""
from services import proc_registry as pr


class _Proc:
    def __init__(self, on_kill=None):
        self.returncode = None
        self.killed = False
        self._on_kill = on_kill

    def kill(self):
        self.killed = True
        if self._on_kill:
            self._on_kill()


def test_proc_registered_during_kill_stays_tracked():
    jid = "job_2632"
    late = _Proc()
    first = _Proc(on_kill=lambda: pr.register_proc(jid, late))
    pr.register_proc(jid, first)
    try:
        pr.kill_job_procs(jid)
        assert first.killed and not late.killed
        assert pr.has_active_procs(jid)
        assert pr._active_procs[jid] == [late]
        pr.kill_job_procs(jid)
        assert late.killed
        assert not pr.has_active_procs(jid)
        assert jid not in pr._active_procs
    finally:
        pr._active_procs.pop(jid, None)


def test_failed_kill_stays_tracked_for_retry_with_concurrent_registration():
    jid = "job_kill_retry"
    late = _Proc()

    class FailOnce(_Proc):
        attempts = 0

        def kill(self):
            self.attempts += 1
            if self.attempts == 1:
                pr.register_proc(jid, late)
                raise PermissionError("transient kill failure")
            super().kill()

    first = FailOnce()
    pr.register_proc(jid, first)
    try:
        pr.kill_job_procs(jid)
        assert not first.killed
        assert pr.has_active_procs(jid)
        assert pr._active_procs[jid] == [first, late]
        pr.kill_job_procs(jid)
        assert first.attempts == 2
        assert first.killed and late.killed
        assert not pr.has_active_procs(jid)
    finally:
        pr._active_procs.pop(jid, None)


def test_completed_and_missing_processes_are_unregistered():
    jid = "job_finished_kill"

    class Missing(_Proc):
        def kill(self):
            raise ProcessLookupError("already exited")

    completed = _Proc()
    completed.returncode = 0
    pr.register_proc(jid, completed)
    pr.register_proc(jid, Missing())
    try:
        pr.kill_job_procs(jid)
        assert not completed.killed
        assert not pr.has_active_procs(jid)
    finally:
        pr._active_procs.pop(jid, None)
