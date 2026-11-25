"""Pure tests for the durable-resume manifest (services.longform_resume).

Uses a monkeypatch fixture to point OUTPUTS_DIR at a tmp dir — deliberately NOT
a module-level ``sys.modules["core.config"]`` stub, which would pollute the rest
of the shared ``pytest tests/`` session (e.g. test_longform_e2e)."""
from __future__ import annotations

import os

import pytest

from services import longform_resume as lr

_PLAN = [
    {"title": "One", "spans": [
        {"voice_id": "v1", "text": "hello", "pause_ms_after": 0, "speed": None}]},
    {"title": "Two", "spans": [
        {"voice_id": "v1", "text": "world", "pause_ms_after": 500, "speed": 0.9}]},
]
_PARAMS = {"default_voice": "v1", "fmt": "m4b", "bitrate": "128k",
           "loudness": "acx", "cover_path": None, "metadata": {"title": "Bk"}, "lexicon": None}


@pytest.fixture(autouse=True)
def _tmp_outputs(tmp_path, monkeypatch):
    import core.config as cfg
    monkeypatch.setattr(cfg, "OUTPUTS_DIR", str(tmp_path), raising=False)
    return tmp_path


def test_build_manifest_shape():
    m = lr.build_manifest(job_id="abc", job_type="audiobook", plan_chapters=_PLAN,
                          params=_PARAMS, title="Bk")
    assert m["version"] == lr.MANIFEST_VERSION
    assert m["job_id"] == "abc" and m["job_type"] == "audiobook"
    assert m["total_chapters"] == 2 and m["title"] == "Bk"
    assert m["plan"] == _PLAN and m["params"] == _PARAMS


def test_write_read_roundtrip():
    m = lr.build_manifest(job_id="rt", job_type="story", plan_chapters=_PLAN,
                          params=_PARAMS, title="S")
    path = lr.write_manifest(m)
    assert path and os.path.isfile(path)
    assert lr.has_manifest("story", "rt") is True
    assert lr.read_manifest("story", "rt") == m


def test_clear_manifest():
    lr.write_manifest(lr.build_manifest(job_id="cl", job_type="audiobook",
                                        plan_chapters=_PLAN, params=_PARAMS))
    assert lr.has_manifest("audiobook", "cl")
    lr.clear_manifest("audiobook", "cl")
    assert not lr.has_manifest("audiobook", "cl")
    lr.clear_manifest("audiobook", "cl")  # idempotent, no raise


def test_read_missing_returns_none():
    assert lr.read_manifest("audiobook", "nope") is None
    assert lr.has_manifest("audiobook", "nope") is False


def test_read_rejects_wrong_version():
    m = lr.build_manifest(job_id="ver", job_type="audiobook", plan_chapters=_PLAN, params=_PARAMS)
    m["version"] = 999
    lr.write_manifest(m)
    assert lr.read_manifest("audiobook", "ver") is None  # foreign schema → not resumed


def test_read_rejects_corrupt_json():
    path = lr.manifest_path("audiobook", "corrupt")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("{ not json")
    assert lr.read_manifest("audiobook", "corrupt") is None


def test_atomic_write_leaves_no_tmp():
    lr.write_manifest(lr.build_manifest(job_id="atom", job_type="story",
                                        plan_chapters=_PLAN, params=_PARAMS))
    assert not any(n.endswith(".tmp") for n in os.listdir(lr.work_dir("story", "atom")))


def test_failed_manifest_update_keeps_previous_plan_and_removes_staging_file(monkeypatch):
    manifest = lr.build_manifest(job_id="retry", job_type="story", plan_chapters=_PLAN,
                                 params=_PARAMS, title="Original")
    assert lr.write_manifest(manifest)

    def reject_replace(source, target):
        raise PermissionError("manifest is temporarily locked")

    with monkeypatch.context() as locked:
        locked.setattr(lr.os, "replace", reject_replace)
        assert lr.write_manifest({**manifest, "title": "Updated"}) is None
    assert lr.read_manifest("story", "retry") == manifest
    assert os.listdir(lr.work_dir("story", "retry")) == ["resume.json"]

    updated = {**manifest, "title": "Updated"}
    assert lr.write_manifest(updated)
    assert lr.read_manifest("story", "retry") == updated
