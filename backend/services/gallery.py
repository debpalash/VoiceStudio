"""Pre-rendered voice previews — the download client for the voice gallery.

A fresh install can hear nothing until the 2.4 GB TTS checkpoint lands
(``config/models.yaml``), because every archetype preview is synthesized on
demand on the GPU. This module fetches previews that were rendered once, ahead
of time, by ``scripts/render_gallery.py`` and published as a release of the
``omnivoice-gallery`` repo, so the voice picker works on first run and stops
burning a cold model load per voice afterwards.

Trust
=====
``manifest.json`` is signed with the minisign key that signed the final Tauri
release feeds and verified against :data:`UPDATER_PUBKEY`, which this module
carries verbatim (``tests/test_gallery_previews.py`` checks it decodes as a
valid minisign key). Signature verification is the *only* thing
that makes the per-file SHA-256 digests meaningful, so a manifest that fails it
is discarded outright (including a manifest already on disk: it is re-verified
on every load, not trusted because it was trusted once). No new key, no new
infrastructure, no second trust root.

Consent — the gallery is OPT-IN
===============================
Downloading previews is a new outbound call, and CLAUDE.md's local-first
guarantee is that nothing leaves the machine without an explicit yes. "It fails
silently offline and can be turned off" is not consent, so there is **no
on-install background fetch**: :func:`is_enabled` is false until the user turns
the gallery on in Settings, and every network entry point in this module is a
no-op while it is off. Turning it on is the yes, and it is what schedules the
featured-set download. With the gallery off — or unreachable — previews render
locally exactly as they always have, which is the whole app remaining functional
with everything declined.

Fixed reference renderings
==========================
The preview key is ``sha256(instruct|language)[:16]`` (``archetypes.py``), which
is derived from the archetype *definition* and says nothing about which engine
produced the audio. Gallery files are therefore a **fixed reference rendering**
of each voice — the engine that rendered them is recorded in the manifest
(``engine`` / ``engine_version``) and surfaced in Settings, not encoded in the
key. Because of that they live in their own directory and never mix with the
user's local renders under ``OUTPUTS_DIR/archetype_previews``, which share the
same key and follow whichever engine is active. On collision the gallery wins:
it is the rendering we can prove the provenance of.

They are also, deliberately, never reference audio. ``/archetypes/{id}/use``
renders locally, always — a downloaded MP3 must not become the sample a user's
cloned voice is built from.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import logging
import os
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any, Optional

from core.config import DATA_DIR
from core.path_security import UnsafePath, resolve_within, safe_filename
from worker.clock import resolve as _now

logger = logging.getLogger("omnivoice.preview_gallery")

#: Manifest schema this client understands. A manifest declaring anything else
#: is ignored rather than guessed at — an old build must not act on a layout it
#: was not written against, and the signature proves nothing about semantics.
SCHEMA_VERSION = 1

#: Minisign public key of the release signing key the final Tauri builds' updater
#: carried. The Tauri shell and its config are gone, so this constant is now the
#: only copy; changing it orphans every manifest signed with the old key.
UPDATER_PUBKEY = (
    "dW50cnVzdGVkIGNvbW1lbnQ6IG1pbmlzaWduIHB1YmxpYyBrZXk6IDhFMDQ1QkZCQ0I4RDlCQkYKUl"
    "dTL200M0wrMXNFamdPSGF3VkUzVjBRY1FFOE0yTkxSMVZKNUowL2wyZEw2OG1TWXNLMDlSeTQK"
)

_DEFAULT_BASE_URL = (
    "https://github.com/debpalash/omnivoice-gallery/releases/latest/download"
)

_MANIFEST_NAME = "manifest.json"
_SIGNATURE_NAME = "manifest.json.minisig"
_FEATURED_NAME = "featured.tar.gz"

#: Once a day, per the plan's "updates refresh silently" — not per launch.
UPDATE_INTERVAL_S = 24 * 3600

# Every response is read under a hard byte cap: the far end is trusted only
# after a signature check, and the signature check itself needs a bounded read
# to happen at all. Sized off the real artifacts (1126 entries ≈ 300 kB of
# manifest; a 64 kbps mono preview of a sample script ≈ 100 kB) with room to
# grow, so a hostile or broken endpoint cannot fill the user's disk.
_MAX_MANIFEST_BYTES = 8 << 20
_MAX_SIGNATURE_BYTES = 4 << 10
_MAX_PREVIEW_BYTES = 4 << 20
_MAX_FEATURED_BYTES = 64 << 20

_KEY_RE = re.compile(r"^[0-9a-f]{16}$")
_MEMBER_RE = re.compile(r"^(?:\./)?(?:previews/)?([0-9a-f]{16})\.mp3$")

# Background work (manifest refresh, featured tarball) can afford to wait; an
# on-demand fetch is blocking a user who clicked play, and every second past a
# couple is worse than just rendering the preview locally.
_HTTP_TIMEOUT_S = 30.0
ON_DEMAND_TIMEOUT_S = 8.0


class GalleryError(RuntimeError):
    """The gallery answered, and what it said cannot be trusted or used."""


# ── Layout ───────────────────────────────────────────────────────────────────

def gallery_root() -> Path:
    """Directory holding the verified manifest, its signature, and the MP3s."""
    return Path(DATA_DIR) / "voice_gallery_previews"


def _previews_dir() -> Path:
    return gallery_root() / "previews"


def preview_path(key: str) -> Path:
    """Filesystem path for a preview key, or raise if the key is not a key.

    Keys arrive from a manifest and from tar member names — both remote — so
    they are validated as bare 16-hex before they are allowed near a path, and
    then contained under the previews directory anyway.
    """
    if not isinstance(key, str) or not _KEY_RE.match(key):
        raise UnsafePath("preview key must be 16 lowercase hex characters")
    root = _previews_dir()
    root.mkdir(parents=True, exist_ok=True)
    return resolve_within(root, safe_filename(f"{key}.mp3"))


def cached_preview(key: str) -> Optional[Path]:
    """The on-disk gallery preview for *key*, or ``None``.

    Never raises: this sits on the preview request path, where an unusable
    gallery must degrade to a local render rather than fail the request.
    """
    try:
        path = preview_path(key)
    except (UnsafePath, OSError):
        return None
    try:
        return path if path.is_file() and path.stat().st_size > 0 else None
    except OSError:
        return None


# ── State ────────────────────────────────────────────────────────────────────

def _state_path() -> Path:
    return gallery_root() / "state.json"


def load_state() -> dict:
    """Persisted client state: consent, last check, ETag, last error."""
    try:
        raw = _state_path().read_text(encoding="utf-8")
        state = json.loads(raw)
        if isinstance(state, dict) and state.get("schema") == SCHEMA_VERSION:
            return state
    except (OSError, ValueError):
        pass
    return {"schema": SCHEMA_VERSION, "enabled": False}


def _save_state(state: dict) -> None:
    state["schema"] = SCHEMA_VERSION
    _atomic_write(_state_path(), json.dumps(state, indent=2).encode("utf-8"))


def is_enabled() -> bool:
    """True once the user has said yes to downloading previews."""
    return bool(load_state().get("enabled"))


def set_enabled(enabled: bool) -> dict:
    """Record the user's consent decision. Returns the new status."""
    state = load_state()
    state["enabled"] = bool(enabled)
    _save_state(state)
    return status()


