"""Settings → Storage report (services.storage_report + the /api/settings/storage
endpoint): du-style category sizes, threshold warnings, cache/refresh behavior,
and the bounded-walk timeout path.
"""
from __future__ import annotations

import asyncio
import collections
import os
from pathlib import Path

import pytest

from services import storage_report

_Usage = collections.namedtuple("usage", "total used free")
_GB = 1024 ** 3


@pytest.fixture(autouse=True)
def _fresh_cache():
    storage_report.clear_cache()
    yield
    storage_report.clear_cache()


def _write(path, size):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(b"x" * size)


@pytest.fixture
def roots(tmp_path):
    """A miniature on-disk layout with known sizes for every category."""
    data = tmp_path / "data"
    _write(str(data / "voices" / "a.wav"), 100)
    _write(str(data / "voices" / "b.wav"), 50)
    _write(str(data / "outputs" / "o.wav"), 200)
    _write(str(data / "dub_jobs" / "j1" / "seg_0.wav"), 30)
    _write(str(data / "omnivoice.db"), 400)
    _write(str(data / "omnivoice.db-wal"), 40)
    _write(str(data / "omnivoice.log"), 10)
    _write(str(data / "crash_log.txt"), 5)
    _write(str(data / "prefs.json"), 7)  # → "other"

    hf = tmp_path / "hf"
    _write(str(hf / "hub" / "models--Org--Big" / "blobs" / "w.bin"), 900)
    _write(str(hf / "hub" / "models--Org--Small" / "blobs" / "w.bin"), 100)
    _write(str(hf / "hub" / "version.txt"), 3)  # non-model remainder

    engines = tmp_path / "engines"
    # A real sidecar install = venv + git checkout + multi-GB weights; the whole
    # <id> dir is its footprint, so all three of these count.
    _write(str(engines / "indextts" / ".venv" / "lib" / "x.py"), 60)
    _write(str(engines / "indextts" / "main.py"), 40)          # checkout
    _write(str(engines / "indextts" / "checkpoints" / "w.bin"), 900)  # weights
    _write(str(engines / "_echo" / "no_venv.py"), 999)  # no .venv → not an install, excluded

    tmp = tmp_path / "tmp"
    _write(str(tmp / "omnivoice_frames_abc" / "f.png"), 25)
    _write(str(tmp / "unrelated" / "f.bin"), 999)  # not app-owned — excluded

    return {
        "data_dir": str(data),
        "hf_cache_dir": str(hf),
        "engines_dir": str(engines),
        "temp_root": str(tmp),
    }


def _build(roots, **kw):
    kw.setdefault("app_venv", None)
    return storage_report.build_report(**roots, **kw)


def _cat(report, cid):
    return next(c for c in report["categories"] if c["id"] == cid)


# ── Sizes ───────────────────────────────────────────────────────────────────

def test_category_sizes_computed(roots):
    r = _build(roots)

    hf = _cat(r, "hf_cache")
    assert hf["bytes"] == 900 + 100 + 3
    assert hf["complete"] is True
    # Top models: sorted desc, humanized org/name
    assert hf["items"][0] == {"name": "Org/Big", "bytes": 900}
    assert hf["items"][1] == {"name": "Org/Small", "bytes": 100}

    data = _cat(r, "data")
    child = {c["id"]: c["bytes"] for c in data["children"]}
    assert child["voices"] == 150
    assert child["outputs"] == 200
    assert child["dub_jobs"] == 30
    assert child["database"] == 440   # db + wal
    assert child["logs"] == 15        # omnivoice.log + crash_log.txt
    assert child["other"] == 7        # prefs.json
    assert data["bytes"] == sum(child.values())

    venvs = _cat(r, "engine_venvs")
    assert venvs["bytes"] == 1000     # whole install: .venv 60 + checkout 40 + weights 900
    assert venvs["items"] == [{"name": "indextts", "bytes": 1000}]

    tmp = _cat(r, "temp")
    assert tmp["bytes"] == 25         # only omnivoice* entries


def test_app_venv_included(roots, tmp_path):
    venv = tmp_path / "appvenv"
    _write(str(venv / "bin" / "python"), 80)
    r = _build(roots, app_venv=str(venv))
    venvs = _cat(r, "engine_venvs")
    assert venvs["bytes"] == 1000 + 80
    assert {"name": "app", "bytes": 80} in venvs["items"]


