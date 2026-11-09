"""Tests for the community gallery (marketplace) loader.

Covers strict item validation, manifest/cache boundaries, same-origin preview,
and idempotent profile materialization without a model or network dependency.
"""
from __future__ import annotations

import io
import json
import os
from pathlib import Path
import wave

import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before this module imports the REAL core.config (the old
# sys.modules stub leaked at collection time and broke mixed runs).
from fastapi import FastAPI, HTTPException, Response  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from api.routers import community  # noqa: E402


def _wav_bytes() -> bytes:
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(24_000)
        wav.writeframes(b"\x00\x01" * 64)
    return buf.getvalue()


def _write_wav(path: Path) -> bytes:
    data = _wav_bytes()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return data

_FIXTURE = {
    "schema_version": 1,
    "items": [
        {"id": "p1", "type": "preset", "name": "Test Narrator", "use_case": "narration",
         "facets": {"gender": "female", "age": "middle-aged", "pitch": "low pitch", "lang": "English"},
         "instruct": "female, middle-aged, low pitch", "language": "English", "source": "community"},
        # invalid instruct token -> dropped
        {"id": "p_bad", "type": "preset", "name": "Bad", "use_case": "narration",
         "instruct": "female, raspy, smoky"},
        # unsafe audio host -> dropped
        {"id": "v_bad", "type": "voice", "name": "Sketchy", "use_case": "narration",
         "audio": {"url": "http://evil.example.com/x.wav"}},
        # valid voice (allow-listed host)
        {"id": "v1", "type": "voice", "name": "Recorded One", "use_case": "narration",
         "facets": {"gender": "male", "lang": "English"},
         "audio": {"url": "https://github.com/debpalash/omnivoice-gallery/releases/download/voices-v1/v1.wav"}},
        # unknown use_case -> dropped
        {"id": "u1", "type": "preset", "name": "Mystery", "use_case": "banana", "instruct": "male"},
    ],
    "packs": [{"id": "starter", "name": "Starter", "item_ids": ["p1"]}],
}


@pytest.fixture(scope="module", autouse=True)
def seed_cache():
    cache = community._cache_path("debpalash/omnivoice-gallery")
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(json.dumps(_FIXTURE), encoding="utf-8")
    yield


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(community.router)
    return TestClient(app)


# ── pure validation ───────────────────────────────────────────────────────────
def test_valid_preset_kept():
    assert community.validate_item(_FIXTURE["items"][0]) is not None


def test_invalid_instruct_dropped():
    assert community.validate_item(_FIXTURE["items"][1]) is None


def test_unsafe_audio_url_dropped():
    assert community.validate_item(_FIXTURE["items"][2]) is None


def test_unknown_use_case_dropped():
    assert community.validate_item(_FIXTURE["items"][4]) is None


def test_malformed_manifest_entries_do_not_break_other_sources():
    valid = _FIXTURE["items"][0]
    items, packs = community._merge([
        ("bad/repo", {"items": 42, "packs": "not-a-list"}),
        ("good/repo", {"items": [None, "not-an-item", valid], "packs": [None]}),
    ])

    assert [item["id"] for item in items] == [valid["id"]]
    assert packs == []


def test_is_valid_instruct():
    assert community.is_valid_instruct("male, elderly, very low pitch")
    assert not community.is_valid_instruct("male, sultry")
    assert not community.is_valid_instruct("male, female")
    assert not community.is_valid_instruct("british accent, 四川话")
    assert not community.is_valid_instruct("")


def test_preset_attrs_are_normalized_and_complete():
    item = community.validate_item(_FIXTURE["items"][0])
    assert item["instruct"] == "female, middle-aged, low pitch"
    assert item["attrs"] == {
        "Gender": "female", "Age": "middle-aged", "Pitch": "low pitch",
        "Style": "Auto", "EnglishAccent": "Auto", "ChineseDialect": "Auto",
    }
    assert item["preview_url"] == "/community/items/p1/preview"


def test_remote_transcript_fields_are_bounded():
    preset = community.validate_item({
        **_FIXTURE["items"][0],
        "sample_script": " x " * (community._MAX_SAMPLE_SCRIPT_CHARS + 10),
    })
    voice = community.validate_item({
        **_FIXTURE["items"][3],
        "audio": {
            **_FIXTURE["items"][3]["audio"],
            "ref_text": " y " * (community._MAX_REF_TEXT_CHARS + 10),
        },
    })
    assert len(preset["sample_script"]) == community._MAX_SAMPLE_SCRIPT_CHARS
    assert len(voice["audio"]["ref_text"]) == community._MAX_REF_TEXT_CHARS


# ── merge keeps only valid items ──────────────────────────────────────────────
def test_merge_drops_invalid_and_dedups():
    items, packs = community._merge([("debpalash/omnivoice-gallery", _FIXTURE)])
    ids = {i["id"] for i in items}
    assert ids == {"p1", "v1"}
    assert packs and packs[0]["id"] == "starter"


# ── endpoints (served from cache, no network) ─────────────────────────────────
def test_manifest_endpoint_from_cache(client):
    body = client.get("/community/manifest").json()
    assert body["count"] == 2
    assert {i["id"] for i in body["items"]} == {"p1", "v1"}
    assert "debpalash/omnivoice-gallery" in body["sources"]


def test_items_filter_by_type(client):
    body = client.get("/community/items", params={"type": "voice"}).json()
    assert [i["id"] for i in body["items"]] == ["v1"]


def test_items_filter_by_use_case(client):
    body = client.get("/community/items", params={"use_case": "narration"}).json()
    assert body["total"] == 2


def test_items_filter_by_pack_and_report_facets(client):
    body = client.get("/community/items", params={"pack": "starter"}).json()
    assert [i["id"] for i in body["items"]] == ["p1"]
    # Facets cover the whole catalogue, so the filter keeps its own options.
    assert body["packs"] == [{"id": "starter", "name": "Starter", "count": 1}]
    assert body["languages"] == ["English"]
    assert client.get("/community/items", params={"pack": "missing"}).json()["total"] == 0


def test_pack_cannot_claim_another_sources_items():
    packs = community._pack_index([
        {"id": "x", "name": "X", "item_ids": ["p1"], "_source_repo": "a/one"},
        {"id": "bad id!", "name": "Bad"},
        {"id": "y", "name": "  "},
    ])
    assert list(packs) == ["x"]
    assert packs["x"]["members"] == {("a/one", "p1")}


@pytest.mark.parametrize("raw, kept", [
    ("2026-10-26", "2026-10-26"),
    ("2026-02-30", None),
    ("26-10-2026", None),
    ("2026-10-26T00:00:00", None),
    (20261026, None),
])
def test_added_at_is_kept_only_as_a_real_date(raw, kept):
    item = community.validate_item({**_FIXTURE["items"][0], "added_at": raw})
    assert item is not None
    assert item.get("added_at") == kept


@pytest.mark.parametrize("raw, kept", [
    ("  iluucasz ", "iluucasz"),
    (123, None),
    ({"login": "x"}, None),
    ("   ", None),
    ("x" * 500, "x" * 120),
])
def test_credit_fields_are_bounded_strings(raw, kept):
    item = community.validate_item({**_FIXTURE["items"][0], "author": raw, "license": raw})
    assert item is not None
    assert item.get("author") == kept
    assert item.get("license") == kept


def test_sources_endpoint(client):
    assert client.get("/community/sources").json()["sources"]


def test_submit_url(client):
    preset = client.get("/community/submit-url", params={"type": "preset"}).json()["url"]
    voice = client.get("/community/submit-url", params={"type": "voice"}).json()["url"]
    assert "preset-submission.yml" in preset and "omnivoice-gallery" in preset
    assert "voice-submission.yml" in voice


# ── bounded cache freshness + stale offline fallback ─────────────────────────
def test_stale_manifest_refreshes_then_stays_fresh(tmp_path, monkeypatch):
    monkeypatch.setattr(community, "_CACHE_DIR", tmp_path)
    source = "debpalash/omnivoice-gallery"
    cache = community._cache_path(source)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(_FIXTURE), encoding="utf-8")
    os.utime(cache, (100.0, 100.0))

    fresh = {**_FIXTURE, "updated_at": "new"}
    calls = []
    monkeypatch.setattr(
        community, "_fetch_remote_manifest",
        lambda src: calls.append(src) or fresh,
    )
    now = 100.0 + community._MANIFEST_MAX_AGE_S + 1
    assert community._fetch_manifest(source, False, now=now)["updated_at"] == "new"
    assert community._fetch_manifest(source, False, now=now + 1)["updated_at"] == "new"
    assert calls == [source]


