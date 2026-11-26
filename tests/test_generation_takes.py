"""Generation takes — starred flag + takes retention + safe file cleanup.

Four layers:
  * Migration 0009 adds ``generation_history.starred`` to a pre-takes user DB,
    is idempotent, and converges to the same table shape as a fresh
    ``_BASE_SCHEMA`` install (dual-path discipline). A DB where alembic can't
    run heals the column via ``ensure_schema()`` (#552/#547 class).
  * Star/unstar API contract: round-trip, 404 on a pruned/deleted take, and
    the schema self-heal on a pre-migration DB (#710 class).
  * Retention cap: the oldest UNstarred rows over the cap are pruned together
    with their WAVs; starred takes and files still referenced by a surviving
    row are never touched; 0 disables pruning.
  * Delete-take file safety: the WAV goes only when no other history row
    references it.
"""
import os
import sqlite3
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import core.db as _db
from core.db import _BASE_SCHEMA, ensure_schema


def _repo_root() -> str:
    root = os.path.abspath(os.path.dirname(__file__))
    while root and root != "/" and not os.path.isfile(os.path.join(root, "alembic.ini")):
        root = os.path.dirname(root)
    assert os.path.isfile(os.path.join(root, "alembic.ini")), "alembic.ini not found"
    return root


def _run_alembic(direction, db_path, target="head"):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    (command.upgrade if direction == "upgrade" else command.downgrade)(cfg, target)


def _stamp(db_path, rev):
    from alembic import command
    from alembic.config import Config

    cfg = Config(os.path.join(_repo_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    command.stamp(cfg, rev)


def _cols(db_path, table="generation_history"):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


# The generation_history shape v0.3.x users have on disk today — pre-`starred`.
_PRE_TAKES_HISTORY = """
    CREATE TABLE generation_history (
        id TEXT PRIMARY KEY,
        text TEXT,
        mode TEXT,
        language TEXT,
        instruct TEXT,
        profile_id TEXT,
        audio_path TEXT,
        duration_seconds REAL,
        generation_time REAL,
        seed INTEGER DEFAULT NULL,
        created_at REAL
    );
"""


def _seed_pre_takes_db(path):
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_PRE_TAKES_HISTORY)
        conn.execute(
            "INSERT INTO generation_history (id, text, mode, audio_path, created_at) "
            "VALUES ('old1', 'hello', 'clone', 'old1.wav', 1.0)"
        )
        conn.commit()


# ── migration 0009 ────────────────────────────────────────────────────────────


def test_migration_0009_adds_starred_and_keeps_rows(tmp_path):
    dbf = tmp_path / "user.db"
    _seed_pre_takes_db(dbf)
    _stamp(str(dbf), "0008_pronunciation_dictionary")

    _run_alembic("upgrade", str(dbf))

    assert "starred" in _cols(dbf)
    with sqlite3.connect(str(dbf)) as conn:
        row = conn.execute(
            "SELECT text, COALESCE(starred, 0) FROM generation_history WHERE id='old1'"
        ).fetchone()
    assert row == ("hello", 0)


def test_migration_0009_idempotent_on_fresh_install(tmp_path):
    """Fresh installs get the column from _BASE_SCHEMA; re-running 0009 no-ops."""
    dbf = tmp_path / "fresh.db"
    with sqlite3.connect(str(dbf)) as conn:
        conn.executescript(_BASE_SCHEMA)
    _stamp(str(dbf), "0008_pronunciation_dictionary")
    _run_alembic("upgrade", str(dbf))  # must not raise on the existing column
    assert "starred" in _cols(dbf)


def test_migration_0009_downgrade_drops_column(tmp_path):
    dbf = tmp_path / "user.db"
    _seed_pre_takes_db(dbf)
    _stamp(str(dbf), "0008_pronunciation_dictionary")
    _run_alembic("upgrade", str(dbf))
    _run_alembic("downgrade", str(dbf), target="0008_pronunciation_dictionary")
    assert "starred" not in _cols(dbf)


def test_migration_and_base_schema_converge(tmp_path):
    """Migrated DB and fresh install agree on generation_history's columns."""
    mig = tmp_path / "mig.db"
    _seed_pre_takes_db(mig)
    _stamp(str(mig), "0008_pronunciation_dictionary")
    _run_alembic("upgrade", str(mig))

    fresh = tmp_path / "fresh.db"
    with sqlite3.connect(str(fresh)) as conn:
        conn.executescript(_BASE_SCHEMA)

    assert _cols(mig) == _cols(fresh)


def test_pre_migration_db_self_heals_starred(tmp_path, monkeypatch):
    """A DB where alembic never runs (#552/#547 class) still gains `starred`
    via the additive schema reconcile — no manual migration ever required."""
    dbf = tmp_path / "user.db"
    _seed_pre_takes_db(dbf)
    monkeypatch.setattr(_db, "get_db", lambda: _connect(dbf))

    assert "starred" not in _cols(dbf)
    ensure_schema()
    assert "starred" in _cols(dbf)
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute(
            "SELECT COALESCE(starred, 0) FROM generation_history WHERE id='old1'"
        ).fetchone()[0] == 0


# ── API fixtures ─────────────────────────────────────────────────────────────


def _connect(path):
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    return conn


def _patch_router_db(monkeypatch, gen, dbf):
    """Redirect the generation router's DB access to ``dbf``.

    Patched through ``gen.ensure_schema.__globals__`` — the module dict of the
    ``core.db`` instance the router closed over at ITS import — not via a
    fresh ``import core.db``: an earlier suite (tests/backend/**) purges
    ``core.*``/``api.*`` from ``sys.modules`` mid-run and never restores them
    (the #909/#932 leak), so the ``core.db`` this file imported at collection
    and the one the router's ``db_conn``/``ensure_schema`` read can diverge in
    a full-suite run. ``ensure_schema`` (a plain function) is the seam because
    ``db_conn`` is ``@contextmanager``-wrapped — its ``__globals__`` is
    contextlib's namespace, not ``core.db``'s.
    """
    monkeypatch.setitem(gen.ensure_schema.__globals__, "get_db", lambda: _connect(dbf))


@pytest.fixture
def api(tmp_path, monkeypatch):
    """TestClient over the generation router with a throwaway DB + outputs dir."""
    import api.routers.generation as gen

    dbf = tmp_path / "takes.db"
    _patch_router_db(monkeypatch, gen, dbf)
    gen.ensure_schema()

    outdir = tmp_path / "outputs"
    outdir.mkdir()
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(outdir))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(gen.router)
    return TestClient(app, client=("127.0.0.1", 50000)), dbf, outdir, gen


