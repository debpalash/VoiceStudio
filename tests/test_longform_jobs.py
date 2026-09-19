"""PR 7 — Longform Job Library. Tests the recovery logic in
``api.routers.longform_jobs.build_longform_library`` against a seeded job_store.

We call the pure builder (and the route handler) directly — no ``main``/torch
import — seeding the real job_store over its temp DB.
"""
import json
import os
import time

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from core import job_store
from core.db import init_db

from api.routers.longform_jobs import (
    build_longform_library,
    _done_payload_from_events,
    longform_jobs,
)


@pytest.fixture(autouse=True)
def _init_db_once():
    init_db()
    yield


def _uid(prefix: str) -> str:
    return f"{prefix}_{int(time.time()*1e6)}_{os.getpid()}"


def _seed_done(job_id: str, *, type: str, done_payload: dict | None,
               meta: dict | None = None, extra_events: list[dict] | None = None):
    """Create a job, append some progress events + a final done event, mark done."""
    job_store.create(job_id, type=type, meta=meta)
    job_store.mark_running(job_id)
    for ev in (extra_events or []):
        job_store.append_event(job_id, json.dumps(ev))
    if done_payload is not None:
        job_store.append_event(job_id, json.dumps(done_payload))
    job_store.mark_done(job_id)


# ── _done_payload_from_events ────────────────────────────────────────────────


def test_done_payload_recovers_last_done():
    events = [
        {"payload": json.dumps({"type": "started", "chapters": 2})},
        {"payload": json.dumps({"type": "chapter", "index": 0})},
        {"payload": json.dumps({"type": "done", "output": "story_x.m4b",
                                 "chapters": 2, "duration_s": 12.5})},
    ]
    done = _done_payload_from_events(events)
    assert done is not None
    assert done["output"] == "story_x.m4b"


def test_done_payload_skips_malformed_json():
    events = [
        {"payload": "not json at all"},
        {"payload": json.dumps({"type": "done", "output": "ok.m4b"})},
    ]
    assert _done_payload_from_events(events)["output"] == "ok.m4b"


def test_done_payload_none_when_absent():
    events = [{"payload": json.dumps({"type": "chapter"})}]
    assert _done_payload_from_events(events) is None


# ── build_longform_library ───────────────────────────────────────────────────


def test_library_lists_only_finished_longform():
    ab = _uid("ab")
    st = _uid("st")
    failed = _uid("fail")
    dub = _uid("dub")

    _seed_done(ab, type="audiobook", meta={"title": "My Book"},
               done_payload={"type": "done", "output": f"{ab}.m4b",
                             "chapters": 3, "duration_s": 100.0})
    _seed_done(st, type="story",
               done_payload={"type": "done", "output": f"{st}.m4b",
                             "chapters": 1, "duration_s": 42.0, "title": "A Tale"})
    # A failed audiobook job — has progress events but never a done event.
    job_store.create(failed, type="audiobook")
    job_store.mark_running(failed)
    job_store.append_event(failed, json.dumps({"type": "chapter", "index": 0}))
    job_store.mark_failed(failed, "boom")
    # A finished dub job — done, but not a longform type → excluded.
    _seed_done(dub, type="dub_generate",
               done_payload={"type": "done", "output": f"{dub}.wav"})

    lib = build_longform_library(job_store.list_jobs, job_store.events_since, limit=50)
    by_id = {it["job_id"]: it for it in lib}

    assert ab in by_id
    assert st in by_id
    assert failed not in by_id  # never finished
    assert dub not in by_id     # wrong type

    assert by_id[ab]["type"] == "audiobook"
    assert by_id[ab]["output"] == f"{ab}.m4b"
    assert by_id[ab]["chapters"] == 3
    assert by_id[ab]["duration_s"] == 100.0
    assert by_id[ab]["title"] == "My Book"          # from job meta
    assert by_id[ab]["created_at"] is not None

    assert by_id[st]["title"] == "A Tale"           # from done event
    assert by_id[st]["chapters"] == 1


def test_library_skips_done_job_without_output():
    bad = _uid("noout")
    _seed_done(bad, type="story",
               done_payload={"type": "done", "chapters": 2})  # no "output"
    lib = build_longform_library(job_store.list_jobs, job_store.events_since, limit=50)
    assert bad not in {it["job_id"] for it in lib}


def test_library_newest_first():
    older = _uid("old")
    newer = _uid("new")
    _seed_done(older, type="audiobook",
               done_payload={"type": "done", "output": f"{older}.m4b", "chapters": 1})
    time.sleep(0.01)
    _seed_done(newer, type="audiobook",
               done_payload={"type": "done", "output": f"{newer}.m4b", "chapters": 1})

    lib = build_longform_library(job_store.list_jobs, job_store.events_since, limit=50)
    ids = [it["job_id"] for it in lib if it["job_id"] in (older, newer)]
    assert ids.index(newer) < ids.index(older)


def test_library_respects_limit():
    seeded = []
    for _ in range(5):
        jid = _uid("lim")
        _seed_done(jid, type="story",
                   done_payload={"type": "done", "output": f"{jid}.m4b", "chapters": 1})
        seeded.append(jid)
    lib = build_longform_library(job_store.list_jobs, job_store.events_since, limit=2)
    assert len(lib) == 2


def test_library_never_raises_on_bad_callables():
    def boom(*a, **k):
        raise RuntimeError("db down")
    # list_jobs failing → empty list, no exception.
    assert build_longform_library(boom, job_store.events_since, limit=10) == []
    # events_since failing for a row → that row skipped, no exception.
    jid = _uid("ev")
    _seed_done(jid, type="audiobook",
               done_payload={"type": "done", "output": f"{jid}.m4b", "chapters": 1})

    def evboom(*a, **k):
        raise RuntimeError("events down")
    lib = build_longform_library(job_store.list_jobs, evboom, limit=10)
    assert jid not in {it["job_id"] for it in lib}


