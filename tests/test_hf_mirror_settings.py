"""HF mirror (HF_ENDPOINT) setting — Wave 4.3. Pure, prefs stubbed."""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest


_ENDPOINT_ENV_KEYS = ("HF_ENDPOINT", "OMNIVOICE_HF_ENDPOINT_MODE", "OMNIVOICE_HF_ENDPOINT_AUTO")


@pytest.fixture(autouse=True)
def _endpoint_env_hygiene():
    """Guaranteed save/restore of the endpoint env vars.

    `monkeypatch.delenv(raising=False)` on an ABSENT var records nothing to
    undo — so the `os.environ["HF_ENDPOINT"] = url` that set_hf_mirror writes
    DURING a test used to leak process-wide and flip later suites' preflight
    tests into the explicit-endpoint branch (CI-order-dependent failures).
    """
    saved = {k: os.environ.pop(k, None) for k in _ENDPOINT_ENV_KEYS}
    yield
    for k, v in saved.items():
        if v is None:
            os.environ.pop(k, None)
        else:
            os.environ[k] = v


@pytest.fixture
def settings_mod(monkeypatch, tmp_path):
    store = {}
    import core.user_env as ue
    from core import prefs
    monkeypatch.setattr(ue, "get_user_env", lambda k, path=None: store.get(k))
    monkeypatch.setattr(ue, "set_user_env", lambda k, v, path=None: store.__setitem__(k, v))
    monkeypatch.setattr(ue, "unset_user_env", lambda k, path=None: store.pop(k, None))
    # Isolate the auto-selection state (hf_endpoint_mode + cached decision).
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    import services.endpoint_race as er
    monkeypatch.setattr(er, "_FAILOVER_ATTEMPTED", set())
    return importlib.import_module("api.routers.settings")


def test_get_default_empty(settings_mod):
    st = settings_mod.get_hf_mirror()
    assert st["configured"] == "" and st["effective"] == ""
    assert any(p["url"] == "https://hf-mirror.com" for p in st["presets"])


def test_set_and_clear(settings_mod):
    st = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com/"))
    assert st["configured"] == "https://hf-mirror.com"  # trailing slash trimmed
    assert st["restart_required"] is True  # empty → mirror is a real change
    assert os.environ["HF_ENDPOINT"] == "https://hf-mirror.com"
    assert settings_mod.get_hf_mirror()["configured"] == "https://hf-mirror.com"

    st2 = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url=""))
    assert st2["restart_required"] is True  # mirror → cleared is a real change
    assert settings_mod.get_hf_mirror()["configured"] == ""
    assert "HF_ENDPOINT" not in os.environ


def test_restart_required_only_on_change(settings_mod):
    """restart_required is honest: True only when the persisted value actually
    changes — a no-op re-save of the same URL must NOT nag the user to restart."""
    # First save of a value is a change.
    assert settings_mod.set_hf_mirror(
        settings_mod._HFMirrorBody(url="https://hf-mirror.com")
    )["restart_required"] is True
    # Re-saving the SAME value (even with a trailing slash) is a no-op.
    assert settings_mod.set_hf_mirror(
        settings_mod._HFMirrorBody(url="https://hf-mirror.com/")
    )["restart_required"] is False
    # Saving empty when already empty is also a no-op.
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url=""))
    assert settings_mod.set_hf_mirror(
        settings_mod._HFMirrorBody(url="")
    )["restart_required"] is False


def test_rejects_non_http(settings_mod):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="hf-mirror.com"))
    assert ei.value.status_code == 400


# ── Automatic endpoint selection (Auto mode) ────────────────────────────────

def test_get_defaults_to_auto_mode_when_nothing_configured(settings_mod):
    st = settings_mod.get_hf_mirror()
    assert st["mode"] == "auto"
    assert st["auto"] is None  # never raced yet — GET must not probe
    assert st["auto_opt_out"] is False


def test_get_reports_manual_when_endpoint_configured(settings_mod):
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com"))
    st = settings_mod.get_hf_mirror()
    assert st["mode"] == "manual"
    assert st["auto"] is None


def test_existing_explicit_config_never_migrated_to_auto(settings_mod, monkeypatch):
    """A pre-existing HF_ENDPOINT (older install, launcher env) loads as
    manual — auto never captures users who already chose."""
    monkeypatch.setenv("HF_ENDPOINT", "https://custom.example")
    st = settings_mod.get_hf_mirror()
    assert st["mode"] == "manual"


