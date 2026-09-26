"""Pre-rendered voice previews — trust, consent, and the fallback to rendering.

Five things must hold, and every one of them is silent when it breaks:

* the gallery is **opt-in** — until the user says yes, not one byte leaves the
  machine (a "disableable" background fetch is not consent);
* a manifest is worthless unless its **signature verifies against the key
  already in the binary**, so an unsigned/tampered/foreign-key manifest is
  discarded rather than downgraded to a warning — including one already on disk;
* an update re-fetches **only previews already cached**, never the 1075 the user
  never asked for;
* a network gallery is a **filesystem-write primitive**: keys, filenames and tar
  members are all attacker-supplied strings;
* ``/archetypes/{id}/use`` **never** reads a gallery file — that WAV becomes the
  reference audio a cloned voice is built from.

CI runs with ``HF_HUB_OFFLINE=1`` and there is no HTTP-stub fixture in this
suite, so :class:`StubGallery` below is one: an ``httpx.MockTransport`` that
serves a signed bundle from memory and counts every request, which is what makes
"made no request at all" an assertable outcome.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import io
import importlib.util
import json
import tarfile
import wave
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from services import gallery

_REPO = Path(__file__).resolve().parents[1]


# ── minisign fixtures ────────────────────────────────────────────────────────

class Signer:
    """An ephemeral minisign identity: makes the pubkey blob and the .minisig."""

    def __init__(self, algorithm: bytes = b"Ed", key_id: bytes = b"\x01\x02\x03\x04\x05\x06\x07\x08"):
        self._key = Ed25519PrivateKey.generate()
        self.algorithm = algorithm
        self.key_id = key_id

    @property
    def pubkey(self) -> str:
        raw = self.algorithm + self.key_id + self._key.public_key().public_bytes_raw()
        text = (
            "untrusted comment: minisign public key\n"
            + base64.b64encode(raw).decode("ascii")
            + "\n"
        )
        return base64.b64encode(text.encode("ascii")).decode("ascii")

    def sign(self, payload: bytes, *, trusted: str = "timestamp:0") -> str:
        signed = (
            hashlib.blake2b(payload, digest_size=64).digest()
            if self.algorithm == b"ED" else payload
        )
        sig = self._key.sign(signed)
        blob = base64.b64encode(self.algorithm + self.key_id + sig).decode("ascii")
        # minisign's global signature covers the 64-byte signature plus the
        # trusted comment — not the algorithm/key-id prefix.
        global_sig = base64.b64encode(
            self._key.sign(sig + trusted.encode("utf-8"))
        ).decode("ascii")
        return (
            "untrusted comment: signature\n"
            f"{blob}\n"
            f"trusted comment: {trusted}\n"
            f"{global_sig}\n"
        )


def _mp3(seed: bytes) -> bytes:
    """Stand-in preview bytes — content is irrelevant, its digest is not."""
    return b"ID3" + seed * 16


def _wav(seed: bytes) -> bytes:
    """Small valid PCM WAV used when the route's decoder boundary is under test."""
    payload = (seed or b"\x00") * 64
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(1)
        wav.setframerate(8_000)
        wav.writeframes(payload)
    return buf.getvalue()