def test_stale_manifest_falls_back_and_throttles_offline_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(community, "_CACHE_DIR", tmp_path)
    source = "debpalash/omnivoice-gallery"
    cache = community._cache_path(source)
    cache.parent.mkdir(parents=True)
    cache.write_text(json.dumps(_FIXTURE), encoding="utf-8")
    os.utime(cache, (100.0, 100.0))

    calls = []
    def offline(src):
        calls.append(src)
        raise OSError("offline")
    monkeypatch.setattr(community, "_fetch_remote_manifest", offline)
    now = 100.0 + community._MANIFEST_MAX_AGE_S + 1
    assert community._fetch_manifest(source, False, now=now) == _FIXTURE
    assert community._fetch_manifest(source, False, now=now + 1) == _FIXTURE
    assert calls == [source]


def test_manifest_fetch_is_bounded(monkeypatch):
    monkeypatch.setattr(community, "_MAX_MANIFEST_BYTES", 8)

    class Response:
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self): yield b'{"items":[]}'
    class Client:
        def stream(self, method, url, **kwargs):
            assert method == "GET"
            assert url.startswith("https://cdn.jsdelivr.net/")
            assert kwargs == {"follow_redirects": False}
            return Response()

    with pytest.raises(ValueError, match="size limit"):
        community._fetch_remote_manifest("test/source", client=Client())


