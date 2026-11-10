"""A slow engine import is not a broken one (#1414).

Every subprocess engine confirms a candidate interpreter by spawning it and
importing the engine package. That probe used to have two outcomes, and a
bound that elapsed produced the *negative* one — so on the hosts where
``import indextts.infer_v2`` is slowest (cold page cache, spinning disk,
Windows AV scanning every DLL of a torch import), a working
``OMNIVOICE_INDEXTTS_DIR`` install was discarded and the user was told the
engine was not installed, or got a 500 on the first generation after launch.

These pin the three properties that fix depends on:

* a timeout is reported as ``"unproven"``, never ``"no"``;
* an unproven candidate is used rather than declaring the engine missing,
  but only after every candidate has had a chance to prove itself outright;
* the bound is tunable per host, because "how slow is too slow" is a
  property of the machine and not of the code.

Fail-before/pass-after: with the old two-state probe every one of these
either raises "not installed" or resolves the wrong interpreter.
"""
from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import pytest

logger = logging.getLogger("test.venv_probe")


@pytest.fixture
def probe():
    """Resolved at run time, not import time: a module-level import of an app
    module keeps mutable state in sys.modules across test boundaries."""
    from engines import _venv_probe

    return _venv_probe


# ── the probe itself ───────────────────────────────────────────────────────

def test_a_timeout_is_unproven_not_a_failure(probe, monkeypatch):
    def _raise(*a, **kw):
        raise subprocess.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(probe.subprocess, "run", _raise)
    verdict = probe.venv_can_import("python", "import anything", engine="indextts", logger=logger)
    assert verdict == "unproven", (
        "a probe that ran out of time proves nothing — reporting 'no' is what "
        "discarded working installs in #1414"
    )


def test_a_real_import_failure_is_still_no(probe):
    # Proven negative: the interpreter ran and the import raised.
    verdict = probe.venv_can_import(
        sys.executable,
        "import a_module_that_does_not_exist_1414",
        engine="indextts",
        logger=logger,
    )
    assert verdict == "no"


def test_a_working_import_is_yes(probe):
    verdict = probe.venv_can_import(
        sys.executable, "import json", engine="indextts", logger=logger,
    )
    assert verdict == "yes"


def test_an_unrunnable_interpreter_is_no(probe, tmp_path):
    # OSError, not a slow import: nothing to wait for.
    verdict = probe.venv_can_import(
        tmp_path / "does-not-exist", "import json", engine="indextts", logger=logger,
    )
    assert verdict == "no"


# ── the bound ──────────────────────────────────────────────────────────────

def test_bound_defaults_generously(probe, monkeypatch):
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_IMPORT_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.delenv("OMNIVOICE_ENGINE_IMPORT_PROBE_TIMEOUT_S", raising=False)
    assert probe.probe_timeout_s("indextts") == probe.DEFAULT_PROBE_TIMEOUT_S
    # The old per-engine bound. A cold torch import routinely exceeds it, which
    # is the whole of #1414 — if this ever drops back, the bug is back.
    assert probe.DEFAULT_PROBE_TIMEOUT_S > 15


