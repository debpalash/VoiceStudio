"""probe pytest plumbing: a session-scoped result recorder that emits an HTML
report when the probe suite finishes (opened only with PROBE_OPEN=1).

Tests record outcomes via the ``probe_report`` fixture. At session end the
recorded outcomes are rendered to ``tests/probe/reports/report-*.html`` and
opened in the browser (suppressed in CI / headless / when ``PROBE_NO_OPEN``).

This conftest is imported by pytest as ``probe.conftest`` (the probe package),
so relative imports resolve; a sys.path fallback covers odd invocations.
"""

from __future__ import annotations

import pytest

try:  # normal case: loaded as part of the `probe` package
    from .report import Report, SpecOutcome, save_and_open
    from .spec import JudgeResult, Spec
except ImportError:  # pragma: no cover - defensive for unusual rootdirs
    import os
    import sys

    sys.path.insert(0, os.path.dirname(__file__))
    from report import Report, SpecOutcome, save_and_open  # type: ignore
    from spec import JudgeResult, Spec  # type: ignore


class ProbeRecorder:
    """Collects per-spec outcomes during a session for the final HTML report."""

    def __init__(self) -> None:
        self.outcomes: list[SpecOutcome] = []

    def record(self, spec: Spec, results: list[JudgeResult], duration_s: float = 0.0) -> None:
        self.outcomes.append(SpecOutcome.from_spec(spec, results, duration_s=duration_s))

    def record_group(self, name: str, results: list[JudgeResult], *, layer: str = "", duration_s: float = 0.0) -> None:
        self.outcomes.append(SpecOutcome(name=name, feature=name, layer=layer, results=list(results), duration_s=duration_s))


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "api_fuzz: L1 Schemathesis property fuzzing of the API")
    if not hasattr(config, "_probe_recorder"):
        config._probe_recorder = ProbeRecorder()  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def probe_report(request: pytest.FixtureRequest) -> ProbeRecorder:
    return request.config._probe_recorder  # type: ignore[attr-defined]


@pytest.fixture(scope="session")
def boot_capture() -> dict:
    """Boot the backend ONCE (fresh data dir, subprocess-isolated) and share the
    rich capture — first-run endpoints, engine/ASR matrices, loopback-reject
    status, and the OpenAPI inventory — across the engine/security/coverage
    probes so the suite pays for only one boot."""
    from . import env

    with env.fresh_data_dir() as data_dir:
        return env.capture_first_run(data_dir)


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    recorder: ProbeRecorder | None = getattr(session.config, "_probe_recorder", None)
    if not recorder or not recorder.outcomes:
        return
    report = Report(outcomes=recorder.outcomes)
    # When there are blocking failures, the Triager drafts a prefilled GitHub
    # issue URL and the report renders a one-click "Draft GitHub issue" button.
    if report.failed:
        try:
            from .triage import triage

            report.issue_url = triage(report).url
        except Exception:  # noqa: BLE001 — triage is best-effort, never break reporting
            pass
    # Writes the HTML; opens it in the browser only with PROBE_OPEN=1
    # (see report._should_open).
    save_and_open(report)