def test_manifest_fetch_rejects_redirect_before_external_request():
    requested = []

    class Response:
        status_code = 302
        headers = {"location": "https://evil.example/manifest.json"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    class Client:
        def stream(self, _method, url, **_kwargs):
            requested.append(url)
            return Response()

    with pytest.raises(ValueError, match="disallowed host"):
        community._fetch_remote_manifest("test/source", client=Client())
    assert requested == [community._manifest_url("test/source")]


# ── Preview proxy ─────────────────────────────────────────────────────────────
def test_canonical_preset_preview_delegates_same_origin(client, monkeypatch):
    from core import archetypes
    from api.routers import archetypes as arch_router

    canonical = archetypes.list_archetypes(featured=True)[0]
    item = community.validate_item({
        **canonical, "type": "preset", "source": "starter",
    })
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    delegated = []

    async def preview(archetype_id, local=False):
        delegated.append((archetype_id, local))
        return Response(_wav_bytes(), media_type="audio/wav")

    monkeypatch.setattr(arch_router, "preview_archetype", preview)
    response = client.get(f"/community/items/{item['id']}/preview")
    local = client.get(f"/community/items/{item['id']}/preview?local=true")

    assert response.status_code == local.status_code == 200
    assert "location" not in response.headers
    assert delegated == [(item["id"], False), (item["id"], True)]


def test_noncanonical_preset_preview_renders_once(client, tmp_path, monkeypatch):
    item = community.validate_item(_FIXTURE["items"][0])
    monkeypatch.setattr(community, "_CACHE_DIR", tmp_path)
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    from api.routers import archetypes as arch_router
    calls = []
    async def render(_item, path):
        calls.append(path)
        _write_wav(Path(path))
    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)

    first = client.get("/community/items/p1/preview")
    second = client.get("/community/items/p1/preview")
    assert first.status_code == second.status_code == 200
    assert first.content == _wav_bytes()
    assert first.headers["x-omnivoice-preview-source"] == "community"
    assert len(calls) == 1

    community._preset_preview_path(item).write_bytes(b"not audio")
    repaired = client.get("/community/items/p1/preview")
    assert repaired.status_code == 200
    assert repaired.content == _wav_bytes()
    assert len(calls) == 2


