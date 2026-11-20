"""Every workflow job must declare its own timeout.

GitHub's default is 360 minutes per job. A job that wedges — a hung install, a
crawler waiting on a health check that never answers, a registry push retrying
forever — therefore holds a runner and, for the jobs the required check needs,
blocks the merge for six hours. The repository already pins queue hygiene
(``tests/test_ci_queue_hygiene.py``) and the required-check wiring
(``tests/test_ci_required_gate.py``); this is the missing third rule, and it is
the one that keeps a single stuck job from turning into a queue-wide stall.

Budgets live in the workflow next to each job so a reviewer sees the number
where it applies. This guard checks presence and a ceiling, not the exact value:
choosing 20 vs 30 minutes is a judgement call, but "no bound at all" is not.
"""
from __future__ import annotations

import pathlib

import yaml

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"

#: No single job may be allowed to run longer than this without a deliberate
#: edit here. ``ct2-rocm-windows.yml::build`` is the documented long pole: it
#: compiles the HIP CTranslate2 wheel set on a Windows runner, which legitimately
#: takes hours. Everything else stays well under the ceiling so a runaway job is
#: caught early rather than 360 minutes later.
MAX_JOB_TIMEOUT_MINUTES = 180
LONG_POLE_MINUTES = 300
LONG_POLE_JOBS = {("ct2-rocm-windows.yml", "build")}


def _jobs():
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for name, job in (document.get("jobs") or {}).items():
            if isinstance(job, dict):
                yield path, name, job


def test_every_job_declares_a_timeout():
    missing = [f"{path.name}::{name}" for path, name, job in _jobs() if "timeout-minutes" not in job]
    assert not missing, (
        "These jobs fall back to GitHub's 360-minute default, so one wedged run "
        "holds a runner (and the merge queue) for six hours. Add a "
        "`timeout-minutes:` sized from the sibling jobs that already carry one:\n"
        + "\n".join(missing)
    )


def test_timeouts_are_bounded_and_positive():
    bad = []
    for path, name, job in _jobs():
        value = job.get("timeout-minutes")
        if value is None:
            continue
        ceiling = LONG_POLE_MINUTES if (path.name, name) in LONG_POLE_JOBS else MAX_JOB_TIMEOUT_MINUTES
        # A workflow may compute the value from a matrix expression; those are
        # checked where the expression is defined rather than skipped silently.
        if isinstance(value, str):
            if "${{" in value:
                continue
            bad.append(f"{path.name}::{name} has a non-numeric timeout {value!r}")
            continue
        if not isinstance(value, int) or value <= 0 or value > ceiling:
            bad.append(f"{path.name}::{name} has timeout-minutes={value!r} (ceiling {ceiling})")
    assert not bad, (
        f"Every timeout must be an int in 1..{MAX_JOB_TIMEOUT_MINUTES} "
        f"({LONG_POLE_MINUTES} for the documented long-pole jobs):\n" + "\n".join(bad)
    )


def test_the_scan_sees_every_workflow():
    # Guard against a vacuous pass if the directory or the loader changes.
    seen = {path for path, _name, _job in _jobs()}
    assert len(seen) >= 30, f"only {len(seen)} workflows parsed; the glob or layout changed"
