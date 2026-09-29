"""#2389 — a Voice Design take keeps the draft that produced it.

The history row only held the combined instruct, so reopening a take could not
tell the written description from the details the user picked or the ones the
description mapped to. ``generation_history.design_recipe`` stores that draft.

Four layers:
  * Migration 0013 adds the column to an existing DB without touching rows, is
    idempotent on a fresh install, downgrades cleanly and converges with
    ``_BASE_SCHEMA``; a DB where alembic cannot run heals via ``ensure_schema``.
  * ``_design_recipe_json`` treats the form field as hostile: only a
    well-formed, bounded recipe with known categories is stored, re-serialized,
    and the mapped details are always derived from the description itself.
  * ``POST /generate`` stores the recipe with the take and ``GET /history``
    returns it; a malformed recipe never fails the take.
"""
import importlib
import json
import os
import sqlite3
import uuid

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

_PREVIOUS = "0012_call_sessions"
_SENT = {"description": "raspy old woman, scottish accent", "picks": {"Pitch": "low pitch"}}


def _stored(sent=_SENT):
    parse = importlib.import_module("core.describe_voice").parse_description
    return {**sent, "mapped": parse(sent["description"])["attrs"]}

# generation_history as every DB before this change has it.
_PRE_RECIPE_HISTORY = """
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
        starred INTEGER DEFAULT 0,
        created_at REAL
    );
"""


def _alembic(command_name, db_path, target):
    from alembic import command
    from alembic.config import Config

    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    cfg = Config(os.path.join(root, "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    getattr(command, command_name)(cfg, target)


def _cols(db_path):
    with sqlite3.connect(str(db_path)) as conn:
        return {r[1] for r in conn.execute("PRAGMA table_info(generation_history)")}


def _seed_pre_recipe_db(path):
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_PRE_RECIPE_HISTORY)
        conn.execute(
            "INSERT INTO generation_history (id, text, mode, instruct, audio_path, created_at) "
            "VALUES ('old1', 'hello', 'design', 'female', 'old1.wav', 1.0)"
        )
        conn.commit()
    _alembic("stamp", path, _PREVIOUS)


# ── migration 0013 ────────────────────────────────────────────────────────────


def test_migration_adds_design_recipe_and_keeps_rows(tmp_path):
    dbf = tmp_path / "user.db"
    _seed_pre_recipe_db(dbf)

    _alembic("upgrade", dbf, "head")

    assert "design_recipe" in _cols(dbf)
    with sqlite3.connect(str(dbf)) as conn:
        row = conn.execute(
            "SELECT text, instruct, design_recipe FROM generation_history WHERE id='old1'"
        ).fetchone()
    assert row == ("hello", "female", None)


def test_migration_is_idempotent_on_fresh_install(tmp_path):
    base_schema = importlib.import_module("core.db")._BASE_SCHEMA
    dbf = tmp_path / "fresh.db"
    with sqlite3.connect(str(dbf)) as conn:
        conn.executescript(base_schema)
    _alembic("stamp", dbf, _PREVIOUS)

    _alembic("upgrade", dbf, "head")  # must not raise on the existing column

    assert "design_recipe" in _cols(dbf)


def test_migration_downgrade_drops_column(tmp_path):
    dbf = tmp_path / "user.db"
    _seed_pre_recipe_db(dbf)
    _alembic("upgrade", dbf, "head")

    _alembic("downgrade", dbf, _PREVIOUS)

    assert "design_recipe" not in _cols(dbf)


def test_migration_and_base_schema_converge(tmp_path):
    base_schema = importlib.import_module("core.db")._BASE_SCHEMA
    migrated = tmp_path / "migrated.db"
    _seed_pre_recipe_db(migrated)
    _alembic("upgrade", migrated, "head")
    fresh = tmp_path / "fresh.db"
    with sqlite3.connect(str(fresh)) as conn:
        conn.executescript(base_schema)

    assert _cols(migrated) == _cols(fresh)


def test_db_without_alembic_self_heals_design_recipe(tmp_path, monkeypatch):
    db = importlib.import_module("core.db")
    dbf = tmp_path / "user.db"
    _seed_pre_recipe_db(dbf)

    def connect():
        conn = sqlite3.connect(str(dbf))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(db, "get_db", connect)
    db.ensure_schema()

    assert "design_recipe" in _cols(dbf)


# ── form field validation ─────────────────────────────────────────────────────


def _recipe_json():
    return importlib.import_module("api.routers.generation")._design_recipe_json


def test_valid_recipe_is_stored_re_serialized():
    raw = json.dumps({**_SENT, "extra": "ignored"})
    stored = json.loads(_recipe_json()(raw))
    assert stored == _stored()
    assert stored["mapped"]["Gender"] == "female" and stored["mapped"]["Age"] == "elderly"


def test_mapped_details_come_from_the_description_not_the_client():
    """A take rendered before the page's mapping landed still records details
    that match the description it was rendered from."""
    stale = {**_SENT, "mapped": {"Gender": "male", "Age": "child"}}
    assert json.loads(_recipe_json()(json.dumps(stale))) == _stored()


@pytest.mark.parametrize(
    "raw",
    [
        None,
        "",
        "not json",
        "[]",
        json.dumps({**_SENT, "description": 7}),
        json.dumps({**_SENT, "description": "x" * 2001}),
        json.dumps({"picks": {}}),
        json.dumps({**_SENT, "picks": ["low pitch"]}),
        json.dumps({**_SENT, "picks": {"Timbre": "warm"}}),
        json.dumps({**_SENT, "picks": {"Gender": 1}}),
        json.dumps({**_SENT, "picks": {"Gender": "x" * 65}}),
        "{" + " " * 9000 + "}",
    ],
)
def test_malformed_recipe_is_dropped(raw):
    assert _recipe_json()(raw) is None


# ── POST /generate → GET /history ─────────────────────────────────────────────


def _fake_engine():
    tts = importlib.import_module("services.tts_backend")

    class _FakeEngine(tts.TTSBackend):
        id = "fake-design-recipe-engine"
        display_name = "Fake Design Recipe Engine (test)"
        applies_own_mastering = False
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            return torch.zeros(1, 24000)

    return tts, _FakeEngine


@pytest.fixture()
def client(monkeypatch, tmp_path):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    db = importlib.import_module("core.db")
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "recipes.db"))
    db.init_db()
    generation = importlib.import_module("api.routers.generation")
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(generation, "OUTPUTS_DIR", str(outputs))
    tts, engine = _fake_engine()
    monkeypatch.setitem(tts._REGISTRY, engine.id, engine)
    app = FastAPI()
    app.include_router(generation.router)
    with TestClient(app, client=("127.0.0.1", 50000)) as http:
        yield http, engine