# ── Signature verification ───────────────────────────────────────────────────

def _decode_minisign_pubkey(pubkey_b64: str) -> tuple[bytes, bytes, bytes]:
    """Return ``(algorithm, key_id, raw_key)`` from a Tauri-style pubkey.

    Tauri stores the base64 of the whole two-line minisign ``.pub`` *file*, so
    unwrap that first and decode the payload line.
    """
    try:
        text = base64.b64decode(pubkey_b64.encode("ascii"), validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as exc:
        raise GalleryError("updater public key is not decodable") from exc
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]
    if not lines:
        raise GalleryError("updater public key is empty")
    try:
        raw = base64.b64decode(lines[-1].encode("ascii"), validate=True)
    except ValueError as exc:
        raise GalleryError("updater public key payload is not base64") from exc
    if len(raw) != 42:
        raise GalleryError("updater public key has the wrong length")
    return raw[:2], raw[2:10], raw[10:]


def _parse_minisig(signature: str) -> tuple[bytes, bytes, bytes, bytes, bytes]:
    """Parse a minisign signature file.

    Returns ``(algorithm, key_id, signature, trusted_comment, global_signature)``.
    Accepts the raw file text and the base64-of-the-file form Tauri publishes in
    ``latest.json``, because both spellings of "the sig for this artifact" exist
    in this project already.
    """
    text = signature.strip()
    if "untrusted comment:" not in text:
        try:
            text = base64.b64decode(text.encode("ascii"), validate=True).decode("utf-8")
        except (ValueError, UnicodeDecodeError) as exc:
            raise GalleryError("signature is neither minisign text nor base64") from exc
    lines = [ln.rstrip("\r") for ln in text.strip().splitlines()]
    payload = [ln for ln in lines if ln and not ln.startswith("untrusted comment:")]
    trusted = ""
    body: list[str] = []
    for line in payload:
        if line.startswith("trusted comment:"):
            trusted = line[len("trusted comment:"):].lstrip()
            continue
        body.append(line.strip())
    if len(body) < 1:
        raise GalleryError("signature file carries no signature line")
    try:
        raw = base64.b64decode(body[0].encode("ascii"), validate=True)
        global_sig = base64.b64decode(body[1].encode("ascii"), validate=True) if len(body) > 1 else b""
    except ValueError as exc:
        raise GalleryError("signature payload is not base64") from exc
    if len(raw) != 74:
        raise GalleryError("signature has the wrong length")
    return raw[:2], raw[2:10], raw[10:], trusted.encode("utf-8"), global_sig