def test_env_opt_out_reported(settings_mod, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_HF_ENDPOINT_MODE", "manual")
    st = settings_mod.get_hf_mirror()
    assert st["mode"] == "manual"
    assert st["auto_opt_out"] is True


def test_put_mode_auto_clears_explicit_and_races(settings_mod, monkeypatch):
    import services.endpoint_race as er

    calls = []

    def fake_probe(endpoint, timeout=None):
        calls.append(endpoint)
        return er.ProbeResult(endpoint=endpoint, reachable=True,
                              latency_ms=40.0 if endpoint == er.CANONICAL_ENDPOINT else 90.0)

    monkeypatch.setattr(er, "probe_endpoint", fake_probe)
    # Start from an explicit mirror, then switch to Auto.
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com"))
    st = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="", mode="auto"))
    assert st["mode"] == "auto"
    assert st["configured"] == ""
    assert "HF_ENDPOINT" not in os.environ
    assert st["restart_required"] is True  # a persisted endpoint was cleared
    # Switching to Auto raced immediately so the panel shows a real pick.
    assert calls and st["auto"]["endpoint"] == er.CANONICAL_ENDPOINT
    assert st["auto"]["latency_ms"] == 40.0


def test_put_manual_official_is_an_explicit_choice(settings_mod):
    """Explicitly picking the official endpoint (mode=manual, empty url) must
    stick as manual — never silently become Auto."""
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="", mode="manual"))
    st = settings_mod.get_hf_mirror()
    assert st["mode"] == "manual"
    import services.endpoint_race as er
    assert er.mode() == "manual"


def test_put_mode_auto_clears_pref_fallback(settings_mod):
    """The `hf_endpoint` pref (bootstrap-installer fallback) counts as explicit
    config too — switching to Auto must clear it."""
    from core import prefs
    prefs.set_("hf_endpoint", "https://custom.example")
    st = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(mode="auto"))
    assert st["mode"] == "auto"
    assert prefs.get("hf_endpoint") is None


def test_clear_to_official_also_clears_pref_fallback(settings_mod):
    """Switching to official (manual, empty url) must also drop the legacy
    `hf_endpoint` pref fallback — the download paths resolve it as an explicit
    mirror, so leaving it behind meant "switch to official" didn't actually
    switch (the wizard's mirror rescue kept failing on the dead mirror)."""
    from core import prefs
    import services.endpoint_race as er

    prefs.set_("hf_endpoint", "https://hf-mirror.com")
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="", mode="manual"))
    assert prefs.get("hf_endpoint") is None
    assert er.explicit_endpoint() == ""


def test_mirror_change_clears_install_cooldowns(settings_mod):
    """A mirror change resets the failed-recently install cooldowns: the
    wizard's switch-and-retry flow retries the failed download immediately,
    and a 429 there would dead-end it (the cooldown guards against hammering
    a broken network — which the endpoint switch just changed)."""
    from api.routers.setup import download as dl

    dl._install_cooldowns["org/model"] = 10.0 ** 12  # far future — never sweeps
    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com"))
    assert dl._install_cooldowns == {}


def test_put_rejects_unknown_mode(settings_mod):
    from fastapi import HTTPException
    with pytest.raises(HTTPException) as ei:
        settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="", mode="turbo"))
    assert ei.value.status_code == 400


def test_test_endpoint_forces_rerace(settings_mod, monkeypatch):
    import services.endpoint_race as er

    calls = []

    def fake_probe(endpoint, timeout=None):
        calls.append(endpoint)
        return er.ProbeResult(endpoint=endpoint, reachable=(endpoint != er.CANONICAL_ENDPOINT),
                              latency_ms=None if endpoint == er.CANONICAL_ENDPOINT else 90.0)

    monkeypatch.setattr(er, "probe_endpoint", fake_probe)
    st = settings_mod.test_hf_mirror()
    assert calls  # "Test again" always probes fresh
    assert st["mode"] == "auto"
    assert st["auto"]["endpoint"] == er.COMMUNITY_MIRROR
    # A second test re-probes again (force), never serves the cache.
    n = len(calls)
    settings_mod.test_hf_mirror()
    assert len(calls) > n


def test_test_endpoint_is_noop_in_manual_mode(settings_mod, monkeypatch):
    import services.endpoint_race as er

    def boom(endpoint, timeout=None):
        raise AssertionError("manual mode — test must not probe")

    settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com"))
    monkeypatch.setattr(er, "probe_endpoint", boom)
    st = settings_mod.test_hf_mirror()
    assert st["mode"] == "manual"
    assert st["configured"] == "https://hf-mirror.com"


def test_leaving_an_applied_automatic_mirror_requires_a_restart(settings_mod):
    """Loads already use the automatic pick; choosing another endpoint needs a restart."""
    os.environ["HF_ENDPOINT"] = os.environ["OMNIVOICE_HF_ENDPOINT_AUTO"] = "https://hf-mirror.com"
    st = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url=""))
    assert st["restart_required"] is True
    assert "OMNIVOICE_HF_ENDPOINT_AUTO" not in os.environ


def test_choosing_the_applied_automatic_mirror_needs_no_restart(settings_mod):
    os.environ["HF_ENDPOINT"] = os.environ["OMNIVOICE_HF_ENDPOINT_AUTO"] = "https://hf-mirror.com"
    st = settings_mod.set_hf_mirror(settings_mod._HFMirrorBody(url="https://hf-mirror.com"))
    assert st["restart_required"] is False
    assert "OMNIVOICE_HF_ENDPOINT_AUTO" not in os.environ
    assert st["configured"] == "https://hf-mirror.com"
