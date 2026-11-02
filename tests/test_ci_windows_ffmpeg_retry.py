"""A retry that trusts a lying exit code is not a retry.

On 2026-07-28 the chocolatey community feed returned 503. `choco install
ffmpeg` printed "Unable to find package 'ffmpeg'" and "installed 0/0 packages"
— then exited **0**. The retry loop added on 2026-07-20 was written as
`choco install ... && break`, so it broke out on the first attempt, no backoff
ran, and the job died one line later on `ffmpeg: command not found`.

This runs the real loop body from ci.yml against a stubbed `choco` that
reproduces that behaviour, so the guard is pinned to what actually happened
rather than to what the exit code claimed.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
import sys

import pytest
import yaml

pytestmark = pytest.mark.skipif(
    sys.platform == "win32", reason="the step is bash; exercised here on POSIX"
)

_WORKFLOW = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    ".github", "workflows", "ci.yml",
)
_STEP = "System deps (Windows)"


def _step_script():
    with open(_WORKFLOW, encoding="utf-8") as fh:
        wf = yaml.safe_load(fh)
    for job in wf["jobs"].values():
        for step in job.get("steps", []):
            if step.get("name") == _STEP:
                return step["run"]
    raise AssertionError(f"step {_STEP!r} not found in ci.yml")


_BASH = shutil.which("bash") or "/bin/bash"


def _run(tmp_path, *, choco_exit, succeed_on_attempt=None):
    """Run the step with stub `choco`/`ffmpeg`/`sleep` on PATH.

    `succeed_on_attempt` is the 1-based attempt on which choco finally puts
    ffmpeg on PATH; None means it never does.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    counter = tmp_path / "attempts"
    ffmpeg_path = bin_dir / "ffmpeg"

    # The binary a "successful" install drops onto PATH. Staged outside bin_dir
    # and already executable, so the choco stub only has to copy it — building
    # a shell script from inside another shell script is how the quoting in
    # this harness went wrong the first time.
    staged = tmp_path / "ffmpeg-staged"
    staged.write_text(f"#!{_BASH}\necho 'ffmpeg version 8.1.2'\n")
    staged.chmod(staged.stat().st_mode | stat.S_IEXEC)

    # ffmpeg must be genuinely ABSENT from PATH until that copy happens —
    # `command -v` tests existence, so a stub that merely exits 127 would look
    # installed and the retry would never be exercised.
    install_line = (
        f'if [ "$n" -ge {succeed_on_attempt} ]; then '
        f"cp {str(staged)!r} {str(ffmpeg_path)!r}; fi\n"
        if succeed_on_attempt
        else ""
    )
    (bin_dir / "choco").write_text(
        f"#!{_BASH}\n"
        f"n=$(cat {str(counter)!r} 2>/dev/null || echo 0); n=$((n+1));"
        f" echo $n > {str(counter)!r}\n"
        + install_line
        + 'echo "Chocolatey installed 0/0 packages."\n'
        f"exit {choco_exit}\n"
    )
    # Keep the backoff from actually sleeping 90s in the test.
    (bin_dir / "sleep").write_text(f"#!{_BASH}\nexit 0\n")
    for name in ("choco", "sleep"):
        p = bin_dir / name
        p.chmod(p.stat().st_mode | stat.S_IEXEC)

    # PATH is ONLY this directory (see below), so the few real tools the stubs
    # shell out to have to be reachable from inside it.
    for tool in ("cat", "cp"):
        real = shutil.which(tool)
        if real:
            (bin_dir / tool).symlink_to(real)

    script = tmp_path / "step.sh"
    script.write_text(_step_script())
    # PATH is the stub dir and NOTHING else. Any system directory on it can
    # carry a real ffmpeg that satisfies `command -v`, which silently neuters
    # every assertion below — that false-green happened twice while writing
    # this: /opt/homebrew/bin locally, then /usr/bin on the Linux runner.
    env = dict(os.environ, PATH=str(bin_dir))
    proc = subprocess.run(
        [shutil.which("bash") or "/bin/bash", str(script)],
        capture_output=True, text=True, env=env, timeout=120,
    )
    attempts = int(counter.read_text(encoding="utf-8").strip()) if counter.exists() else 0
    return proc, attempts


def test_harness_actually_hides_ffmpeg(tmp_path):
    """Guard the guard.

    Every assertion here depends on ffmpeg being genuinely absent until a stub
    install creates it. If a real ffmpeg leaks onto PATH, the loop exits on the
    first attempt and all four tests below pass against a broken workflow —
    which is exactly what happened twice: /opt/homebrew/bin locally, /usr/bin
    on the Linux runner. So assert the sandbox is a sandbox.
    """
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    probe = subprocess.run(
        [shutil.which("bash") or "/bin/bash", "-c", "command -v ffmpeg"],
        capture_output=True, text=True,
        env=dict(os.environ, PATH=str(bin_dir)),
    )
    assert probe.returncode != 0, (
        f"ffmpeg is visible at {probe.stdout.strip()!r} with PATH pinned to the "
        f"stub dir — the retry tests would pass against a broken loop"
    )


def test_retries_when_choco_lies_about_success(tmp_path):
    """The 2026-07-28 regression: exit 0, nothing installed, no retry."""
    proc, attempts = _run(tmp_path, choco_exit=0, succeed_on_attempt=2)
    # Exactly 2, not ">= 2": the loop must also STOP once ffmpeg appears, or a
    # regression that keeps going would still satisfy a lower bound.
    assert attempts == 2, (
        "choco exited 0 without installing ffmpeg and the loop moved on — "
        "the retry must test whether ffmpeg exists, not what choco returned"
    )
    assert proc.returncode == 0, proc.stderr


def test_retries_on_a_normal_nonzero_failure(tmp_path):
    proc, attempts = _run(tmp_path, choco_exit=1, succeed_on_attempt=3)
    assert attempts == 3
    assert proc.returncode == 0, proc.stderr


def test_gives_up_loudly_when_ffmpeg_never_arrives(tmp_path):
    """Exhausting the retries must fail the job — a silent pass would push a
    broken toolchain into the test run."""
    proc, attempts = _run(tmp_path, choco_exit=0, succeed_on_attempt=None)
    assert attempts == 3
    assert proc.returncode != 0


def test_no_backoff_after_the_final_attempt(tmp_path):
    """There is no fourth try to wait for; sleeping 90s only delays a job that
    has already failed."""
    proc, _ = _run(tmp_path, choco_exit=0, succeed_on_attempt=None)
    assert "retrying in 90s" not in proc.stdout, (
        "the loop announced a retry after its last attempt:\n" + proc.stdout
    )
    assert proc.stdout.count("retrying in") == 2


def test_does_not_retry_when_the_first_attempt_works(tmp_path):
    """Backoff is 30s+60s; burning it when nothing is wrong is its own bug."""
    proc, attempts = _run(tmp_path, choco_exit=0, succeed_on_attempt=1)
    assert attempts == 1
    assert proc.returncode == 0, proc.stderr