def verify_manifest(raw: bytes, signature: str, *, pubkey: Optional[str] = None) -> dict:
    """Verify *signature* over *raw* and return the parsed manifest.

    Raises :class:`GalleryError` on anything short of a full verification —
    wrong key id, wrong algorithm, bad signature, unparsable JSON, unknown
    schema. Callers treat that as "there is no gallery", never as a warning.

    *pubkey* is resolved at call time, not bound as a default: a default
    argument would freeze the module constant at import and make the trust root
    un-substitutable — including for the tests that prove rejection works.
    """
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    key_algo, key_id, raw_key = _decode_minisign_pubkey(pubkey or UPDATER_PUBKEY)
    sig_algo, sig_key_id, sig, trusted_comment, global_sig = _parse_minisig(signature)
    if sig_key_id != key_id:
        raise GalleryError("signature was made by a different key")
    if sig_algo not in (b"Ed", b"ED"):
        raise GalleryError("unsupported signature algorithm")
    if key_algo == b"ED" and sig_algo != b"ED":
        raise GalleryError("signature algorithm is weaker than the key allows")

    # minisign's two algorithms differ only in what is signed: "Ed" signs the
    # content, "ED" signs its BLAKE2b-512 digest (so a huge artifact needn't be
    # buffered by the signer). The key declares the maximum; the signature
    # declares which was used.
    signed = hashlib.blake2b(raw, digest_size=64).digest() if sig_algo == b"ED" else raw
    key = Ed25519PublicKey.from_public_bytes(raw_key)
    try:
        key.verify(sig, signed)
    except InvalidSignature as exc:
        raise GalleryError("manifest signature does not verify") from exc
    if global_sig:
        # The trusted comment is only trustworthy because of this second
        # signature over signature||comment; skipping it is how minisign
        # implementations end up honouring an attacker-chosen comment.
        try:
            key.verify(global_sig, sig + trusted_comment)
        except InvalidSignature as exc:
            raise GalleryError("trusted comment signature does not verify") from exc

    try:
        manifest = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeDecodeError) as exc:
        raise GalleryError("manifest is not valid JSON") from exc
    if not isinstance(manifest, dict) or manifest.get("schema") != SCHEMA_VERSION:
        raise GalleryError("manifest schema is not supported by this build")
    previews = manifest.get("previews")
    if not isinstance(previews, dict):
        raise GalleryError("manifest carries no previews table")
    for key_name, entry in previews.items():
        if not _KEY_RE.match(str(key_name)) or not isinstance(entry, dict):
            raise GalleryError("manifest contains a malformed preview key")
        if not _is_sha256(entry.get("sha256")) or not isinstance(entry.get("bytes"), int):
            raise GalleryError("manifest contains a malformed preview entry")
    return manifest


def _is_sha256(value: Any) -> bool:
    return isinstance(value, str) and len(value) == 64 and all(
        c in "0123456789abcdef" for c in value
    )


# ── Manifest on disk ─────────────────────────────────────────────────────────