def test_per_engine_env_beats_global(probe, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ENGINE_IMPORT_PROBE_TIMEOUT_S", "30")
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_IMPORT_PROBE_TIMEOUT_S", "120")
    assert probe.probe_timeout_s("indextts") == 120.0
    assert probe.probe_timeout_s("dots_tts") == 30.0


@pytest.mark.parametrize("bad", ["", "  ", "abc", "0", "-5", "inf", "nan", "1e400"])
def test_a_useless_bound_is_ignored(probe, monkeypatch, bad):
    """Never honour 0/negative: an unbounded probe lets one wedged candidate
    hang engine resolution forever, which is the failure the bound exists for.

    `inf` needs its own rejection: it parses cleanly and is positive, but
    `subprocess.run(timeout=inf)` raises OverflowError — from inside the one
    function that promises never to raise (CodeRabbit)."""
    monkeypatch.delenv("OMNIVOICE_ENGINE_IMPORT_PROBE_TIMEOUT_S", raising=False)
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_IMPORT_PROBE_TIMEOUT_S", bad)
    assert probe.probe_timeout_s("indextts") == probe.DEFAULT_PROBE_TIMEOUT_S


# ── resolution order ───────────────────────────────────────────────────────

@pytest.fixture
def indextts_bootstrap(monkeypatch, tmp_path):
    from engines.indextts import bootstrap

    bootstrap.invalidate()
    user_venv = tmp_path / "user" / ".venv"
    own_venv = tmp_path / "own"
    for root in (user_venv, own_venv):
        (root / "bin").mkdir(parents=True, exist_ok=True)
        (root / "Scripts").mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("OMNIVOICE_INDEXTTS_DIR", str(tmp_path / "user"))
    monkeypatch.setattr(bootstrap, "_ENGINES_VENV_DIR", own_venv)
    # Both candidates exist on disk; only the probe verdict decides.
    for root in (user_venv, own_venv):
        bootstrap._venv_python_path(root).write_text("")
    yield bootstrap, bootstrap._venv_python_path(user_venv), bootstrap._venv_python_path(own_venv)
    bootstrap.invalidate()


def test_unproven_user_venv_is_used_rather_than_declared_missing(
    indextts_bootstrap, monkeypatch,
):
    """The reported bug, end to end.

    Before the fix this raised "IndexTTS-2 is not installed" (or fell through
    to a multi-minute bootstrap over the top of a working clone) purely
    because the probe was slower than its bound.
    """
    bootstrap, user_py, own_py = indextts_bootstrap
    monkeypatch.setattr(
        bootstrap, "_venv_can_import_indextts",
        lambda p: "unproven" if p == user_py else "no",
    )
    assert bootstrap.resolve_indextts_venv() == user_py


def test_a_proven_candidate_still_beats_an_unproven_one(
    indextts_bootstrap, monkeypatch,
):
    """Accepting-on-timeout must not demote a candidate that actually works —
    otherwise a wedged user clone would shadow a healthy bootstrapped venv."""
    bootstrap, user_py, own_py = indextts_bootstrap
    monkeypatch.setattr(
        bootstrap, "_venv_can_import_indextts",
        lambda p: "unproven" if p == user_py else "yes",
    )
    assert bootstrap.resolve_indextts_venv() == own_py


def test_proven_failures_alone_still_raise(indextts_bootstrap, monkeypatch):
    """No weakening of the real "you have not installed this" path."""
    bootstrap, user_py, own_py = indextts_bootstrap
    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    monkeypatch.setattr(bootstrap, "_venv_can_import_indextts", lambda p: "no")
    with pytest.raises(RuntimeError, match="not installed"):
        bootstrap.resolve_indextts_venv()


# ── the whole class, not one engine ────────────────────────────────────────

ENGINES = ("indextts", "confucius4", "dots_tts", "moss_tts_v15", "ming_tts")


@pytest.mark.parametrize("engine", ENGINES)
def test_every_engine_shares_one_probe(engine):
    """The bound was 10s for IndexTTS and 15s for its three peers, each with
    its own copy of the same function. One implementation means a fix here
    cannot leave three engines behind."""
    src = Path("backend/engines") / engine / "bootstrap.py"
    text = src.read_text(encoding="utf-8")
    assert "from engines._venv_probe import" in text, f"{engine} has its own probe"
    assert "_IMPORT_PROBE_TIMEOUT_S" not in text, (
        f"{engine} still carries a private probe bound"
    )


def test_probe_logs_do_not_leak_the_users_home_path(probe, monkeypatch, caplog):
    """These lines land in backend.log, which goes into diagnostic bundles and
    prefilled bug reports — and every candidate path runs through the user's
    home directory, i.e. their account name (CodeRabbit)."""
    import subprocess as sp

    def _timeout(*a, **kw):
        raise sp.TimeoutExpired(cmd="python", timeout=1)

    monkeypatch.setattr(probe.subprocess, "run", _timeout)
    home = str(Path.home())
    candidate = Path(home) / "secret-project" / ".venv" / "bin" / "python"

    with caplog.at_level(logging.DEBUG):
        assert probe.venv_can_import(
            candidate, "import x", engine="indextts", logger=logger,
        ) == "unproven"

    text = caplog.text
    assert home not in text, "the probe log carries the user's home path"
    # The log must still be useful — it should name the venv, just not the user.
    assert "secret-project" in text or "python" in text


def test_log_safe_survives_a_broken_sanitizer(probe, monkeypatch):
    """A probe must not fail because logging could not redact."""
    import core.failure as cf

    monkeypatch.setattr(
        cf, "sanitize",
        lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("nope")),
    )
    out = probe.log_safe("/home/someone/.venv/bin/python")
    assert out == "python"
