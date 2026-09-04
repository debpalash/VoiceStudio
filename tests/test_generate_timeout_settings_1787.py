"""Regression for #1787 (closes duplicates #1774, #1778): the generation-
timeout error told users to "raise the generation timeout" with no UI path to
do it — the only control was the `OMNIVOICE_GENERATE_TIMEOUT_S` env var, which
a desktop-installer user has no obvious way to set.

Things this pins down:
  1. `/system/set-env` now accepts OMNIVOICE_GENERATE_TIMEOUT_S and
     OMNIVOICE_CPU_GENERATE_TIMEOUT_S, validated as positive numbers (same
     shape as the existing `_PORT_KEYS` validation), and persists them to
     prefs.json like every other PERSISTENT_KEYS entry.
  2. The persisted value is what services/model_manager.py's GPU_JOB_TIMEOUT_S
     / CPU_JOB_TIMEOUT_S capture on the next import — i.e. the same "restored
     before ml_imports" contract OMNIVOICE_PORT already relies on, so a saved
     budget really does take effect after a restart (not silently never).
  3. The public-facing timeout copy no longer recommends an action with no UI
     path.
  4. Review fix (CodeRabbit/Greptile P1): the two Settings rows are genuinely
     independent — saving BOTH budgets must not leave the CPU one silently
     ignored by the legacy "universal override" (see the parallel tests in
     test_generate_timeout_730.py for the generate_timeout_s()-level proof).
  5. Review fix #2: a saved value that an external env var (shell, `.env`,
     Docker, …) shadows must be reported as such — never as a plain success
     the next restart will actually honor.
"""
from __future__ import annotations

import os
import sys

import pytest
from fastapi.testclient import TestClient


def _loopback_client():
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


# ── /system/set-env validation ──────────────────────────────────────────────

@pytest.mark.parametrize(
    "key", ["OMNIVOICE_GENERATE_TIMEOUT_S", "OMNIVOICE_CPU_GENERATE_TIMEOUT_S"]
)
class TestSetEnvValidation:
    def test_rejects_non_numeric(self, key):
        c = _loopback_client()
        r = c.post("/system/set-env", json={"key": key, "value": "soon"})
        assert r.status_code == 400
        assert "not a number" in r.json()["detail"]

    def test_rejects_zero(self, key):
        c = _loopback_client()
        r = c.post("/system/set-env", json={"key": key, "value": "0"})
        assert r.status_code == 400

    def test_rejects_negative(self, key):
        c = _loopback_client()
        r = c.post("/system/set-env", json={"key": key, "value": "-30"})
        assert r.status_code == 400

    def test_rejects_absurdly_large_value(self, key):
        """A fat-fingered extra digit must not hide a wedged job for days."""
        c = _loopback_client()
        r = c.post("/system/set-env", json={"key": key, "value": "50000000"})
        assert r.status_code == 400

    def test_accepts_valid_value_and_persists(self, key):
        c = _loopback_client()
        try:
            r = c.post("/system/set-env", json={"key": key, "value": "900"})
            assert r.status_code == 200
            assert r.json() == {"key": key, "set": True, "shadowed": False}
            assert os.environ.get(key) == "900"

            from core import prefs
            assert prefs.get(f"env.{key}") == "900"
        finally:
            os.environ.pop(key, None)
            from core import prefs
            prefs.delete(f"env.{key}")


# ── effective-budget behaviour: persisted value survives the next import ──

@pytest.fixture
def model_manager(monkeypatch):
    for mod_name in ("core.config", "services.model_manager"):
        if getattr(sys.modules.get(mod_name), "__file__", None) is None:
            sys.modules.pop(mod_name, None)
    import services.model_manager as mm
    return mm


def test_generate_budget_takes_effect_after_restart(model_manager, monkeypatch):
    """`generate_timeout_s` is read from module-level GPU_JOB_TIMEOUT_S, which
    is captured at import time — so the effective budget changes only once
    the (restored) env var is present BEFORE the next import, exactly what
    the "restart required" badge in Settings promises."""
    import importlib

    monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "111")
    mm = importlib.reload(model_manager)
    try:
        assert mm.GPU_JOB_TIMEOUT_S == 111.0
        assert mm.generate_timeout_s("short") == 111.0
    finally:
        monkeypatch.delenv("OMNIVOICE_GENERATE_TIMEOUT_S", raising=False)
        importlib.reload(mm)


def test_cpu_budget_takes_effect_after_restart(model_manager, monkeypatch):
    import importlib

    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "222")
    mm = importlib.reload(model_manager)
    try:
        assert mm.CPU_JOB_TIMEOUT_S == 222.0
    finally:
        monkeypatch.delenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", raising=False)
        importlib.reload(mm)


def test_saving_both_budgets_the_cpu_one_actually_governs_cpu_dispatch(
    model_manager, monkeypatch,
):
    """Review fix (CodeRabbit/Greptile P1): the two Settings rows must be
    genuinely independent. Before the fix, an explicit
    OMNIVOICE_GENERATE_TIMEOUT_S made the legacy `universal_override` win for
    CPU jobs too, so saving BOTH rows silently ignored the CPU one — the exact
    class of defect #1787 exists to remove. This test fails on the pre-fix
    `generate_timeout_s` logic (CPU dispatch would get 123.5, not 999.0)."""
    import importlib
    import types
    import core.device_caps as caps

    monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "123.5")
    monkeypatch.setenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", "999.0")
    mm = importlib.reload(model_manager)
    monkeypatch.setattr(caps, "detect_host_caps", lambda: types.SimpleNamespace(family="cpu"))
    try:
        assert mm.generate_timeout_s("A short CPU render") == 999.0
    finally:
        monkeypatch.delenv("OMNIVOICE_GENERATE_TIMEOUT_S", raising=False)
        monkeypatch.delenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", raising=False)
        importlib.reload(mm)