def test_recorded_preview_is_served_from_same_origin(client, tmp_path, monkeypatch):
    item = community.validate_item(_FIXTURE["items"][3])
    clip = tmp_path / "voice.wav"
    expected = _write_wav(clip)
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    monkeypatch.setattr(community, "_cached_voice_audio", lambda _item: clip)
    response = client.get("/community/items/v1/preview")
    assert response.status_code == 200
    assert response.content == expected


def test_recorded_download_cap_is_atomic(tmp_path, monkeypatch):
    item = community.validate_item(_FIXTURE["items"][3])
    destination = tmp_path / "voice.wav"
    destination.write_bytes(b"existing-good-audio")
    monkeypatch.setattr(community, "_MAX_VOICE_AUDIO_BYTES", 8)

    class Response:
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self): yield b"123456789"
    class Client:
        def stream(self, method, url, **kwargs):
            assert method == "GET" and url.startswith("https://github.com/")
            assert kwargs == {"follow_redirects": False}
            return Response()

    with pytest.raises(HTTPException) as exc:
        community._download_voice_audio(item, destination, client=Client())
    assert getattr(exc.value, "status_code", None) == 502
    assert destination.read_bytes() == b"existing-good-audio"
    assert not list(tmp_path.glob(".*.part"))


def test_recorded_download_rejects_redirect_before_external_request(tmp_path):
    item = community.validate_item(_FIXTURE["items"][3])
    requested = []

    class Response:
        status_code = 302
        headers = {"location": "https://evil.example/private.wav"}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
    class Client:
        def stream(self, _method, url, **_kwargs):
            requested.append(url)
            return Response()

    with pytest.raises(HTTPException) as exc:
        community._download_voice_audio(item, tmp_path / "voice.wav", client=Client())
    assert getattr(exc.value, "status_code", None) == 502
    assert requested == [item["audio"]["url"]]


def test_recorded_download_follows_allowlisted_redirect(tmp_path):
    item = community.validate_item(_FIXTURE["items"][3])
    destination = tmp_path / "voice.wav"
    requested = []
    expected = _wav_bytes()

    class Response:
        def __init__(self, status, headers, body=b""):
            self.status_code, self.headers, self.body = status, headers, body
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self): yield self.body
    class Client:
        def stream(self, _method, url, **_kwargs):
            requested.append(url)
            if len(requested) == 1:
                return Response(302, {"location": "https://objects.githubusercontent.com/v1.wav"})
            return Response(200, {}, expected)

    community._download_voice_audio(item, destination, client=Client())
    assert destination.read_bytes() == expected
    assert requested == [item["audio"]["url"], "https://objects.githubusercontent.com/v1.wav"]


def test_recorded_download_rejects_non_audio_bytes(tmp_path):
    item = community.validate_item(_FIXTURE["items"][3])
    destination = tmp_path / "voice.wav"

    class Response:
        status_code = 200
        headers = {}
        def __enter__(self): return self
        def __exit__(self, *_args): return False
        def raise_for_status(self): return None
        def iter_bytes(self): yield b"this is not audio"
    class Client:
        def stream(self, _method, _url, **_kwargs): return Response()

    with pytest.raises(HTTPException, match="valid WAV"):
        community._download_voice_audio(item, destination, client=Client())
    assert not destination.exists()
    assert not list(tmp_path.glob(".*.part"))


# ── Materialization ───────────────────────────────────────────────────────────
def test_community_use_is_idempotent_design_profile_and_defers_render(
    client, monkeypatch,
):
    from core import event_bus
    from core.db import db_conn, init_db
    from api.routers import archetypes as arch_router

    init_db()
    item = community.validate_item(_FIXTURE["items"][0])
    item["_source_repo"] = "test/source"
    personality = community._community_personality(item)
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    emitted = []
    async def unexpected_render(*_args, **_kwargs):
        raise AssertionError("Use voice must not load or run the synthesis model")
    monkeypatch.setattr(arch_router, "_render_archetype_wav", unexpected_render)
    monkeypatch.setattr(
        event_bus, "emit", lambda topic, payload: emitted.append((topic, payload)),
    )
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (item["id"], personality),
        )

    first = client.post("/community/items/p1/use")
    second = client.post("/community/items/p1/use")
    assert first.status_code == second.status_code == 200
    assert second.json()["profile_id"] == first.json()["profile_id"]
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (first.json()["profile_id"],),
        ).fetchone()
    assert row["kind"] == "design"
    assert row["personality"] == personality
    assert json.loads(row["vd_states"])["Gender"] == "female"
    assert row["instruct"] == item["instruct"]
    assert row["ref_audio_path"] is None
    assert emitted[-1] == (
        "profiles", {"action": "updated", "id": first.json()["profile_id"]},
    )

