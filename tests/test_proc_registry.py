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