def _insert_take(dbf, outdir, take_id, created_at, starred=0, audio_path=None, with_file=True):
    audio_path = audio_path or f"{take_id}.wav"
    with sqlite3.connect(str(dbf)) as conn:
        conn.execute(
            "INSERT INTO generation_history (id, text, mode, audio_path, starred, created_at) "
            "VALUES (?, ?, 'clone', ?, ?, ?)",
            (take_id, f"text {take_id}", audio_path, starred, created_at),
        )
        conn.commit()
    wav = outdir / audio_path
    if with_file and not wav.exists():
        wav.write_bytes(b"RIFFfake")
    return wav


# ── star/unstar contract ─────────────────────────────────────────────────────


def test_star_unstar_roundtrip(api):
    client, dbf, outdir, _gen = api
    _insert_take(dbf, outdir, "t1", 1.0)

    r = client.put("/history/t1/starred", json={"starred": True})
    assert r.status_code == 200
    assert r.json() == {"id": "t1", "starred": True}
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT starred FROM generation_history WHERE id='t1'").fetchone()[0] == 1

    r = client.put("/history/t1/starred", json={"starred": False})
    assert r.status_code == 200
    assert r.json() == {"id": "t1", "starred": False}
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT starred FROM generation_history WHERE id='t1'").fetchone()[0] == 0


def test_star_unknown_take_404s(api):
    client, *_ = api
    r = client.put("/history/nope/starred", json={"starred": True})
    assert r.status_code == 404


