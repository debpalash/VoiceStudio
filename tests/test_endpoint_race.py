"""Automatic Hugging Face endpoint selection (services.endpoint_race).

Policy matrices (reachable/unreachable/latency/stickiness), locale/timezone
probe-order hints, decision caching + re-race triggers, explicit-setting
precedence, and the failover-once guard — all with mocked probers, zero real
network (the suite-wide conftest guard additionally pins the module probers).
"""
from __future__ import annotations

import os
import sys
import time

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend"))


@pytest.fixture
def er(monkeypatch, tmp_path):
    """endpoint_race with isolated prefs, no explicit endpoint, fresh guards."""
    from core import prefs
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("HF_ENDPOINT", raising=False)
    monkeypatch.delenv("OMNIVOICE_HF_ENDPOINT_MODE", raising=False)
    import services.endpoint_race as er_mod
    monkeypatch.setattr(er_mod, "_FAILOVER_ATTEMPTED", set())
    return er_mod


def make_prober(er_mod, latencies):
    """Prober from {endpoint: latency_ms | None (unreachable)}; counts calls."""
    calls: list[str] = []

    def prober(endpoint, timeout=None):
        calls.append(endpoint)
        lat = latencies.get(endpoint)
        if lat is None:
            return er_mod.ProbeResult(endpoint=endpoint, reachable=False, error="timeout")
        return er_mod.ProbeResult(endpoint=endpoint, reachable=True, latency_ms=lat)

    prober.calls = calls
    return prober


_NO_THROUGHPUT = lambda endpoint, timeout=None: None  # noqa: E731


# ── Locale/timezone probe-ORDER hint ────────────────────────────────────────

def test_cn_hint_from_locale_strings(er):
    assert er.cn_probe_hint(["zh_CN.UTF-8"], []) is True
    assert er.cn_probe_hint(["zh-CN"], []) is True
    assert er.cn_probe_hint(["Chinese (Simplified)_China.936"], []) is True  # Windows
    assert er.cn_probe_hint(["en_US.UTF-8"], []) is False
    # zh_TW / zh_HK must NOT hint mainland-China probe order.
    assert er.cn_probe_hint(["zh_TW.UTF-8"], []) is False


def test_cn_hint_from_timezone(er):
    assert er.cn_probe_hint([], ["Asia/Shanghai"]) is True
    assert er.cn_probe_hint([], ["China Standard Time"]) is True  # Windows tzname
    assert er.cn_probe_hint([], ["Europe/Berlin"]) is False
    # Bare "CST" is ambiguous (US Central) — must NOT hint China.
    assert er.cn_probe_hint([], ["CST"]) is False


def test_candidates_order_follows_hint(er):
    assert er.candidates(cn_hint=False) == [er.CANONICAL_ENDPOINT, er.COMMUNITY_MIRROR]
    assert er.candidates(cn_hint=True) == [er.COMMUNITY_MIRROR, er.CANONICAL_ENDPOINT]


def test_hint_only_reorders_never_drops(er):
    """The hint is cosmetic: every candidate is probed either way."""
    for hint in (True, False):
        prober = make_prober(er, {er.CANONICAL_ENDPOINT: 50, er.COMMUNITY_MIRROR: 40})
        er.race(endpoints=er.candidates(cn_hint=hint), prober=prober,
                throughput_prober=_NO_THROUGHPUT)
        assert set(prober.calls) == {er.CANONICAL_ENDPOINT, er.COMMUNITY_MIRROR}


@pytest.mark.parametrize(
    "endpoint",
    [
        "file:///etc/passwd",
        "http://huggingface.co",
        "https://huggingface.co.evil.example",
        "https://huggingface.co@evil.example",
        "https://huggingface.co:444",
        "https://huggingface.co/model",
        "https://huggingface.co?redirect=file:///etc/passwd",
    ],
)
def test_probe_rejects_unapproved_origins(er, endpoint):
    # The suite-wide network guard replaces the actual prober, so exercise the
    # validation chokepoint directly. Both real network helpers call it before
    # constructing a Request or reaching urlopen.
    assert er._is_allowed_probe_endpoint(endpoint) is False