# ── route handler ────────────────────────────────────────────────────────────


def test_route_handler_returns_jobs_envelope():
    jid = _uid("route")
    _seed_done(jid, type="audiobook",
               done_payload={"type": "done", "output": f"{jid}.m4b",
                             "chapters": 2, "duration_s": 9.0})
    resp = longform_jobs(limit=50)
    assert "jobs" in resp
    assert jid in {it["job_id"] for it in resp["jobs"]}


def test_route_survives_leaked_module_world_purge(monkeypatch):
    """Full-suite flake regression: some suites purge and re-import the whole
    ``core``/``services`` module tree under a temporary OMNIVOICE_DATA_DIR
    without restoring the previous modules. The route used to run
    ``from core import job_store`` at CALL time, re-resolving through
    ``sys.modules`` into that leaked stale world — whose DB_PATH was a
    different SQLite file — so the library came back empty despite seeded
    jobs (the order-dependent ``test_route_handler_returns_jobs_envelope``
    failure). The route must keep using the world it was imported into."""
    import sys
    import types

    jid = _uid("purge")
    _seed_done(jid, type="audiobook",
               done_payload={"type": "done", "output": f"{jid}.m4b",
                             "chapters": 1, "duration_s": 3.0})

    # Simulate the leaked world: sys.modules and the ``core`` package now hold
    # a foreign core.job_store whose store is empty (exactly what a call-time
    # import would resolve after a purge-and-reimport under another data dir).
    stale = types.ModuleType("core.job_store")
    stale.list_jobs = lambda **kw: []
    stale.events_since = lambda *a, **k: []
    monkeypatch.setitem(sys.modules, "core.job_store", stale)
    import core as core_pkg
    monkeypatch.setattr(core_pkg, "job_store", stale, raising=False)

    resp = longform_jobs(limit=50)
    assert jid in {it["job_id"] for it in resp["jobs"]}, (
        "route resolved job_store through the leaked module world instead of "
        "its import-time binding"
    )


# ── Render summary: a finished render says what it is ────────────────────────


def test_library_carries_the_title_and_summary_the_done_event_recorded():
    jid = _uid("story_sum")
    summary = {"engine": "gpt-sovits", "voices": [{"id": "v1", "name": "Jake"}],
               "speeds": [0.95], "lines": 12, "words": 1564, "options": {"seed": 7}}
    _seed_done(jid, type="story", done_payload={
        "type": "done", "output": "story_sum.mp3", "chapters": 9, "duration_s": 3460.0,
        "title": "The Super Sloth", "summary": summary})
    item = next(j for j in build_longform_library(job_store.list_jobs, job_store.events_since, limit=500)
                if j["job_id"] == jid)
    assert item["title"] == "The Super Sloth"
    assert item["summary"] == {**summary, "language": "", "format": "", "chapter_titles": []}


def test_library_tolerates_renders_without_or_with_a_malformed_summary():
    old, bad = _uid("story_old"), _uid("story_bad")
    _seed_done(old, type="story", done_payload={"type": "done", "output": "old.mp3"})
    _seed_done(bad, type="story", done_payload={"type": "done", "output": "bad.mp3", "summary": "nope"})
    jobs = {j["job_id"]: j for j in build_longform_library(job_store.list_jobs, job_store.events_since, limit=500)}
    assert "summary" not in jobs[old] and "summary" not in jobs[bad]


def test_render_summary_is_settings_and_counts_never_script_text():
    from types import SimpleNamespace as NS

    from services.longform_render import render_summary

    chapters = [
        NS(title="Chapter One", spans=[NS(text="Zoe raced along the path.", speed=0.95),
                                       NS(text="", speed=None)]),          # pause-only span
        NS(title="Chapter Two", spans=[NS(text="Morning came early.", speed=None)]),
    ]
    out = render_summary(chapters, voices=[{"id": "v1", "name": "Jake"}], engine_id="gpt-sovits",
                         language="English", fmt="mp3",
                         options={"seed": 7, "num_step": None, "vary_repeats": False, "line_gap_ms": 250})
    assert out["voices"] == [{"id": "v1", "name": "Jake"}] and out["engine"] == "gpt-sovits"
    assert out["lines"] == 2 and out["words"] == 8
    assert out["speeds"] == [0.95, 1.0]                     # unset speed = engine default
    # The caller pre-filters to non-default options; explicit falsy values stay.
    assert out["options"] == {"seed": 7, "vary_repeats": False, "line_gap_ms": 250}
    assert out["chapter_titles"] == ["Chapter One", "Chapter Two"]
    assert "Zoe" not in json.dumps(out)                        # content-free


def test_a_summary_with_malformed_nested_fields_degrades_instead_of_reaching_clients():
    jid = _uid("story_nested")
    _seed_done(jid, type="story", done_payload={
        "type": "done", "output": "nested.mp3",
        "summary": {"engine": 7, "voices": "v1", "speeds": ["fast", 0.95, True], "lines": "12",
                    "words": None, "options": {"seed": 0, "bad": {"x": 1}}, "chapter_titles": [1, "One"]}})
    item = next(j for j in build_longform_library(job_store.list_jobs, job_store.events_since, limit=500)
                if j["job_id"] == jid)
    assert item["summary"] == {
        "engine": "7", "voices": [], "language": "", "format": "", "lines": 12, "words": 0,
        "speeds": [0.95], "options": {"seed": 0}, "chapter_titles": ["One"]}
