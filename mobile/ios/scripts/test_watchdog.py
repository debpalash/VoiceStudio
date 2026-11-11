#!/usr/bin/env python3
"""Run a test command with live, per-test output and fail a silent hang by name.

Usage: test_watchdog.py [--stall SECONDS] -- COMMAND...

The command runs on a pseudo-terminal, so every test process line-buffers its output
and each "Test ... started" line reaches the CI log at once (through a plain pipe the
log stayed empty until the step timed out). When nothing is printed for --stall
seconds, the watchdog names the last test that started, prints the stacks of every
process in the tree (macOS `sample`), kills the tree and exits 124.
"""
import argparse
import os
import pathlib
import pty
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time

ANSI = re.compile(rb"\x1b\[[0-9;]*[A-Za-z]")
STARTED = re.compile(r"(Test (?:Case )?.+? started)")
STALL_EXIT = 124


def descendants(root):
    """Root and the pids under it, deepest first, from one `ps` snapshot."""
    try:
        table = subprocess.run(["ps", "-A", "-o", "pid=,ppid="], capture_output=True, text=True, timeout=10).stdout
    except (OSError, subprocess.SubprocessError):
        return [root]
    children = {}
    for row in table.splitlines():
        parts = row.split()
        if len(parts) == 2 and all(part.isdigit() for part in parts):
            children.setdefault(int(parts[1]), []).append(int(parts[0]))
    found, queue = [root], [root]
    while queue:
        for child in children.get(queue.pop(), []):
            found.append(child)
            queue.append(child)
    return list(reversed(found))


#: Total time all stack samples may take, so sampling a large process tree
#: cannot delay the kill that follows.
SAMPLE_BUDGET_S = 90.0


def dump_stacks(pids, out, budget=SAMPLE_BUDGET_S, clock=time.monotonic):
    sampler = shutil.which("sample")
    if not sampler:
        out.write(b"(no `sample` on this host; stacks unavailable)\n")
        return
    deadline = clock() + budget
    for pid in pids:
        remaining = deadline - clock()
        if remaining <= 1:
            out.write(f"\n(sampling budget spent; skipped pid {pid})\n".encode())
            continue
        with tempfile.NamedTemporaryFile(suffix=".txt") as report:
            try:
                done = subprocess.run([sampler, str(pid), "3", "-mayDie", "-file", report.name],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
                                      timeout=min(60, remaining))
                stacks = pathlib.Path(report.name).read_bytes()
                if done.returncode != 0 or not stacks.strip():
                    reason = (done.stderr or b"").decode(errors="replace").strip() or "empty report"
                    out.write(f"\n(sample {pid} failed: exit {done.returncode}: {reason})\n".encode())
                else:
                    out.write(f"\n===== stacks of pid {pid} =====\n".encode() + stacks)
            except (OSError, subprocess.SubprocessError) as error:
                out.write(f"\n(sample {pid} failed: {error})\n".encode())
        out.flush()


def _exited_within(child, seconds):
    try:
        child.wait(timeout=seconds)
        return True
    except subprocess.TimeoutExpired:
        return False


def run(command, stall, out=None, clock=time.monotonic):
    out = out or sys.stdout.buffer
    primary, secondary = pty.openpty()
    env = dict(os.environ, NSUnbufferedIO="YES", PYTHONUNBUFFERED="1")
    child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=secondary, stderr=secondary,
                             env=env, start_new_session=True)
    os.close(secondary)
    last_output, last_started, pending, closed = clock(), None, b"", False
    try:
        while True:
            # After EOF the child may still be alive with its output closed: keep
            # polling it against the same stall deadline instead of waiting forever.
            ready = [] if closed else select.select([primary], [], [], 1.0)[0]
            if ready:
                try:
                    chunk = os.read(primary, 65536)
                except OSError:  # EIO once every writer has closed the terminal (Linux)
                    chunk = b""
                if not chunk:
                    closed = True
                    continue
                out.write(chunk)
                out.flush()
                last_output = clock()
                pending += chunk
                *lines, pending = pending.split(b"\n")
                for line in lines:
                    match = STARTED.search(ANSI.sub(b"", line).decode("utf-8", "replace"))
                    if match:
                        last_started = match.group(1)
            elif child.poll() is not None or (closed and _exited_within(child, 1.0)):
                break
            elif clock() - last_output > stall:
                name = last_started or "no test had started"
                out.write(f"\n::error::No test output for {stall} s; hung after: {name}\n".encode())
                dump_stacks(descendants(child.pid), out)
                os.killpg(child.pid, signal.SIGKILL)
                child.wait()
                out.write(f"\n::error::Killed the hung test run after: {name}\n".encode())
                out.flush()
                return STALL_EXIT
    finally:
        os.close(primary)
    return child.wait()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stall", type=int, default=120, help="seconds without output before the run is killed")
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args(argv)
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("missing command")
    return run(command, args.stall)


if __name__ == "__main__":
    sys.exit(main())
