"""Voice Design recipe stored with a take: migration 0013, validator, endpoints."""
import asyncio
import importlib
import json
import os
import sqlite3

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import core.db as _db

_PRIOR = "0012_call_sessions"


def _root() -> str:
    """Nearest ancestor of this file that holds ``alembic.ini``."""
    here = os.path.dirname(os.path.abspath(__file__))
    while True:
        if os.path.isfile(os.path.join(here, "alembic.ini")):
            return here
        parent = os.path.dirname(here)
        if parent == here:
            return here
        here = parent


def _cfg(db_path):
    from alembic.config import Config

    cfg = Config(os.path.join(_root(), "alembic.ini"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")
    return cfg


def _columns(path):
    with sqlite3.connect(str(path)) as conn:
        return {info[1] for info in conn.execute("PRAGMA table_info(generation_history)")}


def _old_schema_db(path, *, with_column=False):
    """A database at revision ``_PRIOR``, optionally already carrying the column."""
    from alembic import command

    ddl = _db._BASE_SCHEMA
    if not with_column:
        ddl = ddl.replace("design_recipe TEXT DEFAULT NULL,", "")
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(ddl)
        conn.execute(
            "INSERT INTO generation_history (id, text, mode, instruct, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            ("row1", "hello", "design", "female", 1_700_000_000.0),
        )
        conn.commit()
    command.stamp(_cfg(path), _PRIOR)


# -- migration -------------------------------------------------------------------

def test_upgrade_adds_nullable_column_and_downgrade_removes_it(tmp_path):
    from alembic import command

    path = tmp_path / "old.db"
    _old_schema_db(path)
    assert "design_recipe" not in _columns(path)
    command.upgrade(_cfg(path), "head")
    assert "design_recipe" in _columns(path)
    with sqlite3.connect(str(path)) as conn:
        row = conn.execute(
            "SELECT text, mode, instruct, design_recipe FROM generation_history WHERE id='row1'"
        ).fetchone()
    assert row == ("hello", "design", "female", None)
    command.downgrade(_cfg(path), _PRIOR)
    assert "design_recipe" not in _columns(path)


def test_migrated_and_fresh_schemas_have_the_same_columns(tmp_path):
    from alembic import command

    migrated, fresh = tmp_path / "m.db", tmp_path / "f.db"
    _old_schema_db(migrated)
    _old_schema_db(fresh, with_column=True)  # fresh install already has the column
    command.upgrade(_cfg(migrated), "head")
    command.upgrade(_cfg(fresh), "head")
    assert _columns(migrated) == _columns(fresh)
    with sqlite3.connect(":memory:") as canon:
        canon.executescript(_db._BASE_SCHEMA)
        base = {r[1] for r in canon.execute("PRAGMA table_info(generation_history)")}
    assert _columns(migrated) == base


def test_ensure_schema_heals_a_db_alembic_never_touched(tmp_path, monkeypatch):
    path = tmp_path / "bypass.db"
    with sqlite3.connect(str(path)) as conn:
        conn.executescript(_db._BASE_SCHEMA.replace("design_recipe TEXT DEFAULT NULL,", ""))
    assert "design_recipe" not in _columns(path)

    def _connect():
        conn = sqlite3.connect(str(path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(_db, "get_db", _connect)
    _db.ensure_schema()
    assert "design_recipe" in _columns(path)


# -- validator ---------------------------------------------------------------------

def _gen():
    return importlib.import_module("api.routers.generation")


def test_validator_re_serialises_with_server_derived_mapping():
    from core.describe_voice import parse_description

    gen = _gen()
    desc = "an old British woman"
    raw = json.dumps({
        "description": desc,
        "picks": {"Pitch": "High Pitch"},
        "mapped": {"Gender": "Male"},   # client claim: ignored
        "extra": "dropped",
    })
    stored = json.loads(gen._design_recipe_json(raw))
    assert set(stored) == {"description", "picks", "mapped"}
    assert stored["picks"] == {"Pitch": "High Pitch"}
    assert stored["mapped"] == parse_description(desc)["attrs"]


def test_validator_keeps_non_ascii_text_readable():
    out = _gen()._design_recipe_json(json.dumps({"description": "声音 low", "picks": {}}))
    assert "声音" in out


@pytest.mark.parametrize("raw", [
    None,
    "",
    "not json",
    "[1, 2]",
    '"text"',
    json.dumps({"picks": {}}),
    json.dumps({"description": 5, "picks": {}}),
    json.dumps({"description": "x" * 2001, "picks": {}}),
    json.dumps({"description": "x"}),
    json.dumps({"description": "x", "picks": []}),
    json.dumps({"description": "x", "picks": {"Nonsense": "v"}}),
    json.dumps({"description": "x", "picks": {"Gender": 3}}),
    json.dumps({"description": "x", "picks": {"Gender": "g" * 65}}),
    json.dumps({"description": "x", "picks": {}}) + " " * 8100,
])
def test_validator_rejects_hostile_input(raw):
    assert _gen()._design_recipe_json(raw) is None


# -- endpoints ---------------------------------------------------------------------

@pytest.fixture
def api(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient

    from main import app

    gen = _gen()
    db_path = tmp_path / "t.db"

    def _connect():
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        return conn

    monkeypatch.setattr(_db, "get_db", _connect)
    _db.ensure_schema()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr(gen, "OUTPUTS_DIR", str(outputs))
    tts = importlib.import_module("services.tts_backend")

    class _Engine(tts.TTSBackend):
        id = "recipe-test-engine"
        display_name = "Recipe test engine"
        gpu_compat = ("cpu",)

        @property
        def sample_rate(self):
            return 24000

        @property
        def supported_languages(self):
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw):
            return torch.zeros(1, 24000)

    monkeypatch.setitem(tts._REGISTRY, _Engine.id, _Engine)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", _Engine.id)
    return TestClient(app, client=("127.0.0.1", 50000))


def _history_recipe(client):
    items = client.get("/history").json()
    assert len(items) == 1
    return items[0]["design_recipe"]


def test_generate_stores_validated_recipe_and_history_returns_it(api):
    raw = json.dumps({"description": "calm older man", "picks": {"Age": "Elderly"}})
    res = api.post("/generate", data={"text": "Hi", "design_recipe": raw})
    assert res.status_code == 200, res.text
    assert _history_recipe(api) == _gen()._design_recipe_json(raw)


@pytest.mark.parametrize("raw", ["{broken", None])
def test_bad_or_missing_recipe_still_yields_a_take(api, raw):
    data = {"text": "Hi"}
    if raw is not None:
        data["design_recipe"] = raw
    assert api.post("/generate", data=data).status_code == 200
    assert _history_recipe(api) is None


def test_clone_takes_never_keep_a_recipe(api, tmp_path):
    gen = _gen()
    recipe = gen._design_recipe_json(json.dumps({"description": "x", "picks": {}}))
    asyncio.run(gen._finalize_generation(
        torch.zeros(1, 2400), 24000, text="t", history_mode="clone", ref_audio_path=None,
        language="Auto", instruct="", resolved_profile_id=None, used_seed=1,
        start_time=0.0, design_recipe=recipe,
    ))
    with _db.db_conn() as conn:
        assert conn.execute("SELECT design_recipe FROM generation_history").fetchone()[0] is None