def test_recorded_community_use_is_idempotent_clone_profile(client, tmp_path, monkeypatch):
    from core.db import db_conn, init_db

    init_db()
    item = community.validate_item(_FIXTURE["items"][3])
    item["_source_repo"] = "test/source"
    personality = community._community_personality(item)
    clip = tmp_path / "recorded.wav"
    _write_wav(clip)
    cache_calls = []
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    monkeypatch.setattr(
        community, "_cached_voice_audio", lambda _item: cache_calls.append(_item["id"]) or clip,
    )
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (item["id"], personality),
        )

    first = client.post("/community/items/v1/use")
    second = client.post("/community/items/v1/use")
    assert first.status_code == second.status_code == 200
    assert second.json()["profile_id"] == first.json()["profile_id"]
    assert cache_calls == ["v1"]
    with db_conn() as conn:
        row = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (first.json()["profile_id"],),
        ).fetchone()
    assert row["kind"] == "clone"
    assert row["personality"] == personality
    assert row["vd_states"] is None and row["instruct"] == ""
    assert row["ref_text"] == ""

    old_audio_filename = row["ref_audio_path"]
    item["audio"]["url"] = "https://raw.githubusercontent.com/test/source/main/v2.wav"
    refreshed = client.post("/community/items/v1/use")
    assert refreshed.status_code == 200
    assert refreshed.json()["profile_id"] == first.json()["profile_id"]
    assert cache_calls == ["v1", "v1"]
    with db_conn() as conn:
        refreshed_row = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?",
            (first.json()["profile_id"],),
        ).fetchone()
    assert refreshed_row["ref_audio_path"] != old_audio_filename


def test_noncanonical_builtin_id_cannot_heal_archetype_profile(client, monkeypatch):
    from core import archetypes
    from core.db import db_conn, init_db
    from api.routers import archetypes as arch_router

    init_db()
    canonical = archetypes.list_archetypes(featured=True)[0]
    changed_instruct = "female" if canonical["instruct"] != "female" else "male"
    item = community.validate_item({
        **canonical,
        "type": "preset",
        "source": "community",
        "instruct": changed_instruct,
    })
    item["_source_repo"] = "test/source"
    personality = community._community_personality(item)
    builtin_profile_id = f"b{os.urandom(4).hex()[:7]}"
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (canonical["id"], personality),
        )
        conn.execute(
            "INSERT INTO voice_profiles (id, name, personality, instruct, kind, created_at) "
            "VALUES (?, 'Built-in profile', ?, 'sentinel', 'design', 1)",
            (builtin_profile_id, canonical["id"]),
        )
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    async def render(_item, path):
        _write_wav(Path(path))
    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)

    response = client.post(f"/community/items/{canonical['id']}/use")
    assert response.status_code == 200
    assert response.json()["profile_id"] != builtin_profile_id
    with db_conn() as conn:
        builtin = conn.execute(
            "SELECT instruct FROM voice_profiles WHERE id=?", (builtin_profile_id,),
        ).fetchone()
        community_row = conn.execute(
            "SELECT personality FROM voice_profiles WHERE id=?",
            (response.json()["profile_id"],),
        ).fetchone()
        conn.execute(
            "DELETE FROM voice_profiles WHERE id IN (?, ?)",
            (builtin_profile_id, response.json()["profile_id"]),
        )
    assert builtin["instruct"] == "sentinel"
    assert community_row["personality"] == personality


