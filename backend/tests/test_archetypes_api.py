"""API contract tests for the archetype router (``api.routers.archetypes``).

These cover the parts that don't need the 5 GB TTS model: category listing,
filtering, pagination, lookup, 404s, and the preview *cache-hit* path (a
pre-existing cached WAV is served without invoking the model). A cold preview
calls the real inference pipeline; ``/use`` intentionally stores only the
recipe so choosing a voice stays instant.
"""
from __future__ import annotations

import io
import json
from pathlib import Path
import wave

import pytest

# conftest.py puts `backend/` on sys.path and points OMNIVOICE_DATA_DIR at a
# throwaway tmpdir before the router imports VOICES_DIR / OUTPUTS_DIR from
# the REAL core.config (the old sys.modules stub leaked at collection time).
from fastapi import FastAPI  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from core import archetypes  # noqa: E402
from api.routers import archetypes as arch_router  # noqa: E402


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


@pytest.fixture(scope="module")
def client():
    app = FastAPI()
    app.include_router(arch_router.router)
    return TestClient(app)


# ── Categories ────────────────────────────────────────────────────────────────
def test_categories_endpoint(client):
    r = client.get("/archetypes/categories")
    assert r.status_code == 200
    ids = {c["id"] for c in r.json()}
    assert ids == {
        "narration", "conversational", "characters",
        "social", "entertainment", "advertisement", "informative",
    }


# ── Listing + pagination ──────────────────────────────────────────────────────
def test_list_returns_paginated_envelope(client):
    r = client.get("/archetypes", params={"limit": 10})
    assert r.status_code == 200
    body = r.json()
    assert set(body) >= {"total", "limit", "offset", "items"}
    assert body["total"] >= 250
    assert len(body["items"]) == 10


def test_list_offset_advances(client):
    first = client.get("/archetypes", params={"limit": 5, "offset": 0}).json()
    second = client.get("/archetypes", params={"limit": 5, "offset": 5}).json()
    assert first["total"] == second["total"]
    assert [i["id"] for i in first["items"]] != [i["id"] for i in second["items"]]


# ── Filters ───────────────────────────────────────────────────────────────────
def test_filter_featured(client):
    body = client.get("/archetypes", params={"featured": "true", "limit": 100}).json()
    assert body["items"]
    assert all(a["is_featured"] for a in body["items"])


def test_filter_use_case(client):
    body = client.get("/archetypes", params={"use_case": "narration", "limit": 20}).json()
    assert body["items"]
    assert all(a["use_case"] == "narration" for a in body["items"])


def test_filter_gender(client):
    body = client.get("/archetypes", params={"gender": "female", "limit": 20}).json()
    assert body["items"]
    assert all(a["facets"]["gender"] == "female" for a in body["items"])


def test_filter_language_chinese(client):
    body = client.get("/archetypes", params={"lang": "Chinese", "limit": 20}).json()
    assert body["items"]
    assert all(a["language"] == "Chinese" for a in body["items"])


# ── Free-text search (voice-picker gallery search) ────────────────────────────
def test_q_substring_search_by_name(client):
    """`q` reaches a featured voice by name — the gallery picker's search box."""
    body = client.get("/archetypes", params={"q": "librarian", "limit": 50}).json()
    assert body["items"]
    assert all("librarian" in a["name"].lower() for a in body["items"])


def test_q_matches_instruct_tokens(client):
    """`q` also matches instruct tokens (e.g. an accent) so typing narrows the
    several-hundred-voice catalog instead of only the loaded page."""
    body = client.get("/archetypes", params={"q": "british", "limit": 500}).json()
    assert body["items"]
    assert all("british" in a["instruct"].lower() or "british" in a["name"].lower()
               for a in body["items"])


def test_q_empty_is_noop(client):
    """A blank/whitespace `q` must not filter — it's the default picker state."""
    everything = client.get("/archetypes", params={"limit": 500}).json()["total"]
    blank = client.get("/archetypes", params={"q": "   ", "limit": 500}).json()["total"]
    assert blank == everything


# ── Lookup + 404s ─────────────────────────────────────────────────────────────
def test_get_single(client):
    sample = archetypes.list_archetypes(featured=True)[0]
    r = client.get(f"/archetypes/{sample['id']}")
    assert r.status_code == 200
    assert r.json()["id"] == sample["id"]


