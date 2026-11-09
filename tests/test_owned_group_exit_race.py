"""Darwin can report EPERM when a control-pipe cancellation just killed the group."""
import os
import signal
from itertools import chain, repeat
from types import SimpleNamespace

import pytest

pytestmark = pytest.mark.skipif(os.name != "posix", reason="POSIX process groups only")


@pytest.mark.parametrize("state", ["exited", "exit_pending", "live", "reaped"])
@pytest.mark.parametrize("probe", ["waitpid", "waitid"])
def test_group_signal_permission_error_is_benign_only_after_owned_leader_exits(monkeypatch, state, probe):
    from core import contained_subprocess
    from core.contained_subprocess import OwnedPopen

    # Exercise the observation deadline without depending on runner scheduling.
    now = [0.0]
    def sleep(duration):
        now[0] += duration
    monkeypatch.setattr(contained_subprocess, "time", SimpleNamespace(monotonic=lambda: now[0], sleep=sleep))

    # The first probe proves the leader live; the second sees its concurrent
    # control-pipe cancellation. Exercise both native API choices on POSIX CI.
    pid = 12345
    proc = SimpleNamespace(pid=pid, args=[], stdin=None, stdout=None, stderr=None, returncode=None)
    handle = OwnedPopen(proc, -1, -1)
    def probe_result(*args):
        result = next(replies)
        if isinstance(result, BaseException):
            raise result
        return result
    if probe == "waitpid":
        monkeypatch.delattr(os, "waitid", raising=False)
        last = {"exited": (pid, signal.SIGKILL), "live": (0, 0), "reaped": ChildProcessError()}
        replies = (iter([(0, 0), (0, 0), (pid, signal.SIGKILL)])
                   if state == "exit_pending" else chain([(0, 0)], repeat(last[state])))
        monkeypatch.setattr(os, "waitpid", probe_result)
    else:
        for name, value in (("P_PID", 1), ("WEXITED", 4), ("WNOHANG", 1), ("WNOWAIT", 8)):
            monkeypatch.setattr(os, name, value, raising=False)
        last = {"exited": SimpleNamespace(si_pid=pid), "live": None, "reaped": ChildProcessError()}
        replies = (iter([None, None, SimpleNamespace(si_pid=pid)])
                   if state == "exit_pending" else chain([None], repeat(last[state])))
        monkeypatch.setattr(os, "waitid", probe_result, raising=False)
    def denied(*args):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(os, "killpg", denied)
    if state != "live":
        handle._signal_owned_group(signal.SIGKILL)
        if probe == "waitpid" and state in ("exited", "exit_pending"):
            assert proc.returncode == -signal.SIGKILL
    else:
        with pytest.raises(PermissionError):
            handle._signal_owned_group(signal.SIGKILL)