def test_sidecar_under_data_dir_is_counted_once_not_double(tmp_path):
    # The real layout: sidecars install to DATA_DIR/engines/<id>. The engine-venv
    # category owns that subtree; the data dir's "other" must NOT also count it,
    # or a multi-GB IndexTTS-2 install inflates the report by its own size twice.
    data = tmp_path / "data"
    _write(str(data / "voices" / "v.wav"), 100)
    _write(str(data / "engines" / "indextts2" / ".venv" / "x"), 300)
    _write(str(data / "engines" / "indextts2" / "checkpoints" / "w.bin"), 5000)

    r = storage_report.build_report(
        data_dir=str(data),
        hf_cache_dir=str(tmp_path / "hf"),
        engines_dir=str(data / "engines"),  # child of data_dir, as in production
        temp_root=str(tmp_path / "tmp"),
        app_venv=None,
    )
    data_cat = _cat(r, "data")
    other = next(c["bytes"] for c in data_cat["children"] if c["id"] == "other")
    venvs = _cat(r, "engine_venvs")

    assert venvs["bytes"] == 5300, "engine category owns the whole sidecar dir"
    assert other == 0, "the sidecar must not also land in data → other"
    # And it appears exactly once in the grand total.
    grand = sum(c["bytes"] for c in r["categories"])
    assert grand == 100 + 5300


def test_default_engines_dir_points_at_data_dir_not_the_source_tree(monkeypatch):
    # Guards the actual bug: default_engines_dir() used to return backend/engines
    # (built-in modules, no venvs) so sidecar installs were invisible.
    import core.config as config

    monkeypatch.setattr(config, "DATA_DIR", "/tmp/some-data-dir", raising=False)
    got = storage_report.default_engines_dir()
    assert got == str(Path("/tmp/some-data-dir") / "engines")
    assert "backend" not in got


def test_missing_dirs_are_zero_not_warnings(tmp_path):
    r = storage_report.build_report(
        data_dir=str(tmp_path / "nope-data"),
        hf_cache_dir=str(tmp_path / "nope-hf"),
        engines_dir=str(tmp_path / "nope-engines"),
        temp_root=str(tmp_path / "nope-tmp"),
    )
    for cid in ("hf_cache", "data", "engine_venvs", "temp"):
        cat = _cat(r, cid)
        assert cat["bytes"] == 0
        assert cat["complete"] is True
        assert cat["exists"] is False
    assert not [w for w in r["warnings"] if w["kind"] == "unreadable"]


def test_top_models_capped_at_ten(roots, tmp_path):
    hub = tmp_path / "hf" / "hub"
    for i in range(15):
        _write(str(hub / f"models--Org--M{i:02d}" / "w.bin"), 10 + i)
    r = _build(roots)
    items = _cat(r, "hf_cache")["items"]
    assert len(items) == storage_report.TOP_MODEL_COUNT
    assert items == sorted(items, key=lambda m: m["bytes"], reverse=True)


# ── Warnings ────────────────────────────────────────────────────────────────

def _with_disk(monkeypatch, total, used, free):
    monkeypatch.setattr(
        storage_report.shutil, "disk_usage", lambda p: _Usage(total, used, free)
    )


def test_warning_critical_below_min_free(roots, monkeypatch):
    _with_disk(monkeypatch, total=100 * _GB, used=95 * _GB, free=5 * _GB)
    r = _build(roots, min_free_gb=10)
    low = [w for w in r["warnings"] if w["kind"] == "low_disk"]
    assert len(low) == 1  # one volume in tmp → one warning, not four
    assert low[0]["severity"] == "critical"
    assert low[0]["free_gb"] == 5.0
    assert low[0]["min_free_gb"] == 10
    # >90% used volume holding data+hf also trips pressure
    assert [w for w in r["warnings"] if w["kind"] == "volume_pressure"]
    # critical sorts first
    assert r["warnings"][0]["severity"] == "critical"


def test_warning_low_below_twice_min(roots, monkeypatch):
    _with_disk(monkeypatch, total=1000 * _GB, used=985 * _GB, free=15 * _GB)
    r = _build(roots, min_free_gb=10)
    low = [w for w in r["warnings"] if w["kind"] == "low_disk"]
    assert len(low) == 1
    assert low[0]["severity"] == "low"


def test_no_warnings_when_roomy(roots, monkeypatch):
    _with_disk(monkeypatch, total=1000 * _GB, used=500 * _GB, free=500 * _GB)
    r = _build(roots, min_free_gb=10)
    assert r["warnings"] == []


def test_volume_pressure_over_90_percent(roots, monkeypatch):
    # Plenty of absolute free space (40 GB) but the volume is 96% full.
    _with_disk(monkeypatch, total=1000 * _GB, used=960 * _GB, free=40 * _GB)
    r = _build(roots, min_free_gb=10)
    kinds = {w["kind"] for w in r["warnings"]}
    assert kinds == {"volume_pressure"}
    w = next(w for w in r["warnings"] if w["kind"] == "volume_pressure")
    assert w["used_percent"] == 96.0
    assert "data" in w["roots"] and "hf_cache" in w["roots"]