def test_get_missing_404(client):
    assert client.get("/archetypes/nope-xyz").status_code == 404


def test_preview_missing_404(client):
    assert client.get("/archetypes/nope-xyz/preview").status_code == 404


def test_use_missing_404(client):
    assert client.post("/archetypes/nope-xyz/use").status_code == 404


# ── Preview cache-hit (no model needed) ───────────────────────────────────────
def test_preview_serves_cached_wav_without_model(client):
    sample = archetypes.list_archetypes(featured=True)[0]
    key = arch_router._preview_key(sample)
    cache_dir = Path(arch_router._PREVIEW_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    dummy = _write_wav(cache_dir / f"{key}.wav")

    r = client.get(f"/archetypes/{sample['id']}/preview")
    assert r.status_code == 200
    assert r.headers["content-type"] == "audio/wav"
    assert r.content == dummy


# ── Save-on-use idempotency (dedup, deferred render) ──────────────────────────
def test_use_is_idempotent_and_defers_render(client, monkeypatch):
    """Choosing a recipe is instant; only preview/generate may load the model."""
    from core import event_bus
    from core.db import db_conn, init_db

    init_db()  # ensure the voice_profiles table exists in the hermetic tmp DB

    async def _unexpected_render(*_args, **_kwargs):
        raise AssertionError("Use voice must not load or run the synthesis model")

    monkeypatch.setattr(arch_router, "_render_archetype_wav", _unexpected_render)
    emitted = []
    monkeypatch.setattr(
        event_bus, "emit", lambda topic, payload: emitted.append((topic, payload)),
    )

    sample = archetypes.list_archetypes(featured=True)[0]

    first = client.post(f"/archetypes/{sample['id']}/use")
    assert first.status_code == 200
    pid = first.json()["profile_id"]
    assert pid

    second = client.post(f"/archetypes/{sample['id']}/use")
    assert second.status_code == 200
    assert second.json()["profile_id"] == pid  # same row reused

    with db_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM voice_profiles WHERE personality = ?",
            (arch_router._archetype_personality(sample),),
        ).fetchall()
    assert len(rows) == 1
    assert rows[0]["kind"] == "design"
    assert json.loads(rows[0]["vd_states"]) == sample["attrs"]

    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (pid,)).fetchone()
    assert row["kind"] == "design"
    assert row["instruct"] == sample["instruct"]
    assert json.loads(row["vd_states"]) == sample["attrs"]
    assert row["ref_audio_path"] is None

    with db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET instruct='male' WHERE id=?", (pid,))
    refreshed = client.post(f"/archetypes/{sample['id']}/use")
    assert refreshed.status_code == 200
    assert refreshed.json()["profile_id"] != pid
    with db_conn() as conn:
        edited = conn.execute("SELECT instruct FROM voice_profiles WHERE id=?", (pid,)).fetchone()
        replacement = conn.execute(
            "SELECT ref_audio_path FROM voice_profiles WHERE id=?",
            (refreshed.json()["profile_id"],),
        ).fetchone()
    assert edited["instruct"] == "male"
    assert replacement["ref_audio_path"] is None


def test_archetype_use_adopts_only_a_compatible_legacy_row(client, monkeypatch):
    from core.config import VOICES_DIR
    from core.db import db_conn, init_db

    init_db()
    sample = archetypes.list_archetypes(featured=True)[1]
    legacy_id = "legacyarch"
    legacy_audio = Path(VOICES_DIR) / f"{legacy_id}.wav"
    _write_wav(legacy_audio)
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (sample["id"], arch_router._archetype_personality(sample)),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, vd_states, created_at) VALUES (?, 'Legacy archetype', ?, ?, ?, ?, 42, ?, "
            "'clone', NULL, 1)",
            (
                legacy_id, legacy_audio.name, sample["sample_script"], sample["instruct"],
                sample["language"], sample["id"],
            ),
        )

    async def unexpected_render(*_args):
        raise AssertionError("a valid legacy archetype sample must be reused")

    monkeypatch.setattr(arch_router, "_render_archetype_wav", unexpected_render)
    response = client.post(f"/archetypes/{sample['id']}/use")
    assert response.status_code == 200
    assert response.json()["profile_id"] == legacy_id
    with db_conn() as conn:
        row = conn.execute("SELECT * FROM voice_profiles WHERE id=?", (legacy_id,)).fetchone()
    assert row["personality"] == arch_router._archetype_personality(sample)
    assert row["kind"] == "design"
    assert json.loads(row["vd_states"]) == sample["attrs"]
    assert row["ref_audio_path"] == legacy_audio.name
    assert legacy_audio.is_file()