def load_manifest() -> Optional[dict]:
    """The stored manifest, re-verified against the pubkey. ``None`` if absent.

    Re-verifying on every load (rather than trusting the file because it was
    verified when written) means tampering with ``omnivoice_data`` after the
    fact buys nothing, and costs one Ed25519 check per call.
    """
    root = gallery_root()
    try:
        raw = (root / _MANIFEST_NAME).read_bytes()
        signature = (root / _SIGNATURE_NAME).read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    try:
        return verify_manifest(raw, signature)
    except GalleryError as exc:
        logger.warning("Stored gallery manifest rejected (%s) — ignoring it", exc)
        return None


def _store_manifest(raw: bytes, signature: str) -> None:
    root = gallery_root()
    root.mkdir(parents=True, exist_ok=True)
    _atomic_write(root / _MANIFEST_NAME, raw)
    _atomic_write(root / _SIGNATURE_NAME, signature.encode("utf-8"))


# ── HTTP ─────────────────────────────────────────────────────────────────────

def base_url() -> str:
    """Where previews are published. Overridable for self-hosting and tests."""
    return (os.environ.get("OMNIVOICE_GALLERY_URL") or _DEFAULT_BASE_URL).rstrip("/")


def _client(client=None):
    """The shared httpx client, unless a caller (or a test) supplied one."""
    if client is not None:
        return client
    from api.http_client import get_http_client

    return get_http_client()


async def _fetch(client, url: str, limit: int, headers: Optional[dict] = None,
                 timeout: float = _HTTP_TIMEOUT_S):
    """GET *url*, streaming under a hard byte cap.

    Returns ``(status_code, headers, body)``; body is ``b""`` for 304. Raises
    :class:`GalleryError` when the response exceeds *limit* — a
    Content-Length-free chunked response would otherwise be unbounded.
    """
    import httpx

    async with client.stream(
        "GET", url, headers=headers or {}, timeout=timeout,
        follow_redirects=True,
    ) as response:
        if response.status_code == 304:
            return 304, response.headers, b""
        if response.status_code != 200:
            raise GalleryError(f"gallery returned HTTP {response.status_code}")
        chunks: list[bytes] = []
        total = 0
        try:
            async for chunk in response.aiter_bytes():
                total += len(chunk)
                if total > limit:
                    raise GalleryError("gallery response exceeded its size cap")
                chunks.append(chunk)
        except httpx.HTTPError as exc:
            raise GalleryError(f"gallery transfer failed: {type(exc).__name__}") from exc
        return 200, response.headers, b"".join(chunks)


def _quiet(exc: BaseException) -> None:
    """Log a network-shaped failure without surfacing it.

    Offline is the expected state, not an error: the caller falls back to a
    local render and the user is told nothing.
    """
    logger.debug("Voice gallery unreachable (%s: %s)", type(exc).__name__, exc)


# ── Update check ─────────────────────────────────────────────────────────────

async def check_for_updates(
    *, force: bool = False, client=None, now: Optional[float] = None
) -> dict:
    """Refresh the manifest at most once a day and re-fetch changed previews.

    Only previews **already cached** are re-fetched: the preview key is derived
    from the archetype definition, so a re-render keeps its key and changes only
    its bytes, which makes the per-file SHA-256 the thing the updater diffs on.
    Bulk-fetching every changed key would turn a silent background refresh into
    a 1126-file download nobody asked for.
    """
    state = load_state()
    if not state.get("enabled"):
        return status(now=now)
    ts = _now(now)
    # "Never checked" is `last_checked` absent, not zero: `or 0` would make an
    # injected clock near the epoch look like a check that just happened, and
    # silently skip the very first refresh.
    last_checked = state.get("last_checked")
    if not force and last_checked is not None and ts - float(last_checked) < UPDATE_INTERVAL_S:
        return status(now=now)

    http_client = _client(client)
    headers = {}
    etag = state.get("etag")
    if etag and load_manifest() is not None:
        headers["If-None-Match"] = etag
    try:
        code, resp_headers, raw = await _fetch(
            http_client, f"{base_url()}/{_MANIFEST_NAME}", _MAX_MANIFEST_BYTES, headers,
        )
        if code == 304:
            state["last_checked"] = ts
            state.pop("last_error", None)
            _save_state(state)
            return status(now=now)
        _, _, signature_raw = await _fetch(
            http_client, f"{base_url()}/{_SIGNATURE_NAME}", _MAX_SIGNATURE_BYTES,
        )
        manifest = verify_manifest(raw, signature_raw.decode("utf-8", "replace"))
    except GalleryError as exc:
        # A signature failure is not a transient network hiccup — it is the one
        # state the user should be able to see, so record it. The app still
        # works: previews render locally.
        if "HTTP 404" in str(exc):
            _quiet(exc)
        else:
            logger.warning("Voice gallery update rejected: %s", exc)
        state["last_checked"] = ts
        state["last_error"] = str(exc)
        _save_state(state)
        return status(now=now)
    except Exception as exc:  # offline, DNS, TLS, timeout — all expected
        _quiet(exc)
        state["last_checked"] = ts
        _save_state(state)
        return status(now=now)

    previous = load_manifest() or {}
    _store_manifest(raw, signature_raw.decode("utf-8", "replace"))
    state["last_checked"] = ts
    state["etag"] = resp_headers.get("etag") or state.get("etag")
    state.pop("last_error", None)
    _save_state(state)

    old = previous.get("previews") or {}
    refreshed = 0
    for key, entry in (manifest.get("previews") or {}).items():
        if cached_preview(key) is None:
            continue
        if (old.get(key) or {}).get("sha256") == entry.get("sha256"):
            continue
        if await _download_preview(http_client, key, entry) is not None:
            refreshed += 1
    out = status(now=now)
    out["refreshed"] = refreshed
    return out