# ── Timeout → partial + unreadable warning ──────────────────────────────────

def test_timeout_returns_partial_with_unreadable_warning(roots):
    r = _build(roots, category_timeout=-1)  # deadline already passed
    hf = _cat(r, "hf_cache")
    assert hf["complete"] is False
    timeouts = [w for w in r["warnings"] if w["kind"] == "unreadable"]
    assert timeouts, "expected unreadable warnings on timeout"
    assert all(w["severity"] == "warning" for w in timeouts)
    hf_warn = next(w for w in timeouts if w["category_id"] == "hf_cache")
    assert hf_warn["reason"] == "timeout"
    assert hf_warn["path"] == roots["hf_cache_dir"]


def test_unreadable_dir_flagged(roots, monkeypatch):
    real_walk = storage_report.os.walk

    def flaky_walk(path, onerror=None, **kw):
        if path == os.path.join(roots["data_dir"], "voices") and onerror:
            onerror(PermissionError(13, "denied", path))
            return iter(())
        return real_walk(path, onerror=onerror, **kw)

    monkeypatch.setattr(storage_report.os, "walk", flaky_walk)
    r = _build(roots)
    perm = [w for w in r["warnings"] if w["kind"] == "unreadable" and w["reason"] == "permission"]
    assert len(perm) == 1
    assert perm[0]["category_id"] == "data"
    assert perm[0]["path"] == os.path.join(roots["data_dir"], "voices")


# ── Cache + refresh ─────────────────────────────────────────────────────────

def test_cache_hit_and_refresh(roots, monkeypatch):
    calls = {"n": 0}
    real_build = storage_report.build_report

    def counting_build(**kw):
        calls["n"] += 1
        return real_build(**kw)

    monkeypatch.setattr(storage_report, "build_report", counting_build)

    r1 = storage_report.get_report(**roots)
    assert r1["cached"] is False
    r2 = storage_report.get_report(**roots)
    assert r2["cached"] is True
    assert calls["n"] == 1  # served from cache

    r3 = storage_report.get_report(**roots, refresh=True)
    assert r3["cached"] is False
    assert calls["n"] == 2  # refresh forces a rescan


def test_cache_expires_after_ttl(roots, monkeypatch):
    storage_report.get_report(**roots)
    # Age the cache entry past the TTL.
    with storage_report._cache_lock:
        storage_report._cache["ts"] -= storage_report.CACHE_TTL_SECONDS + 1
    r = storage_report.get_report(**roots)
    assert r["cached"] is False


def test_cache_key_change_recomputes(roots, tmp_path):
    storage_report.get_report(**roots)
    other = dict(roots, data_dir=str(tmp_path / "elsewhere"))
    r = storage_report.get_report(**other)
    assert r["cached"] is False


# ── Endpoint wiring ─────────────────────────────────────────────────────────

def test_endpoint_returns_report_shape(roots, monkeypatch):
    from api.routers import settings as s

    monkeypatch.setattr(s, "_effective_models_dir", lambda: roots["hf_cache_dir"])
    monkeypatch.setattr("core.config.DATA_DIR", roots["data_dir"])
    res = asyncio.run(s.get_storage_report(refresh=True))
    assert {"generated_at", "min_free_gb", "volumes", "categories", "warnings", "cached"} <= set(res)
    assert [c["id"] for c in res["categories"]] == ["hf_cache", "data", "engine_venvs", "temp"]
    # min_free_gb single-sourced from the setup wizard constant
    from api.routers.setup.wizard import MIN_FREE_GB
    assert res["min_free_gb"] == MIN_FREE_GB
    assert res["volumes"] and {"total_bytes", "free_bytes", "used_percent", "roots"} <= set(res["volumes"][0])


# ── Temp-files cleanup (Settings → Storage → "Clear temp files") ────────────

def test_clear_temp_removes_only_app_owned_entries(tmp_path):
    tmp = tmp_path / "tmp"
    _write(str(tmp / "omnivoice_frames_abc" / "f.png"), 25)
    _write(str(tmp / "omnivoice.chunk"), 10)
    _write(str(tmp / "unrelated" / "keep.bin"), 999)
    _write(str(tmp / "keep.txt"), 7)

    res = storage_report.clear_temp(str(tmp))

    assert sorted(res["removed"]) == ["omnivoice.chunk", "omnivoice_frames_abc"]
    assert res["freed_bytes"] == 35
    assert res["errors"] == []
    assert not (tmp / "omnivoice_frames_abc").exists()
    assert not (tmp / "omnivoice.chunk").exists()
    # Anything not app-owned is never touched.
    assert (tmp / "unrelated" / "keep.bin").exists()
    assert (tmp / "keep.txt").exists()