def test_archetype_use_does_not_rewrite_an_imported_personality_collision(
    client, monkeypatch,
):
    from core.config import VOICES_DIR
    from core.db import db_conn, init_db

    init_db()
    sample = archetypes.list_archetypes(featured=True)[2]
    imported_id = "importedarch"
    imported_ns_id = "importedarchns"
    imported_audio = Path(VOICES_DIR) / f"{imported_id}.wav"
    imported_ns_audio = Path(VOICES_DIR) / f"{imported_ns_id}.wav"
    original_audio = _write_wav(imported_audio)
    original_ns_audio = _write_wav(imported_ns_audio)
    with db_conn() as conn:
        conn.execute(
            "DELETE FROM voice_profiles WHERE personality IN (?, ?)",
            (sample["id"], arch_router._archetype_personality(sample)),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, is_locked, verified_own_voice, created_at) VALUES "
            "(?, 'Imported collision', ?, 'user transcript', 'male', 'Auto', NULL, ?, "
            "'clone', 1, 1, 1)",
            (imported_id, imported_audio.name, sample["id"]),
        )
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
            "kind, vd_states, is_locked, verified_own_voice, created_at) VALUES "
            "(?, 'Imported namespaced collision', ?, ?, ?, ?, 42, ?, "
            "'design', NULL, 0, 0, 2)",
            (
                imported_ns_id, imported_ns_audio.name, sample["sample_script"],
                sample["instruct"], sample["language"],
                arch_router._archetype_personality(sample),
            ),
        )

    async def render(_item, path):
        _write_wav(Path(path))

    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)
    response = client.post(f"/archetypes/{sample['id']}/use")
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
    assert imported["personality"] == sample["id"]
    assert imported["instruct"] == "male"
    assert imported["ref_text"] == "user transcript"
    assert imported_audio.read_bytes() == original_audio
    assert imported_ns["instruct"] == sample["instruct"]
    assert imported_ns["ref_text"] == sample["sample_script"]
    assert imported_ns["vd_states"] is None
    assert imported_ns_audio.read_bytes() == original_ns_audio
    assert created["personality"] == arch_router._archetype_personality(sample)


# ── Preview render single-flight ──────────────────────────────────────────────
def test_concurrent_previews_share_one_render(monkeypatch, tmp_path):
    """Re-clicks, media re-requests and a second view previewing the same voice
    join one render instead of each queueing their own on the GPU pool."""
    import asyncio

    calls = []

    async def render(_a, out_path):
        calls.append(out_path)
        await asyncio.sleep(0.05)
        _write_wav(Path(out_path))

    monkeypatch.setattr(arch_router, "_render_archetype_wav", render)
    monkeypatch.setattr(arch_router.gallery, "cached_preview", lambda _key: None)

    async def fetch_none(_key, **_kw):
        return None

    monkeypatch.setattr(arch_router.gallery, "fetch_preview", fetch_none)
    monkeypatch.setattr(arch_router, "_PREVIEW_DIR", tmp_path)
    sample = archetypes.list_archetypes(featured=True)[1]

    async def run():
        return await asyncio.gather(
            *(arch_router.preview_archetype(sample["id"], local=False) for _ in range(3))
        )

    responses = asyncio.run(run())
    assert len(calls) == 1
    assert {r.headers["X-OmniVoice-Preview-Source"] for r in responses} == {"local"}
    assert not arch_router._renders


def test_dropped_preview_request_still_finishes_the_render(monkeypatch, tmp_path):
    """A cancelled request (user cancels, client disconnects) must not discard
    the render: the clip lands in the cache so the next click is instant."""
    import asyncio

    out = tmp_path / "clip.wav"

    async def render():
        await asyncio.sleep(0.05)
        _write_wav(out)

    async def run():
        waiter = asyncio.ensure_future(arch_router._render_once(str(out), render))
        await asyncio.sleep(0)
        waiter.cancel()
        with pytest.raises(asyncio.CancelledError):
            await waiter
        await asyncio.sleep(0.1)

    asyncio.run(run())
    assert out.is_file()
    assert not arch_router._renders