def _take(client, engine, **extra):
    data = {"text": f"design recipe {uuid.uuid4().hex}", "engine": engine.id}
    data.update(extra)
    res = client.post("/generate", data=data)
    assert res.status_code == 200, res.text
    take_id = res.headers["X-Audio-Id"]
    with importlib.import_module("core.db").db_conn() as conn:
        stored = conn.execute(
            "SELECT design_recipe FROM generation_history WHERE id=?", (take_id,)
        ).fetchone()["design_recipe"]
        conn.execute("DELETE FROM generation_history WHERE id=?", (take_id,))
    return take_id, stored


def test_generate_stores_the_recipe_with_the_take(client):
    http, engine = client
    raw = json.dumps(_SENT)
    res = http.post(
        "/generate",
        data={"text": "stored recipe", "engine": engine.id, "instruct": "raspy", "design_recipe": raw},
    )
    assert res.status_code == 200, res.text
    take_id = res.headers["X-Audio-Id"]
    try:
        listed = {item["id"]: item for item in http.get("/history").json()}
        assert json.loads(listed[take_id]["design_recipe"]) == _stored()
    finally:
        with importlib.import_module("core.db").db_conn() as conn:
            conn.execute("DELETE FROM generation_history WHERE id=?", (take_id,))


def test_malformed_recipe_never_fails_the_take(client):
    http, engine = client
    _, stored = _take(http, engine, instruct="raspy", design_recipe="{broken")
    assert stored is None
    _, absent = _take(http, engine, instruct="raspy")
    assert absent is None


def test_clone_take_discards_a_design_recipe(client, monkeypatch):
    import asyncio
    import time
    generation = importlib.import_module("api.routers.generation")
    asyncio.run(generation._finalize_generation(
        torch.zeros(1, 24000), 24000, text="clone", history_mode="clone",
        ref_audio_path="reference.wav", language="en", instruct="",
        resolved_profile_id=None, used_seed=None, start_time=time.time(),
        already_marked=True, design_recipe=json.dumps(_SENT),
    ))
    with importlib.import_module("core.db").db_conn() as conn:
        row = conn.execute("SELECT mode, design_recipe FROM generation_history").fetchone()
    assert row["mode"] == "clone"
    assert row["design_recipe"] is None