def test_star_heals_pre_migration_schema(tmp_path, monkeypatch):
    """PUT starred on a pre-takes DB (no `starred` column) self-heals + succeeds
    — the #710 recovery contract extended to the new write."""
    import api.routers.generation as gen

    dbf = tmp_path / "old.db"
    _seed_pre_takes_db(dbf)
    _patch_router_db(monkeypatch, gen, dbf)
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(tmp_path))

    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    app = FastAPI()
    app.include_router(gen.router)
    client = TestClient(app, client=("127.0.0.1", 50000))

    r = client.put("/history/old1/starred", json={"starred": True})
    assert r.status_code == 200
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT starred FROM generation_history WHERE id='old1'").fetchone()[0] == 1


# ── history list shape ───────────────────────────────────────────────────────


def test_list_history_includes_starred_beyond_recency_window(api):
    client, dbf, outdir, _gen = api
    # 52 takes; the two oldest would fall off the 50-row window. Star one.
    for i in range(52):
        _insert_take(dbf, outdir, f"t{i:03d}", float(i))
    client.put("/history/t000/starred", json={"starred": True})

    rows = client.get("/history").json()
    ids = [r["id"] for r in rows]
    assert len(rows) == 51  # newest 50 + the starred straggler
    assert "t000" in ids  # starred → always visible
    assert "t001" not in ids  # unstarred + aged out
    assert all("starred" in r for r in rows)  # shape: flag present on every row
    # Newest-first ordering preserved.
    assert ids == sorted(ids, key=lambda i: -int(i[1:]))


# ── retention cap ────────────────────────────────────────────────────────────


def test_prune_spares_starred_and_deletes_files(api, monkeypatch):
    _client, dbf, outdir, gen = api
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: 5)
    # 8 takes, the two OLDEST starred → excess 3 must come from the unstarred.
    wavs = {}
    for i in range(8):
        wavs[f"t{i}"] = _insert_take(dbf, outdir, f"t{i}", float(i), starred=1 if i < 2 else 0)

    assert gen._prune_history_over_cap() == 3

    with sqlite3.connect(str(dbf)) as conn:
        kept = {r[0] for r in conn.execute("SELECT id FROM generation_history")}
    assert kept == {"t0", "t1", "t5", "t6", "t7"}  # starred oldest survive
    for tid in ("t2", "t3", "t4"):
        assert not wavs[tid].exists(), f"{tid}'s WAV must be pruned"
    for tid in kept:
        assert wavs[tid].exists(), f"{tid}'s WAV must remain"


def test_prune_keeps_wav_shared_with_surviving_row(api, monkeypatch):
    _client, dbf, outdir, gen = api
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: 2)
    shared = _insert_take(dbf, outdir, "a", 1.0, audio_path="shared.wav")
    _insert_take(dbf, outdir, "b", 2.0, audio_path="shared.wav", with_file=False)
    _insert_take(dbf, outdir, "c", 3.0)

    assert gen._prune_history_over_cap() == 1  # prunes 'a'

    with sqlite3.connect(str(dbf)) as conn:
        kept = {r[0] for r in conn.execute("SELECT id FROM generation_history")}
    assert kept == {"b", "c"}
    assert shared.exists(), "WAV still referenced by 'b' must survive the prune"


def test_cap_zero_disables_pruning(api, monkeypatch):
    _client, dbf, outdir, gen = api
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: 0)
    for i in range(4):
        _insert_take(dbf, outdir, f"t{i}", float(i))
    assert gen._prune_history_over_cap() == 0
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 4


def test_default_cap_is_200(api, monkeypatch):
    _client, _dbf, _outdir, gen = api
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: d)  # pref unset → default
    assert gen._history_cap() == 200
    monkeypatch.setattr("core.prefs.get", lambda k, d=None: "garbage")
    assert gen._history_cap() == 200  # corrupt pref falls back, never crashes


# ── delete-take file safety ──────────────────────────────────────────────────