# ── /system/info surfaces the effective values ──────────────────────────────

def test_system_info_has_generate_timeout_fields():
    c = _loopback_client()
    body = c.get("/system/info").json()
    assert isinstance(body["generate_timeout_s"], (int, float))
    assert isinstance(body["cpu_generate_timeout_s"], (int, float))
    assert body["generate_timeout_s"] > 0
    assert body["cpu_generate_timeout_s"] > 0
    assert body["generate_timeout_shadowed"] is False
    assert body["cpu_generate_timeout_shadowed"] is False


# ── review fix #2: external env vars must never be reported as "saved OK" ──

class TestExternalOverrideDetection:
    """core.prefs.restore_env()/is_env_shadowed(): a value already present in
    os.environ before our own prefs restore runs came from OUTSIDE the app
    (shell, `.env`, Docker, …) — Settings never writes there — so
    os.environ.setdefault() silently no-ops for it on every future restart
    too. A Settings control must say so, never a plain "saved" success."""

    def test_flags_shadow_when_external_value_present(self, monkeypatch):
        from core import prefs

        original = prefs._EXTERNALLY_PROVIDED
        try:
            monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "42")  # simulates external
            prefs.restore_env({"env.OMNIVOICE_GENERATE_TIMEOUT_S": "99"})
            assert prefs.is_env_shadowed("OMNIVOICE_GENERATE_TIMEOUT_S") is True
            # setdefault must not have clobbered the external value.
            assert os.environ["OMNIVOICE_GENERATE_TIMEOUT_S"] == "42"
        finally:
            prefs._EXTERNALLY_PROVIDED = original

    def test_no_shadow_when_nothing_external(self, monkeypatch):
        from core import prefs

        original = prefs._EXTERNALLY_PROVIDED
        try:
            monkeypatch.delenv("OMNIVOICE_GENERATE_TIMEOUT_S", raising=False)
            prefs.restore_env({"env.OMNIVOICE_GENERATE_TIMEOUT_S": "99"})
            assert prefs.is_env_shadowed("OMNIVOICE_GENERATE_TIMEOUT_S") is False
            assert os.environ["OMNIVOICE_GENERATE_TIMEOUT_S"] == "99"
        finally:
            prefs._EXTERNALLY_PROVIDED = original
            os.environ.pop("OMNIVOICE_GENERATE_TIMEOUT_S", None)

    def test_shadow_flag_is_per_key_not_global(self, monkeypatch):
        """One shadowed key must not falsely flag an unrelated one."""
        from core import prefs

        original = prefs._EXTERNALLY_PROVIDED
        try:
            monkeypatch.setenv("OMNIVOICE_GENERATE_TIMEOUT_S", "42")
            monkeypatch.delenv("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", raising=False)
            prefs.restore_env({
                "env.OMNIVOICE_GENERATE_TIMEOUT_S": "99",
                "env.OMNIVOICE_CPU_GENERATE_TIMEOUT_S": "88",
            })
            assert prefs.is_env_shadowed("OMNIVOICE_GENERATE_TIMEOUT_S") is True
            assert prefs.is_env_shadowed("OMNIVOICE_CPU_GENERATE_TIMEOUT_S") is False
        finally:
            prefs._EXTERNALLY_PROVIDED = original
            os.environ.pop("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", None)

    def test_system_info_surfaces_the_override_notice(self, monkeypatch):
        """The API-level contract the panel reads: /system/info must NOT
        report a shadowed key as a plain applied value."""
        from core import prefs as prefs_mod

        monkeypatch.setattr(
            prefs_mod, "is_env_shadowed",
            lambda key: key == "OMNIVOICE_GENERATE_TIMEOUT_S",
        )
        c = _loopback_client()
        body = c.get("/system/info").json()
        assert body["generate_timeout_shadowed"] is True
        assert body["cpu_generate_timeout_shadowed"] is False

    def test_set_env_never_reports_plain_success_when_shadowed(self, monkeypatch):
        """The exact defect named in review: a save that will be ignored must
        not come back as an unqualified {"set": True}."""
        from core import prefs as prefs_mod

        monkeypatch.setattr(prefs_mod, "is_env_shadowed", lambda key: True)
        c = _loopback_client()
        try:
            r = c.post(
                "/system/set-env",
                json={"key": "OMNIVOICE_GENERATE_TIMEOUT_S", "value": "900"},
            )
            assert r.status_code == 200
            body = r.json()
            assert body["set"] is True
            assert body["shadowed"] is True
        finally:
            os.environ.pop("OMNIVOICE_GENERATE_TIMEOUT_S", None)
            from core import prefs
            prefs.delete("env.OMNIVOICE_GENERATE_TIMEOUT_S")


# ── copy fix: the timeout message must not recommend a UI-less action ──────

def test_generation_timeout_copy_names_a_reachable_remedy():
    from core.public_errors import stream_failure

    detail = stream_failure("generation_timeout")["detail"]
    assert "raise the generation timeout" not in detail
    assert "Settings" in detail