_refresh_task: Optional["asyncio.Task"] = None


def maybe_refresh_in_background() -> None:
    """Kick a throttled update check without making the caller wait for it.

    The 24 h throttle only means something if something asks, and nothing else
    in the app polls — there is no background scheduler to hang this on. Serving
    a preview is the honest trigger: it is the moment previews matter, and the
    check is a no-op on all but the first request of the day. The task handle is
    held module-level because a bare ``create_task`` result can be garbage
    collected mid-flight.
    """
    global _refresh_task
    if not is_enabled() or (_refresh_task is not None and not _refresh_task.done()):
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:  # sync context (CLI, tests) — nothing to schedule onto
        return
    _refresh_task = loop.create_task(check_for_updates())


# ── Per-file fetch ───────────────────────────────────────────────────────────

async def _download_preview(client, key: str, entry: dict,
                            timeout: float = _HTTP_TIMEOUT_S) -> Optional[Path]:
    """Fetch one preview and commit it only if it matches the signed digest."""
    try:
        path = preview_path(key)
        filename = safe_filename(entry.get("filename") or f"{key}.mp3")
        limit = min(_MAX_PREVIEW_BYTES, max(int(entry.get("bytes") or 0), 1))
        _, _, body = await _fetch(client, f"{base_url()}/previews/{filename}", limit,
                                  timeout=timeout)
        _commit_preview(path, body, entry)
        return path
    except (GalleryError, UnsafePath, OSError, ValueError) as exc:
        logger.debug("Gallery preview %s not fetched (%s)", key[:8], exc)
        return None
    except Exception as exc:
        _quiet(exc)
        return None


def _commit_preview(path: Path, body: bytes, entry: dict) -> None:
    """Write *body* to *path* iff it is exactly the bytes the manifest signed."""
    if len(body) != int(entry["bytes"]):
        raise GalleryError("preview length does not match the manifest")
    if hashlib.sha256(body).hexdigest() != entry["sha256"]:
        raise GalleryError("preview digest does not match the manifest")
    _atomic_write(path, body)


async def fetch_preview(
    key: str, *, client=None, now: Optional[float] = None
) -> Optional[Path]:
    """Fetch a single preview on demand. ``None`` whenever that can't happen.

    Silent by contract — offline, disabled, and unknown-key all look the same to
    the caller, which then renders locally.
    """
    if not is_enabled():
        return None
    cached = cached_preview(key)
    if cached is not None:
        return cached
    manifest = load_manifest()
    if manifest is None:
        await check_for_updates(client=client, now=now)
        manifest = load_manifest()
        if manifest is None:
            return None
    entry = (manifest.get("previews") or {}).get(key)
    if not isinstance(entry, dict):
        return None
    http_client = _client(client)
    return await _download_preview(http_client, key, entry, ON_DEMAND_TIMEOUT_S)


# ── Featured set ─────────────────────────────────────────────────────────────