def test_delete_take_removes_row_and_owned_file(api):
    client, dbf, outdir, _gen = api
    wav = _insert_take(dbf, outdir, "solo", 1.0)

    r = client.delete("/history/solo")
    assert r.status_code == 200 and r.json() == {"deleted": True}
    assert not wav.exists()
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0


def test_delete_take_spares_file_shared_with_another_row(api):
    client, dbf, outdir, _gen = api
    wav = _insert_take(dbf, outdir, "a", 1.0, audio_path="shared.wav")
    _insert_take(dbf, outdir, "b", 2.0, audio_path="shared.wav", with_file=False)

    client.delete("/history/a")
    assert wav.exists(), "file still referenced by 'b' must survive"
    client.delete("/history/b")
    assert not wav.exists(), "last reference gone → file cleaned up"


# ── retention setting endpoint ───────────────────────────────────────────────


@pytest.fixture
def settings_client(tmp_path, monkeypatch):
    import core.prefs as prefs_module

    monkeypatch.setattr(prefs_module, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import api.routers.settings as settings_router

    app = FastAPI()
    app.include_router(settings_router.router)
    return TestClient(app, client=("127.0.0.1", 50000))


def test_history_retention_get_put_roundtrip(settings_client):
    r = settings_client.get("/api/settings/history-retention")
    assert r.status_code == 200
    assert r.json() == {"cap": 200, "default": 200}

    r = settings_client.put("/api/settings/history-retention", json={"cap": 50})
    assert r.status_code == 200
    assert r.json() == {"cap": 50, "default": 200}
    assert settings_client.get("/api/settings/history-retention").json()["cap"] == 50

    # 0 = unlimited is a legal value.
    assert settings_client.put("/api/settings/history-retention", json={"cap": 0}).status_code == 200


def test_history_retention_rejects_negative(settings_client):
    r = settings_client.put("/api/settings/history-retention", json={"cap": -1})
    assert r.status_code == 422


def test_finalization_retains_current_take_when_stars_fill_cap(api, monkeypatch):
    import asyncio
    import time
    import torch
    import threading
    _client, dbf, outdir, gen = api
    monkeypatch.setattr(gen, "_history_cap", lambda: 1)
    _insert_take(dbf, outdir, "favorite", 0, starred=1)
    monkeypatch.setattr("core.analytics.capture", lambda *args: None)
    writing_threads = []
    writer = gen.save_generation_wav
    def record_writer(*args, **kwargs):
        writing_threads.append(threading.get_ident())
        return writer(*args, **kwargs)
    monkeypatch.setattr(gen, "save_generation_wav", record_writer)
    _, meta = asyncio.run(gen._finalize_generation(
        torch.zeros(1, 240), 24000, text="hello", history_mode="design",
        ref_audio_path=None, language="English", instruct=None,
        resolved_profile_id=None, used_seed=42, start_time=time.time(), already_marked=True, include_wav_bytes=True,
    ))
    assert writing_threads and threading.get_ident() not in writing_threads
    assert (outdir / meta["filename"]).is_file()
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 2
    # The next generation can retire this unstarred take; stars remain safe.
    assert gen._prune_history_over_cap(keep_id="next") == 1
    assert not (outdir / meta["filename"]).exists()
    assert meta["_wav_bytes"].startswith(b"RIFF")


@pytest.mark.parametrize("write_fails", [False, True])
def test_cancel_during_save_cleans_up_after_writer_stops(api, monkeypatch, write_fails):
    import asyncio
    import threading
    import time
    import torch
    from pathlib import Path

    _client, dbf, outdir, gen = api
    started, release = threading.Event(), threading.Event()

    def slow_writer(path, *args, **kwargs):
        Path(path).write_bytes(b"partial")
        started.set()
        assert release.wait(5)
        Path(path).write_bytes(b"completed")
        if write_fails:
            raise OSError("write failed")

    monkeypatch.setattr(gen, "save_generation_wav", slow_writer)

    async def run():
        waiting = asyncio.Event()
        original_shield = asyncio.shield
        calls = 0
        def observed_shield(future):
            nonlocal calls
            calls += 1
            if calls == 2:
                waiting.set()
            return original_shield(future)
        monkeypatch.setattr(asyncio, "shield", observed_shield)
        task = asyncio.create_task(gen._finalize_generation(
            torch.zeros(1, 240), 24000, text="hello", history_mode="design",
            ref_audio_path=None, language="English", instruct=None,
            resolved_profile_id=None, used_seed=42, start_time=time.time(),
            already_marked=True,
        ))
        try:
            assert await asyncio.to_thread(started.wait, 3)
            task.cancel()
            await asyncio.wait_for(waiting.wait(), timeout=3)
            assert not task.done(), "cancellation must wait for the active writer"
            task.cancel()
        finally:
            release.set()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert not list(outdir.glob("*.wav"))
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0


@pytest.mark.parametrize("failure", ["write", "read"])
def test_failed_finalization_removes_unpublished_wav(api, monkeypatch, failure):
    import asyncio
    import time
    import torch
    from pathlib import Path

    _client, dbf, outdir, gen = api
    def writer(path, *args, **kwargs):
        Path(path).write_bytes(b"partial")
        if failure == "write":
            raise OSError("write failed")
    def read_fails(path):
        raise OSError("read failed")
    monkeypatch.setattr(gen, "save_generation_wav", writer)
    if failure == "read":
        monkeypatch.setattr(Path, "read_bytes", read_fails)
    with pytest.raises(OSError, match=failure + " failed"):
        asyncio.run(gen._finalize_generation(
            torch.zeros(1, 240), 24000, text="hello", history_mode="design",
            ref_audio_path=None, language="English", instruct=None,
            resolved_profile_id=None, used_seed=42, start_time=time.time(),
            already_marked=True, include_wav_bytes=True,
        ))
    assert not list(outdir.glob("*.wav"))
    with sqlite3.connect(str(dbf)) as conn:
        assert conn.execute("SELECT COUNT(*) FROM generation_history").fetchone()[0] == 0


def test_colliding_take_id_does_not_remove_saved_audio(api, monkeypatch):
    import asyncio
    import time
    import torch
    from pathlib import Path
    _client, dbf, outdir, gen = api
    existing = outdir / "aaaaaaaa.wav"
    existing.write_bytes(b"saved take")
    ids = iter(["aaaaaaaa", "bbbbbbbb"])
    monkeypatch.setattr(gen.uuid, "uuid4", lambda: next(ids))
    def fail_write(path, *args, **kwargs):
        Path(path).write_bytes(b"partial")
        raise OSError("write failed")
    monkeypatch.setattr(gen, "save_generation_wav", fail_write)
    with pytest.raises(OSError, match="write failed"):
        asyncio.run(gen._finalize_generation(
            torch.zeros(1, 240), 24000, text="hello", history_mode="design",
            ref_audio_path=None, language="English", instruct=None,
            resolved_profile_id=None, used_seed=42, start_time=time.time(), already_marked=True))
    assert existing.read_bytes() == b"saved take"
    assert not (outdir / "bbbbbbbb.wav").exists()


# -- list payload stays bounded (#4483) --------------------------------------


def test_history_text_limit_clips_long_scripts_and_item_route_returns_full(api):
    client, dbf, outdir, _gen = api
    _insert_take(dbf, outdir, "long", 2.0)
    _insert_take(dbf, outdir, "short", 1.0)
    book = "word " * 20_000
    with sqlite3.connect(str(dbf)) as conn:
        conn.execute("UPDATE generation_history SET text=? WHERE id='long'", (book,))
        conn.commit()

    full = {r["id"]: r for r in client.get("/history").json()}
    assert full["long"]["text"] == book and "text_truncated" not in full["long"]

    rows = {r["id"]: r for r in client.get("/history?text_limit=500").json()}
    assert len(rows["long"]["text"]) == 500
    assert rows["long"]["text_truncated"] is True
    assert rows["long"]["transcript"] == ""
    assert rows["short"]["text"] == "text short"
    assert "text_truncated" not in rows["short"]

    one = client.get("/history/long").json()
    assert one["text"] == book and "text_truncated" not in one
    assert client.get("/history/nope").status_code == 404