def test_community_use_does_not_rewrite_an_imported_bare_id_collision(
    client, monkeypatch,
):
    from core.config import VOICES_DIR
    from core.db import db_conn, init_db
    from api.routers import archetypes as arch_router

    init_db()
    item = community.validate_item(_FIXTURE["items"][0])
    item["_source_repo"] = "test/source"
    personality = community._community_personality(item)
    imported_id = "importedcomm"
    imported_ns_id = "importedcommns"
    imported_audio = Path(VOICES_DIR) / f"{imported_id}.wav"
    imported_ns_audio = Path(VOICES_DIR) / f"{imported_ns_id}.wav"
    original_audio = _write_wav(imported_audio)
    original_ns_audio = _write_wav(imported_ns_audio)
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (item["id"], personality),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, is_locked, verified_own_voice, created_at) VALUES "
            "(?, 'Imported collision', ?, 'user transcript', 'male', 'Auto', NULL, ?, "
            "'clone', 1, 1, 1)",
            (imported_id, imported_audio.name, item["id"]),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, vd_states, is_locked, verified_own_voice, created_at) VALUES "
            "(?, 'Imported namespaced collision', ?, ?, ?, ?, 42, ?, "
            "'design', NULL, 0, 0, 2)",
            (
                imported_ns_id, imported_ns_audio.name, item["sample_script"],
                item["instruct"], item["language"], personality,
            ),
        )
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )

    async def render(_item, path):
        _write_wav(Path(path))

    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)
    response = client.post(f"/community/items/{item['id']}/use")
    assert response.status_code == 200
    assert response.json()["profile_id"] != imported_id
    with db_conn() as conn:
        imported = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (imported_id,),
        ).fetchone()
        imported_ns = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (imported_ns_id,),
        ).fetchone()
        created = conn.execute(
            "SELECT * FROM voice_profiles WHERE id=?", (response.json()["profile_id"],),
        ).fetchone()
    assert imported["personality"] == item["id"]
    assert imported["instruct"] == "male"
    assert imported["ref_text"] == "user transcript"
    assert imported_audio.read_bytes() == original_audio
    assert imported_ns["instruct"] == item["instruct"]
    assert imported_ns["ref_text"] == item["sample_script"]
    assert imported_ns["vd_states"] is None
    assert imported_ns_audio.read_bytes() == original_ns_audio
    assert created["personality"] == personality


def test_noncolliding_legacy_community_profile_is_adopted(client, monkeypatch):
    from core.config import VOICES_DIR
    from core.db import db_conn, init_db
    from api.routers import archetypes as arch_router

    init_db()
    item = community.validate_item(_FIXTURE["items"][0])
    item["_source_repo"] = "test/source"
    personality = community._community_personality(item)
    legacy_id = f"l{os.urandom(4).hex()[:7]}"
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (item["id"], personality),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, vd_states, created_at) VALUES "
            "(?, 'Legacy community profile', ?, '', ?, ?, NULL, ?, 'design', NULL, 1)",
            (legacy_id, f"{legacy_id}.wav", item["instruct"], item["language"], item["id"]),
        )
    _write_wav(Path(VOICES_DIR) / f"{legacy_id}.wav")
    monkeypatch.setattr(
        community, "_load", lambda _refresh: (["test/source"], [item], [], False),
    )
    community._preset_preview_path(item).unlink(missing_ok=True)
    rendered = []
    async def render(_item, path):
        rendered.append(path)
        _write_wav(Path(path))
    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)

    response = client.post(f"/community/items/{item['id']}/use")
    assert response.status_code == 200
    assert response.json()["profile_id"] == legacy_id
    assert rendered == []
    with db_conn() as conn:
        adopted = conn.execute(
            "SELECT personality, kind, ref_audio_path FROM voice_profiles WHERE id=?",
            (legacy_id,),
        ).fetchone()
    assert adopted["personality"] == personality
    assert adopted["kind"] == "design"
    assert adopted["ref_audio_path"] == f"{legacy_id}.wav"
    assert (Path(VOICES_DIR) / adopted["ref_audio_path"]).is_file()