class StubGallery:
    """In-memory signed gallery + an httpx transport that serves it.

    ``requests`` records every URL asked for, so a test can assert that a
    disabled or throttled client asked for nothing at all.
    """

    BASE = "https://gallery.test"

    def __init__(self, signer: Signer, previews: dict[str, bytes], featured: set[str] | None = None):
        self.signer = signer
        self.files = dict(previews)
        self.featured = set(featured or ())
        self.requests: list[str] = []
        self.etag = '"v1"'
        self.raises: Exception | None = None
        self._rebuild()

    def _rebuild(self) -> None:
        entries = {
            key: {
                "filename": f"{key}.mp3",
                "sha256": hashlib.sha256(body).hexdigest(),
                "bytes": len(body),
                "duration": 3.5,
                "featured": key in self.featured,
            }
            for key, body in self.files.items()
        }
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for key in sorted(self.featured):
                info = tarfile.TarInfo(f"previews/{key}.mp3")
                info.size = len(self.files[key])
                tar.addfile(info, io.BytesIO(self.files[key]))
        self.tarball = buf.getvalue()
        manifest = {
            "schema": gallery.SCHEMA_VERSION,
            "generated_at": 1,
            "engine": "omnivoice",
            "engine_version": "0.0.0-test",
            "featured": {
                "filename": "featured.tar.gz",
                "sha256": hashlib.sha256(self.tarball).hexdigest(),
                "bytes": len(self.tarball),
                "count": len(self.featured),
            },
            "previews": entries,
        }
        self.manifest_bytes = json.dumps(manifest, sort_keys=True).encode("utf-8")
        self.signature = self.signer.sign(self.manifest_bytes)

    def replace(self, key: str, body: bytes) -> None:
        """Re-render one preview: same key, different bytes — the update case."""
        self.files[key] = body
        self.etag = '"v2"'
        self._rebuild()

    def set_tarball(self, data: bytes) -> None:
        """Publish a hostile/broken tarball while keeping the manifest honest."""
        self.tarball = data

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            if self.raises is not None:
                raise self.raises
            path = request.url.path
            self.requests.append(path)
            if path.endswith("/manifest.json"):
                if request.headers.get("if-none-match") == self.etag:
                    return httpx.Response(304, headers={"ETag": self.etag})
                return httpx.Response(200, content=self.manifest_bytes,
                                      headers={"ETag": self.etag})
            if path.endswith("/manifest.json.minisig"):
                return httpx.Response(200, content=self.signature.encode("utf-8"))
            if path.endswith("/featured.tar.gz"):
                return httpx.Response(200, content=self.tarball)
            name = path.rsplit("/", 1)[-1]
            if name.endswith(".mp3") and name[:-4] in self.files:
                return httpx.Response(200, content=self.files[name[:-4]])
            return httpx.Response(404)

        return httpx.MockTransport(handle)

    def client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(transport=self.transport())


KEY_A = "a" * 16
KEY_B = "b" * 16
KEY_C = "c" * 16


@pytest.fixture
def signer() -> Signer:
    return Signer()


@pytest.fixture
def stub(signer) -> StubGallery:
    return StubGallery(
        signer,
        {KEY_A: _mp3(b"A"), KEY_B: _mp3(b"B"), KEY_C: _mp3(b"C")},
        featured={KEY_A, KEY_B},
    )


@pytest.fixture(autouse=True)
def _live_gallery():
    """Re-resolve this module's ``gallery`` alias against sys.modules.

    ``tests/backend/conftest.py`` purges ``services.*`` after every test it
    runs, so in a full-suite run the import-time alias above is a dead module
    object while ``api/routers/archetypes.py`` binds a fresh one. Patching
    ``DATA_DIR``/``UPDATER_PUBKEY`` on the stale copy leaves the router reading
    the real ones — which is why these tests pass alone and fail in the suite.
    """
    global gallery

    import services.gallery  # noqa: PLC0415 — must resolve post-purge

    gallery = services.gallery