async def fetch_featured(
    *, client=None, now: Optional[float] = None, force: bool = False
) -> dict:
    """Download the 51 featured previews as one tarball.

    One request instead of 51: the featured set is what the voice picker opens
    on, so it is the only bulk fetch this client performs — and it happens only
    after the user has enabled the gallery.
    """
    if not is_enabled():
        return status(now=now)
    manifest = load_manifest()
    if manifest is None or force:
        await check_for_updates(force=True, client=client, now=now)
        manifest = load_manifest()
    if manifest is None:
        return status(now=now)
    featured = manifest.get("featured")
    if not isinstance(featured, dict) or not _is_sha256(featured.get("sha256")):
        return status(now=now)

    http_client = _client(client)
    try:
        limit = min(_MAX_FEATURED_BYTES, max(int(featured.get("bytes") or 0), 1))
        name = safe_filename(featured.get("filename") or _FEATURED_NAME)
        _, _, body = await _fetch(http_client, f"{base_url()}/{name}", limit)
        if hashlib.sha256(body).hexdigest() != featured["sha256"]:
            raise GalleryError("featured tarball digest does not match the manifest")
        extracted = _extract_featured(body, manifest)
    except GalleryError as exc:
        if "HTTP 404" in str(exc):
            _quiet(exc)
        else:
            logger.warning("Featured preview set rejected: %s", exc)
        return status(now=now)
    except Exception as exc:
        _quiet(exc)
        return status(now=now)
    out = status(now=now)
    out["fetched"] = extracted
    return out


def _extract_featured(body: bytes, manifest: dict) -> int:
    """Unpack the featured tarball member-by-member, verifying each file.

    Nothing is handed to ``TarFile.extract``: member names are matched against
    the key grammar, only regular files are read, and every member's bytes must
    match the digest the signed manifest carries for that key. A tarball is a
    filesystem-write primitive, and this one arrives over the network.
    """
    previews = manifest.get("previews") or {}
    written = 0
    with tempfile.TemporaryDirectory() as tmp:
        archive = Path(tmp) / "featured.tar.gz"
        archive.write_bytes(body)
        with tarfile.open(archive, "r:gz") as tar:
            for member in tar:
                match = _MEMBER_RE.match(member.name)
                if match is None or not member.isfile():
                    logger.debug("Skipping gallery tar member %r", member.name[:64])
                    continue
                key = match.group(1)
                entry = previews.get(key)
                if not isinstance(entry, dict) or member.size != int(entry.get("bytes") or -1):
                    continue
                handle = tar.extractfile(member)
                if handle is None:
                    continue
                data = handle.read(_MAX_PREVIEW_BYTES + 1)
                try:
                    _commit_preview(preview_path(key), data, entry)
                except (GalleryError, UnsafePath, OSError):
                    continue
                written += 1
    return written


# ── Status ───────────────────────────────────────────────────────────────────

def status(*, now: Optional[float] = None) -> dict:
    """What Settings shows: consent, coverage, freshness, and provenance.

    Counts are reported as "featured set cached" / "N extra voices", never as a
    raw fraction of 1126 — a number nobody can act on.
    """
    state = load_state()
    manifest = load_manifest()
    previews = (manifest or {}).get("previews") or {}
    featured_keys = [k for k, e in previews.items() if isinstance(e, dict) and e.get("featured")]
    cached_keys = _cached_keys()
    featured_cached = sum(1 for k in featured_keys if k in cached_keys)
    return {
        "enabled": bool(state.get("enabled")),
        "available": manifest is not None,
        "featured_total": len(featured_keys),
        "featured_cached": featured_cached,
        "cached": len(cached_keys),
        "last_checked": state.get("last_checked"),
        "last_error": state.get("last_error"),
        "engine": (manifest or {}).get("engine"),
        "engine_version": (manifest or {}).get("engine_version"),
        "generated_at": (manifest or {}).get("generated_at"),
        "checked_seconds_ago": (
            max(0.0, _now(now) - float(state["last_checked"]))
            if state.get("last_checked") else None
        ),
    }


def _cached_keys() -> set[str]:
    try:
        return {
            p.stem for p in _previews_dir().iterdir()
            if p.suffix == ".mp3" and _KEY_RE.match(p.stem)
        }
    except OSError:
        return set()


# ── Utilities ────────────────────────────────────────────────────────────────

def _atomic_write(path: Path, data: bytes) -> None:
    """Write via a sibling temp file + replace so no reader sees a half file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".gallery-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        with __import__("contextlib").suppress(OSError):
            os.unlink(tmp)
        raise