@pytest.mark.parametrize("endpoint", ["https://huggingface.co", "https://hf-mirror.com/"])
def test_probe_allows_only_shipped_https_origins(er, endpoint):
    assert er._is_allowed_probe_endpoint(endpoint) is True


# ── Decision policy matrix ──────────────────────────────────────────────────

def test_both_reachable_similar_latency_prefers_canonical(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 100, er.COMMUNITY_MIRROR: 60})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.CANONICAL_ENDPOINT
    assert d["reachable"] is True


def test_mirror_decisively_faster_wins(er):
    # ≥3× faster (boundary inclusive) — the anti-flapping stickiness rule.
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 300, er.COMMUNITY_MIRROR: 100})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.COMMUNITY_MIRROR


def test_mirror_faster_but_not_decisively_prefers_canonical(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 299, er.COMMUNITY_MIRROR: 100})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.CANONICAL_ENDPOINT


def test_canonical_unreachable_mirror_wins(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 500})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.COMMUNITY_MIRROR
    assert d["reachable"] is True


def test_nothing_reachable_falls_back_to_canonical_unreachable(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: None})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.CANONICAL_ENDPOINT
    assert d["reachable"] is False
    assert len(d["results"]) == 2


def test_throughput_tiebreak_confirms_mirror_win(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 400, er.COMMUNITY_MIRROR: 100})
    tp = {er.CANONICAL_ENDPOINT: 1_000_000.0, er.COMMUNITY_MIRROR: 5_000_000.0}
    d = er.race(prober=prober, throughput_prober=lambda ep, timeout=None: tp[ep])
    assert d["endpoint"] == er.COMMUNITY_MIRROR


def test_throughput_tiebreak_vetoes_latency_noise(er):
    """Mirror decisively faster on latency but slower on actual throughput —
    canonical keeps the win (throughput is what a multi-GB download feels)."""
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 400, er.COMMUNITY_MIRROR: 100})
    tp = {er.CANONICAL_ENDPOINT: 5_000_000.0, er.COMMUNITY_MIRROR: 1_000_000.0}
    d = er.race(prober=prober, throughput_prober=lambda ep, timeout=None: tp[ep])
    assert d["endpoint"] == er.CANONICAL_ENDPOINT


def test_throughput_probe_failure_keeps_latency_verdict(er):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 400, er.COMMUNITY_MIRROR: 100})
    d = er.race(prober=prober, throughput_prober=_NO_THROUGHPUT)
    assert d["endpoint"] == er.COMMUNITY_MIRROR


def test_throughput_not_probed_when_canonical_wins_latency(er):
    """The ranged-GET sample only runs to confirm a decisive mirror win."""
    tp_calls = []

    def tp(endpoint, timeout=None):
        tp_calls.append(endpoint)
        return 1.0

    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 100, er.COMMUNITY_MIRROR: 60})
    er.race(prober=prober, throughput_prober=tp)
    assert tp_calls == []


# ── Mode / explicit-setting precedence ──────────────────────────────────────

def test_default_mode_is_auto(er):
    assert er.mode() == "auto"


def test_env_endpoint_forces_manual(er, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://hf-mirror.com")
    assert er.mode() == "manual"
    assert er.explicit_endpoint() == "https://hf-mirror.com"
    assert er.ensure_decision() is None
    assert er.effective_endpoint() == "https://hf-mirror.com"


def test_pref_endpoint_forces_manual(er):
    from core import prefs
    prefs.set_("hf_endpoint", "https://custom.example")
    assert er.mode() == "manual"
    assert er.effective_endpoint() == "https://custom.example"
    assert er.ensure_decision() is None


def test_env_opt_out_forces_manual(er, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_HF_ENDPOINT_MODE", "manual")
    assert er.mode() == "manual"
    assert er.ensure_decision() is None
    assert er.effective_endpoint() is None  # canonical, no probes


def test_mode_pref_manual_disables_auto(er):
    er.set_mode_pref("manual")
    assert er.mode() == "manual"
    assert er.ensure_decision() is None


def test_explicit_official_choice_is_manual_not_auto(er):
    """Explicitly picking the official endpoint in Settings (mode pref
    'manual', empty url) must never be migrated to Auto."""
    er.set_mode_pref("manual")
    # Even with a cached decision pointing at the mirror, manual-official
    # means canonical:
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True,
        "latency_ms": 10.0, "checked_at": time.time(), "results": [],
    })
    assert er.effective_endpoint() is None