@pytest.fixture
def sandbox(tmp_path, monkeypatch, signer, _live_gallery):
    """Point the gallery at a throwaway data dir, host and trust root."""
    monkeypatch.setattr(gallery, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(gallery, "UPDATER_PUBKEY", signer.pubkey)
    monkeypatch.setenv("OMNIVOICE_GALLERY_URL", StubGallery.BASE)
    # Local renders are the competing cache tier; isolate them too so route
    # tests cannot pass/fail based on a developer's real outputs directory.
    from api.routers import archetypes as router_mod
    monkeypatch.setattr(router_mod, "_PREVIEW_DIR", tmp_path / "local_previews")
    return tmp_path


# ── The key in the binary ────────────────────────────────────────────────────

def test_gallery_pubkey_is_valid():
    """The gallery trust root remains a usable minisign public key."""
    algorithm, _key_id, raw = gallery._decode_minisign_pubkey(gallery.UPDATER_PUBKEY)
    assert algorithm in (b"Ed", b"ED") and len(raw) == 32


# ── Signature verification ───────────────────────────────────────────────────

def test_verify_accepts_a_correctly_signed_manifest(stub, signer):
    manifest = gallery.verify_manifest(stub.manifest_bytes, stub.signature,
                                       pubkey=signer.pubkey)
    assert set(manifest["previews"]) == {KEY_A, KEY_B, KEY_C}


def test_verify_accepts_the_prehashed_algorithm():
    """minisign's 'ED' signs a BLAKE2b digest; both spellings exist in the wild."""
    signer = Signer(algorithm=b"ED")
    payload = json.dumps({"schema": gallery.SCHEMA_VERSION, "previews": {}}).encode()
    assert gallery.verify_manifest(payload, signer.sign(payload),
                                   pubkey=signer.pubkey)["previews"] == {}


@pytest.mark.parametrize("mutate", [
    pytest.param(lambda raw, sig: (raw + b" ", sig), id="tampered-manifest"),
    pytest.param(lambda raw, sig: (raw, sig.replace("trusted comment: timestamp:0",
                                                    "trusted comment: hacked")),
                 id="tampered-trusted-comment"),
    pytest.param(lambda raw, sig: (raw, "untrusted comment: x\n" + base64.b64encode(
        b"Ed" + b"\x01\x02\x03\x04\x05\x06\x07\x08" + b"\x00" * 64).decode() + "\n"),
        id="forged-signature"),
])
def test_verify_rejects_tampering(stub, signer, mutate):
    raw, sig = mutate(stub.manifest_bytes, stub.signature)
    with pytest.raises(gallery.GalleryError):
        gallery.verify_manifest(raw, sig, pubkey=signer.pubkey)


def test_verify_rejects_another_key(stub):
    with pytest.raises(gallery.GalleryError):
        gallery.verify_manifest(stub.manifest_bytes, stub.signature,
                                pubkey=Signer(key_id=b"\x09" * 8).pubkey)


def test_verify_rejects_an_unknown_schema(signer):
    payload = json.dumps({"schema": gallery.SCHEMA_VERSION + 1, "previews": {}}).encode()
    with pytest.raises(gallery.GalleryError):
        gallery.verify_manifest(payload, signer.sign(payload), pubkey=signer.pubkey)


@pytest.mark.asyncio
async def test_stored_manifest_is_reverified_on_every_load(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client())
    assert gallery.load_manifest() is not None

    stored = gallery.gallery_root() / "manifest.json"
    stored.write_bytes(stored.read_bytes().replace(b'"engine"', b'"englne"'))
    assert gallery.load_manifest() is None, "on-disk tampering must not be trusted"


# ── Consent ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_disabled_gallery_makes_no_request_at_all(sandbox, stub):
    """Opt-in means opt-in: no manifest poll, no featured fetch, no per-file GET."""
    assert gallery.is_enabled() is False
    await gallery.check_for_updates(force=True, client=stub.client())
    await gallery.fetch_featured(client=stub.client())
    assert await gallery.fetch_preview(KEY_A, client=stub.client()) is None
    assert stub.requests == []


@pytest.mark.asyncio
async def test_enabling_is_what_starts_the_first_fetch(sandbox, stub):
    gallery.set_enabled(True)
    result = await gallery.fetch_featured(client=stub.client())
    assert result["fetched"] == 2
    assert gallery.cached_preview(KEY_A) is not None
    assert gallery.cached_preview(KEY_C) is None, "only the featured set is bulk-fetched"


# ── Offline ──────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_offline_fails_silently(sandbox, stub):
    gallery.set_enabled(True)
    stub.raises = httpx.ConnectError("no route to host")
    status = await gallery.check_for_updates(force=True, client=stub.client())
    assert status["available"] is False and status.get("last_error") is None
    assert await gallery.fetch_preview(KEY_A, client=stub.client()) is None


@pytest.mark.asyncio
async def test_a_rejected_manifest_is_recorded_but_not_raised(sandbox, stub, signer):
    """Signature failure is the one remote state worth showing the user."""
    gallery.set_enabled(True)
    stub.signature = Signer(key_id=b"\xff" * 8).sign(stub.manifest_bytes)  # a stranger
    status = await gallery.check_for_updates(force=True, client=stub.client())
    assert status["available"] is False
    assert "different key" in status["last_error"]


# ── Throttle and updates ─────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_check_is_throttled_to_once_a_day(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(client=stub.client(), now=1000.0)
    first = len(stub.requests)
    assert first > 0

    await gallery.check_for_updates(client=stub.client(), now=1000.0 + 3600)
    assert len(stub.requests) == first, "a second check within 24 h must not ask"

    await gallery.check_for_updates(client=stub.client(),
                                    now=1000.0 + gallery.UPDATE_INTERVAL_S + 1)
    assert len(stub.requests) > first


@pytest.mark.asyncio
async def test_unchanged_manifest_is_a_conditional_request(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client(), now=10.0)
    stub.requests.clear()
    await gallery.check_for_updates(force=True, client=stub.client(), now=20.0)
    # 304 → the signature file is never even fetched.
    assert stub.requests == ["/manifest.json"]


@pytest.mark.asyncio
async def test_update_refetches_only_keys_already_cached(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client(), now=10.0)
    assert await gallery.fetch_preview(KEY_A, client=stub.client()) is not None

    stub.replace(KEY_A, _mp3(b"A2"))
    stub.replace(KEY_C, _mp3(b"C2"))
    stub.requests.clear()
    result = await gallery.check_for_updates(force=True, client=stub.client(), now=20.0)

    assert result["refreshed"] == 1
    assert f"/previews/{KEY_A}.mp3" in stub.requests
    assert f"/previews/{KEY_C}.mp3" not in stub.requests, "never bulk-fetch uncached previews"
    assert gallery.cached_preview(KEY_A).read_bytes() == _mp3(b"A2")


@pytest.mark.asyncio
async def test_background_refresh_respects_consent_and_the_throttle(sandbox, stub, monkeypatch):
    """The only thing that schedules the daily check must not schedule it early.

    Nothing else in the app polls, so if this fires while the gallery is off it
    is an outbound call the user never agreed to.
    """
    calls: list[bool] = []

    async def _record(**_kwargs):
        calls.append(True)
        return {}

    monkeypatch.setattr(gallery, "check_for_updates", _record)
    gallery.maybe_refresh_in_background()
    assert calls == [], "disabled gallery must not schedule a check"

    gallery.set_enabled(True)
    gallery.maybe_refresh_in_background()
    gallery.maybe_refresh_in_background()  # a second call must not pile up
    await gallery._refresh_task
    assert calls == [True]


def test_background_refresh_is_a_noop_without_a_loop(sandbox):
    gallery.set_enabled(True)
    gallery.maybe_refresh_in_background()  # sync context — must not raise


# ── Integrity ────────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_a_preview_that_fails_its_digest_is_not_committed(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client())
    stub.files[KEY_A] = _mp3(b"swapped")  # served bytes no longer match the manifest
    assert await gallery.fetch_preview(KEY_A, client=stub.client()) is None
    assert gallery.cached_preview(KEY_A) is None


@pytest.mark.asyncio
async def test_featured_tarball_digest_is_checked_before_it_is_opened(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client())
    stub.set_tarball(b"not even a tarball")
    result = await gallery.fetch_featured(client=stub.client())
    assert "fetched" not in result
    assert gallery.cached_preview(KEY_A) is None


@pytest.mark.asyncio
async def test_hostile_tar_members_are_ignored(sandbox, stub, tmp_path):
    """Traversal names, symlinks and unlisted keys never reach the filesystem."""
    gallery.set_enabled(True)
    await gallery.check_for_updates(force=True, client=stub.client())

    buf = io.BytesIO()
    payload = _mp3(b"A")
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        escape = tarfile.TarInfo("../../../../escaped.mp3")
        escape.size = len(payload)
        tar.addfile(escape, io.BytesIO(payload))
        link = tarfile.TarInfo(f"previews/{KEY_B}.mp3")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        good = tarfile.TarInfo(f"previews/{KEY_A}.mp3")
        good.size = len(payload)
        tar.addfile(good, io.BytesIO(payload))
    hostile = buf.getvalue()
    stub.set_tarball(hostile)
    # Re-sign a manifest that vouches for exactly these bytes, so the tarball
    # gets past the digest check and the member handling is what's under test.
    stub.manifest_bytes = stub.manifest_bytes.replace(
        hashlib.sha256(stub.files[KEY_A]).hexdigest().encode(), b"x" * 64, 0)
    manifest = json.loads(stub.manifest_bytes)
    manifest["featured"]["sha256"] = hashlib.sha256(hostile).hexdigest()
    manifest["featured"]["bytes"] = len(hostile)
    stub.manifest_bytes = json.dumps(manifest, sort_keys=True).encode()
    stub.signature = stub.signer.sign(stub.manifest_bytes)
    stub.etag = '"v3"'

    result = await gallery.fetch_featured(client=stub.client(), force=True)
    assert result["fetched"] == 1
    assert gallery.cached_preview(KEY_A) is not None
    assert gallery.cached_preview(KEY_B) is None
    assert not (tmp_path.parent / "escaped.mp3").exists()
    assert list(gallery.gallery_root().rglob("escaped.mp3")) == []


@pytest.mark.parametrize("key", ["../etc/passwd", "A" * 16, "", "a" * 15, "a/b"])
def test_preview_path_rejects_anything_that_is_not_a_key(sandbox, key):
    with pytest.raises(Exception):
        gallery.preview_path(key)
    assert gallery.cached_preview(key) is None


# ── Status line ──────────────────────────────────────────────────────────────

@pytest.mark.asyncio
async def test_status_reports_featured_coverage_and_freshness(sandbox, stub):
    gallery.set_enabled(True)
    await gallery.fetch_featured(client=stub.client(), now=1000.0)
    status = gallery.status(now=1000.0 + 2 * 86400)
    assert status["featured_total"] == 2 and status["featured_cached"] == 2
    assert status["engine"] == "omnivoice"
    assert status["checked_seconds_ago"] == pytest.approx(2 * 86400)


# ── Route integration ────────────────────────────────────────────────────────

@pytest.fixture
def client(sandbox):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from api.routers import archetypes as router_mod

    app = FastAPI()
    app.include_router(router_mod.router)
    return TestClient(app)


def _first_archetype():
    from api.routers.archetypes import _preview_key
    from core import archetypes as catalog

    item = catalog.list_archetypes(featured=True)[0]
    return item, _preview_key(item)


def test_preview_serves_the_gallery_file_without_touching_the_engine(client, sandbox, monkeypatch):
    item, key = _first_archetype()
    gallery.preview_path(key).write_bytes(_mp3(b"G"))

    async def _explode(*_a, **_k):  # the engine must not be reached
        raise AssertionError("render attempted despite a gallery hit")

    monkeypatch.setattr("api.routers.archetypes._render_archetype_wav", _explode)
    resp = client.get(f"/archetypes/{item['id']}/preview")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.headers["X-OmniVoice-Preview-Source"] == "gallery"
    assert resp.content == _mp3(b"G")


def test_offline_gallery_miss_still_renders_locally(client, sandbox, stub, monkeypatch, caplog):
    """Exercise the full route with an actual failing HTTP transport."""
    gallery.set_enabled(True)
    stub.raises = httpx.ConnectError("airplane mode")
    offline_client = stub.client()
    monkeypatch.setattr(gallery, "_client", lambda client=None: offline_client)

    local_wav = _wav(b"O")

    async def _render(_item, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(local_wav)

    monkeypatch.setattr("api.routers.archetypes._render_archetype_wav", _render)
    item, _ = _first_archetype()
    resp = client.get(f"/archetypes/{item['id']}/preview")
    assert resp.status_code == 200
    assert resp.headers["X-OmniVoice-Preview-Source"] == "local"
    assert resp.content == local_wav
    assert not [r for r in caplog.records if r.levelno >= 30]


def test_local_retry_bypasses_present_gallery_file(client, sandbox, monkeypatch):
    """A client-side decode error must be able to replace silence with a render."""
    item, key = _first_archetype()
    gallery.preview_path(key).write_bytes(_mp3(b"undecodable"))

    local_wav = _wav(b"L")

    async def _render(_item, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(local_wav)

    monkeypatch.setattr("api.routers.archetypes._render_archetype_wav", _render)
    resp = client.get(f"/archetypes/{item['id']}/preview?local=true")
    assert resp.status_code == 200
    assert resp.headers["X-OmniVoice-Preview-Source"] == "local"
    assert resp.content == local_wav


def test_preview_state_is_three_states(client, sandbox, monkeypatch):
    item, key = _first_archetype()

    monkeypatch.setattr("api.routers.archetypes._no_voice_model_downloaded", lambda: True)
    body = client.get(f"/archetypes/{item['id']}/preview/state").json()
    assert body == {
        "source": "no_model",
        "message": "You're offline and no voice model is downloaded yet — download one from the engine's Weights list in Model Catalogue.",
    }

    monkeypatch.setattr("api.routers.archetypes._no_voice_model_downloaded", lambda: False)
    body = client.get(f"/archetypes/{item['id']}/preview/state").json()
    assert body == {
        "source": "rendering",
        "message": "Rendering this preview on your machine — it may take a moment.",
    }

    gallery.preview_path(key).write_bytes(_mp3(b"G"))
    body = client.get(f"/archetypes/{item['id']}/preview/state").json()
    assert body["source"] == "gallery"
    assert body["message"].startswith("Pre-rendered preview from the voice gallery")


def test_missing_model_gets_an_action_not_a_log_file(client, sandbox, monkeypatch):
    item, _ = _first_archetype()

    async def _fail(*_a, **_k):
        raise RuntimeError("no checkpoint")

    monkeypatch.setattr("api.routers.archetypes._render_archetype_wav", _fail)
    monkeypatch.setattr("api.routers.archetypes._no_voice_model_downloaded", lambda: True)
    detail = client.get(f"/archetypes/{item['id']}/preview").json()["detail"]
    assert "one from the engine's Weights list in Model Catalogue" in detail
    assert "Logs" not in detail


def test_consent_endpoints_round_trip(client, sandbox, stub, monkeypatch):
    monkeypatch.setattr("api.routers.archetypes.gallery.fetch_featured",
                        _stub_coro(lambda: gallery.status()))
    assert client.get("/archetypes/previews/status").json()["enabled"] is False
    client.put("/archetypes/previews", json={"enabled": True})
    assert gallery.is_enabled() is True
    client.put("/archetypes/previews", json={"enabled": False})
    assert gallery.is_enabled() is False


def _stub_coro(fn):
    async def _inner(*_a, **_k):
        return fn()

    return _inner


# ── Structural guards ────────────────────────────────────────────────────────

def _function(module_path: Path, name: str) -> ast.FunctionDef:
    tree = ast.parse(module_path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    raise AssertionError(f"{name} not found in {module_path}")


def test_use_endpoint_never_reads_the_gallery():
    """A downloaded MP3 must never become a cloned voice's reference audio.

    Structural rather than behavioural on purpose: the failure mode is someone
    "optimising" ``/use`` to reuse the preview it just downloaded, and that
    edit passes every functional test in this file.
    """
    source = _REPO / "backend" / "api" / "routers" / "archetypes.py"
    names = {
        node.value.id
        for node in ast.walk(_function(source, "use_archetype"))
        if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name)
    }
    assert "gallery" not in names


def test_publish_script_forces_the_watermark_and_asserts_detection():
    """The publish path is run by hand, once — CI is the only thing watching it."""
    source = (_REPO / "scripts" / "render_gallery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    forced = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", "") == "mark_synthetic"
        and any(kw.arg == "force" and kw.value.value is True for kw in node.keywords)
    ]
    assert forced, "gallery clips must be marked with force=True at publish time"
    assert "detect_watermark" in source
    assert any(isinstance(node, ast.Raise)
               and getattr(node.exc.func, "id", "") == "AssertionError"
               for node in ast.walk(tree) if isinstance(node, ast.Raise))


@pytest.mark.asyncio
async def test_publish_build_aborts_when_watermark_detection_fails(tmp_path, monkeypatch):
    """Prove the publish assertion executes, not merely that its source exists."""
    spec = importlib.util.spec_from_file_location(
        "render_gallery_under_test", _REPO / "scripts" / "render_gallery.py")
    assert spec and spec.loader
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    from api.routers import archetypes as router_mod
    from api.routers import generation
    from services import watermark

    class Audio:
        shape = (1, 16000)

    async def _render(_archetype, path):
        path.write_bytes(b"wav")

    async def _file_step(_src, dst):
        dst.write_bytes(b"audio")

    monkeypatch.setattr(router_mod, "_render_archetype_wav", _render)
    monkeypatch.setattr(publish, "_load", lambda _path: (Audio(), 16000))
    monkeypatch.setattr(publish, "_encode_mp3", _file_step)
    monkeypatch.setattr(publish, "_decode_wav", _file_step)
    monkeypatch.setattr(watermark, "mark_synthetic", lambda audio, _sr, **_kw: audio)
    monkeypatch.setattr(watermark, "detect_watermark", lambda _audio, _sr: {
        "is_watermarked": False, "confidence": 0.0,
    })
    monkeypatch.setattr(generation, "_safe_torchaudio_save",
                        lambda path, _audio, _sr: Path(path).write_bytes(b"marked"))

    out = tmp_path / "previews"
    out.mkdir()
    with pytest.raises(AssertionError, match="watermark did not survive"):
        await publish._build_one({"id": "x"}, KEY_A, tmp_path, out)
    assert not (out / f"{KEY_A}.mp3").exists()


def test_publish_script_and_client_agree_on_the_schema():
    source = (_REPO / "scripts" / "render_gallery.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    schema = next(
        node.value.value for node in ast.walk(tree)
        if isinstance(node, ast.Assign)
        and getattr(node.targets[0], "id", "") == "SCHEMA_VERSION"
    )
    assert schema == gallery.SCHEMA_VERSION


def test_featured_tarball_is_byte_deterministic(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "render_gallery_determinism", _REPO / "scripts" / "render_gallery.py"
    )
    assert spec and spec.loader
    publish = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publish)
    previews = tmp_path / "previews"
    previews.mkdir()
    (previews / "voice.mp3").write_bytes(b"same audio")
    manifest = {"voice": {"featured": True}}

    first = publish._write_featured_tarball(tmp_path, manifest)
    first_bytes = (tmp_path / "featured.tar.gz").read_bytes()
    second = publish._write_featured_tarball(tmp_path, manifest)

    assert (tmp_path / "featured.tar.gz").read_bytes() == first_bytes
    assert second["sha256"] == first["sha256"]