def test_clear_temp_unlinks_symlinks_without_following(tmp_path, symlink_or_skip):
    tmp = tmp_path / "tmp"
    target = tmp_path / "precious"
    _write(str(target / "data.bin"), 50)
    os.makedirs(tmp, exist_ok=True)
    symlink_or_skip(str(tmp / "omnivoice_link"), str(target), target_is_directory=True)

    res = storage_report.clear_temp(str(tmp))

    assert res["removed"] == ["omnivoice_link"]
    assert not os.path.lexists(tmp / "omnivoice_link")
    # The symlink target's contents must survive.
    assert (target / "data.bin").exists()


def test_clear_temp_empty_dir_is_a_noop(tmp_path):
    tmp = tmp_path / "tmp"
    os.makedirs(tmp, exist_ok=True)
    res = storage_report.clear_temp(str(tmp))
    assert res == {"removed": [], "freed_bytes": 0, "errors": []}


def test_clear_temp_endpoint_clears_and_invalidates_cache(roots, monkeypatch, tmp_path):
    from api.routers import settings as s

    tmp = tmp_path / "endpoint_tmp"
    _write(str(tmp / "omnivoice_job" / "seg.wav"), 40)
    monkeypatch.setattr("tempfile.gettempdir", lambda: str(tmp))

    # Prime the report cache, then clear — the endpoint must invalidate it.
    monkeypatch.setattr(s, "_effective_models_dir", lambda: roots["hf_cache_dir"])
    monkeypatch.setattr("core.config.DATA_DIR", roots["data_dir"])
    asyncio.run(s.get_storage_report(refresh=True))

    res = asyncio.run(s.clear_temp_files())
    assert res["removed"] == ["omnivoice_job"]
    assert res["freed_bytes"] == 40
    assert not (tmp / "omnivoice_job").exists()
    assert storage_report._cache["report"] is None  # cache invalidated


# ── Deadline is enforced between file entries (#2564) ───────────────────────

class _TickingClock:
    """time stand-in whose monotonic() advances one second per call, so a scan's
    budget is spent by how many entries it examines — no sleeps, no real time."""

    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        self.now += 1.0
        return self.now

    time = staticmethod(__import__("time").time)


@pytest.fixture
def ticking(monkeypatch):
    clock = _TickingClock()
    monkeypatch.setattr(storage_report, "time", clock)
    return clock


def test_flat_directory_scan_stops_at_the_deadline(tmp_path, ticking):
    flat = tmp_path / "flat"
    for i in range(60):
        _write(str(flat / f"f{i}.bin"), 10)
    total, complete, _err = storage_report._dir_size(str(flat), deadline=10.5)
    assert complete is False
    assert 0 < total < 600, "partial measured total, not the whole directory"


def test_flat_directory_within_budget_is_complete(tmp_path, ticking):
    flat = tmp_path / "flat"
    for i in range(5):
        _write(str(flat / f"f{i}.bin"), 10)
    assert storage_report._dir_size(str(flat), deadline=1000) == (50, True, None)


def test_loose_data_entries_respect_the_budget_and_mark_other_incomplete(roots, ticking):
    for i in range(40):
        _write(os.path.join(roots["data_dir"], f"loose{i}.bin"), 100)
    r = _build(roots, category_timeout=20.5)
    data = _cat(r, "data")
    other = next(c for c in data["children"] if c["id"] == "other")
    assert other["complete"] is False
    assert 0 < other["bytes"] < 7 + 40 * 100
    assert data["complete"] is False
    assert any(
        w["kind"] == "unreadable" and w["category_id"] == "data" and w["reason"] == "timeout"
        for w in r["warnings"]
    )


def test_other_is_complete_when_every_entry_is_measured(roots):
    other = next(c for c in _cat(_build(roots), "data")["children"] if c["id"] == "other")
    assert other["complete"] is True and other["bytes"] == 7


def test_unreadable_entry_marks_other_incomplete(roots, monkeypatch):
    _write(os.path.join(roots["data_dir"], "mystery", "x.bin"), 5)
    real = storage_report._dir_size

    def fake(path, deadline):
        if os.path.basename(path) == "mystery":
            return 3, True, path
        return real(path, deadline)

    monkeypatch.setattr(storage_report, "_dir_size", fake)
    r = _build(roots)
    other = next(c for c in _cat(r, "data")["children"] if c["id"] == "other")
    assert other["complete"] is False and other["bytes"] == 7 + 3
    assert any(w["reason"] == "permission" for w in r["warnings"])