# ── Decision cache: stickiness + re-race triggers ───────────────────────────

def test_first_run_races_then_cache_sticks(er, monkeypatch):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 50, er.COMMUNITY_MIRROR: 80})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    monkeypatch.setattr(er, "throughput_probe", _NO_THROUGHPUT)

    d1 = er.ensure_decision()
    assert d1["endpoint"] == er.CANONICAL_ENDPOINT
    assert len(prober.calls) == 2

    d2 = er.ensure_decision()  # fresh cache → no new probes
    assert d2 == d1
    assert len(prober.calls) == 2


def test_stale_decision_triggers_rerace(er, monkeypatch):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 50, er.COMMUNITY_MIRROR: 80})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    monkeypatch.setattr(er, "throughput_probe", _NO_THROUGHPUT)
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True, "latency_ms": 10.0,
        "checked_at": time.time() - er.DECISION_MAX_AGE_S - 60, "results": [],
    })
    d = er.ensure_decision()
    assert prober.calls  # stale → re-raced
    assert d["endpoint"] == er.CANONICAL_ENDPOINT


def test_force_triggers_rerace_despite_fresh_cache(er, monkeypatch):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 50, er.COMMUNITY_MIRROR: 80})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    monkeypatch.setattr(er, "throughput_probe", _NO_THROUGHPUT)
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True, "latency_ms": 10.0,
        "checked_at": time.time(), "results": [],
    })
    d = er.ensure_decision(force=True)
    assert prober.calls
    assert d["endpoint"] == er.CANONICAL_ENDPOINT


def test_decision_persists_via_prefs(er, monkeypatch):
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 90})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    er.ensure_decision()
    from core import prefs
    stored = prefs.get("hf_endpoint_auto")
    assert stored["endpoint"] == er.COMMUNITY_MIRROR
    assert er.cached_decision() == stored


def test_cached_decision_rejects_malformed(er):
    from core import prefs
    prefs.set_("hf_endpoint_auto", {"garbage": True})
    assert er.cached_decision() is None
    prefs.set_("hf_endpoint_auto", "not-a-dict")
    assert er.cached_decision() is None


# ── effective_endpoint (the per-download hot path — never probes) ───────────

def test_effective_endpoint_uses_cached_mirror_win(er):
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True,
        "latency_ms": 42.0, "checked_at": time.time(), "results": [],
    })
    assert er.effective_endpoint() == er.COMMUNITY_MIRROR


def test_effective_endpoint_canonical_win_means_none(er):
    er._store_decision({
        "endpoint": er.CANONICAL_ENDPOINT, "reachable": True,
        "latency_ms": 42.0, "checked_at": time.time(), "results": [],
    })
    assert er.effective_endpoint() is None


def test_effective_endpoint_never_probes(er, monkeypatch):
    def boom(endpoint, timeout=None):
        raise AssertionError("effective_endpoint must not probe")

    monkeypatch.setattr(er, "probe_endpoint", boom)
    assert er.effective_endpoint() is None  # no cache → canonical, no probes


def test_effective_endpoint_ignores_unreachable_decision(er):
    er._store_decision({
        "endpoint": er.CANONICAL_ENDPOINT, "reachable": False,
        "latency_ms": None, "checked_at": time.time(), "results": [],
    })
    assert er.effective_endpoint() is None


# ── Failover after a network-classified download failure ────────────────────

_NET_ERR = "connection reset by peer"


def _seed_canonical_decision(er):
    er._store_decision({
        "endpoint": er.CANONICAL_ENDPOINT, "reachable": True,
        "latency_ms": 40.0, "checked_at": time.time(), "results": [],
    })


def test_failover_reraces_and_switches_once(er, monkeypatch):
    _seed_canonical_decision(er)
    # The network changed: canonical is now dead, mirror answers.
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 90})
    monkeypatch.setattr(er, "probe_endpoint", prober)

    assert er.reselect_after_failure("org/repo", _NET_ERR) is True
    assert er.effective_endpoint() == er.COMMUNITY_MIRROR
    # Once per repo per process — a network that stays broken can't loop.
    prober.calls.clear()
    assert er.reselect_after_failure("org/repo", _NET_ERR) is False
    assert prober.calls == []


def test_failover_ignores_non_network_failures(er, monkeypatch):
    _seed_canonical_decision(er)
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 90})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    assert er.reselect_after_failure("org/repo", "CUDA out of memory") is False
    assert prober.calls == []  # no probes for a non-network failure


def test_failover_noop_when_endpoint_unchanged(er, monkeypatch):
    _seed_canonical_decision(er)
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: 40, er.COMMUNITY_MIRROR: 90})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    monkeypatch.setattr(er, "throughput_probe", _NO_THROUGHPUT)
    assert er.reselect_after_failure("org/repo", _NET_ERR) is False


def test_failover_respects_explicit_setting(er, monkeypatch):
    monkeypatch.setenv("HF_ENDPOINT", "https://custom.example")
    prober = make_prober(er, {er.CANONICAL_ENDPOINT: None, er.COMMUNITY_MIRROR: 90})
    monkeypatch.setattr(er, "probe_endpoint", prober)
    assert er.reselect_after_failure("org/repo", _NET_ERR) is False
    assert prober.calls == []
    assert er.effective_endpoint() == "https://custom.example"


# ── Wiring: download paths resolve through the race ─────────────────────────

def test_download_endpoint_uses_auto_decision(er):
    from api.routers.setup.download import _download_endpoint
    assert _download_endpoint() is None  # no cache → canonical
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True,
        "latency_ms": 42.0, "checked_at": time.time(), "results": [],
    })
    assert _download_endpoint() == er.COMMUNITY_MIRROR


def test_download_endpoint_explicit_env_wins(er, monkeypatch):
    from api.routers.setup.download import _download_endpoint
    er._store_decision({
        "endpoint": er.COMMUNITY_MIRROR, "reachable": True,
        "latency_ms": 42.0, "checked_at": time.time(), "results": [],
    })
    monkeypatch.setenv("HF_ENDPOINT", "https://custom.example")
    assert _download_endpoint() == "https://custom.example"


@pytest.mark.parametrize("endpoint", ["https://example.invalid", "http://hf-mirror.com", "https://hf-mirror.com/path", "https://user@hf-mirror.com", "https://hf-mirror.com:8443", "https://[", "file:///tmp/model"])
def test_auto_cache_rejects_unapproved_origins(er, endpoint):
    from core import prefs

    prefs.set_(er._DECISION_PREF, {"endpoint": endpoint, "checked_at": time.time(), "reachable": True})
    assert er.cached_decision() is None
    assert er.effective_endpoint() is None


@pytest.mark.parametrize("checked_at", [float("nan"), float("inf"), -float("inf"), 10 ** 400])
def test_auto_cache_rejects_invalid_timestamps(er, checked_at):
    from core import prefs

    prefs.set_(er._DECISION_PREF, {"endpoint": er.COMMUNITY_MIRROR, "checked_at": checked_at, "reachable": True})
    assert er.cached_decision() is None
    assert er.effective_endpoint() is None


def test_auto_cache_allowlist_does_not_restrict_explicit_endpoint(er):
    from core import prefs

    prefs.set_("hf_endpoint", "https://private.example.invalid")
    assert er.effective_endpoint() == "https://private.example.invalid"
