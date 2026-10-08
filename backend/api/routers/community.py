"""Community gallery (marketplace) API.

Loads designed *presets* and recorded *voices* from configured content repos
(default: ``debpalash/omnivoice-gallery``) over the jsDelivr CDN, caches them
locally, validates strictly, and exposes them to the gallery.

Design / safety
===============
* **Local-first.** The network is touched only when the user opens the
  marketplace or hits refresh. Everything is cached under
  ``DATA_DIR/gallery_cache`` and served offline from cache; the app's built-in
  generated archetypes need no network, so the gallery is never empty.
* **Data only, never code.** Remote content is JSON + audio. Presets are
  validated against the engine's taxonomy and *dropped* if invalid (so a bad
  community entry can't reproduce issue #89). Audio URLs are restricted to an
  allow-list of hosts (jsDelivr / GitHub) — no arbitrary SSRF target.
* **Reuse.** "Use a preset" renders through the same path as archetypes
  (one TTS code path).
"""
from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import time
import uuid
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin, urlparse

from fastapi import APIRouter, HTTPException, Query, Depends
from fastapi.responses import FileResponse

from core import archetypes
from core.audio_validation import is_playable_wav, resolve_regular_file
from core.config import DATA_DIR, VOICES_DIR
from core.browser_guard import reject_cross_site_get

logger = logging.getLogger("omnivoice.community")
router = APIRouter()

_CACHE_DIR = Path(DATA_DIR) / "gallery_cache"
_DEFAULT_SOURCES = ["debpalash/omnivoice-gallery"]
_ALLOWED_AUDIO_HOSTS = {
    "cdn.jsdelivr.net", "github.com", "raw.githubusercontent.com",
    "objects.githubusercontent.com", "release-assets.githubusercontent.com",
}
_ALLOWED_MANIFEST_HOSTS = {"cdn.jsdelivr.net"}
_VALID_TOKENS = set(archetypes._VD._INSTRUCT_ALL_VALID)
_USE_CASE_IDS = {c["id"] for c in archetypes.USE_CASES}
_SOURCE_RE = re.compile(
    r"^[A-Za-z0-9._-]{1,100}/[A-Za-z0-9._-]{1,100}$",
)  # owner/repo only
_ITEM_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

# A gallery open may touch this loader several times (grid, preview, use). Keep
# a successful response for six hours, then revalidate it once. On a network
# failure the readable stale copy remains usable and its check time advances,
# preventing every offline gallery open from waiting through the same timeout.
_MANIFEST_MAX_AGE_S = 6 * 60 * 60
_MAX_MANIFEST_BYTES = 4 << 20
_MAX_SAMPLE_SCRIPT_CHARS = 2_000
_MAX_REF_TEXT_CHARS = 4_000

# Community voice submissions are documented as short clean WAV clips. The cap
# comfortably covers 15 s of uncompressed 96 kHz stereo PCM while preventing a
# remote manifest from turning Preview into an unbounded disk/memory download.
_MAX_VOICE_AUDIO_BYTES = 32 << 20

_ATTR_NAMES = (
    "Gender", "Age", "Pitch", "Style", "EnglishAccent", "ChineseDialect",
)


# ── Config: which content repos to load ───────────────────────────────────────
def configured_sources() -> list[str]:
    """Gallery sources, in priority order. Env var > config file > default."""
    env = os.environ.get("OMNIVOICE_GALLERY_SOURCES")
    if env:
        sources = [s.strip() for s in env.split(",")]
        valid = [s for s in sources if _SOURCE_RE.fullmatch(s)]
        return valid or list(_DEFAULT_SOURCES)
    cfg = Path(DATA_DIR) / "gallery_sources.json"
    if cfg.exists():
        try:
            data = json.loads(cfg.read_text(encoding="utf-8"))
            srcs = data.get("sources")
            if isinstance(srcs, list) and srcs:
                valid = [s for s in srcs if isinstance(s, str) and _SOURCE_RE.fullmatch(s)]
                if valid:
                    return valid
        except Exception:
            logger.warning("gallery_sources.json unreadable; using default")
    return list(_DEFAULT_SOURCES)


def _manifest_url(source: str) -> str:
    return f"https://cdn.jsdelivr.net/gh/{source}@main/manifest.json"


def _cache_path(source: str) -> Path:
    return _CACHE_DIR / source.replace("/", "__") / "manifest.json"


def _safe_audio_url(url: str) -> bool:
    try:
        u = urlparse(url or "")
        return u.scheme == "https" and (u.hostname in _ALLOWED_AUDIO_HOSTS)
    except Exception:
        return False


def _safe_manifest_url(url: str) -> bool:
    try:
        parsed = urlparse(url or "")
        return parsed.scheme == "https" and parsed.hostname in _ALLOWED_MANIFEST_HOSTS
    except Exception:
        return False


def normalize_preset_instruct(instruct: str) -> Optional[tuple[str, dict]]:
    """Normalize one validator-safe tag per design category.

    Membership in the vocabulary is not enough: ``male, female`` contains two
    individually valid tokens but the engine rejects the pair as conflicting.
    Build the frontend's full ``vd_states`` shape at this trust boundary too,
    so Magic Wand never inherits stale sliders from the previous voice.
    """
    attrs = {name: "Auto" for name in _ATTR_NAMES}
    normalized: list[str] = []
    seen_categories: set[int] = set()
    for raw in re.split("[," + chr(0xFF0C) + "]", str(instruct or "")):
        token = raw.strip().lower()
        if not token or token not in _VALID_TOKENS:
            return None
        category = archetypes._VD._instruct_category_index(token)
        if category < 0 or category in seen_categories:
            return None
        seen_categories.add(category)

        # The picker represents the universal gender/age/pitch/style axes in
        # English even for Chinese speech; dialect remains Chinese-only.
        canonical = archetypes._VD._INSTRUCT_ZH_TO_EN.get(token, token)
        attrs[_ATTR_NAMES[category]] = canonical
        normalized.append(canonical)

    if not normalized:
        return None
    # Accent and Chinese dialect are separate taxonomy buckets but the engine
    # deliberately forbids mixing them in a single design.
    if 4 in seen_categories and 5 in seen_categories:
        return None
    return ", ".join(normalized), attrs


def is_valid_instruct(instruct: str) -> bool:
    return normalize_preset_instruct(instruct) is not None


def validate_item(raw: dict) -> Optional[dict]:
    """Return a normalized item, or None if it must be dropped."""
    if not isinstance(raw, dict):
        return None
    it = dict(raw)
    if it.get("type") not in ("preset", "voice"):
        return None
    if not isinstance(it.get("id"), str) or not _ITEM_ID_RE.fullmatch(it["id"]):
        return None
    if not isinstance(it.get("name"), str) or not it["name"].strip():
        return None
    it["name"] = it["name"].strip()[:80]
    if it.get("use_case") not in _USE_CASE_IDS:
        return None
    raw_facets = it.get("facets")
    if not isinstance(raw_facets, dict):
        raw_facets = {}
    language = it.get("language")
    if not isinstance(language, str) or not language.strip():
        language = raw_facets.get("lang", "English")
    it["language"] = language.strip() if isinstance(language, str) and language.strip() else "English"

    facets = dict(raw_facets)
    if it["type"] == "preset":
        normalized = normalize_preset_instruct(it.get("instruct", ""))
        if normalized is None:
            return None  # unknown/conflicting tokens would crash synthesis
        it["instruct"], it["attrs"] = normalized
        attrs = it["attrs"]
        facets.update({
            "gender": None if attrs["Gender"] == "Auto" else attrs["Gender"],
            "age": None if attrs["Age"] == "Auto" else attrs["Age"],
            "pitch": None if attrs["Pitch"] == "Auto" else attrs["Pitch"],
            "accent": None if attrs["EnglishAccent"] == "Auto" else attrs["EnglishAccent"],
            "whisper": attrs["Style"] == "whisper",
            "lang": it["language"],
        })
        sample_script = it.get("sample_script")
        it["sample_script"] = (
            sample_script.strip()[:_MAX_SAMPLE_SCRIPT_CHARS]
            if isinstance(sample_script, str) else ""
        )
    else:
        audio = it.get("audio")
        if not isinstance(audio, dict) or not _safe_audio_url(audio.get("url", "")):
            return None
        expected = audio.get("sha256")
        if expected is not None:
            expected = str(expected).lower()
            if not _SHA256_RE.fullmatch(expected):
                return None
            audio = {**audio, "sha256": expected}
        ref_text = audio.get("ref_text")
        audio = {
            **audio,
            "ref_text": (
                ref_text.strip()[:_MAX_REF_TEXT_CHARS]
                if isinstance(ref_text, str) else ""
            ),
        }
        it["audio"] = audio
        facets.setdefault("gender", None)
        facets.setdefault("age", None)
        facets.setdefault("pitch", None)
        facets.setdefault("accent", None)
        facets.setdefault("whisper", False)
        facets.setdefault("lang", it["language"])
    it["facets"] = facets
    it.setdefault("icon", archetypes._USE_ICON.get(it["use_case"], "Sparkles"))
    it["is_community"] = it.get("source") != "starter"
    it["preview_url"] = f"/community/items/{it['id']}/preview"
    return it


def _merge(manifests: list[tuple[str, Optional[dict]]]) -> tuple[list, list]:
    items, packs, seen = [], [], set()
    for src, m in manifests:
        if not isinstance(m, dict):
            continue
        raw_items = m.get("items")
        for raw in raw_items if isinstance(raw_items, list) else []:
            v = validate_item(raw)
            if v and v["id"] not in seen:
                v["_source_repo"] = src
                seen.add(v["id"])
                items.append(v)
        raw_packs = m.get("packs")
        for p in raw_packs if isinstance(raw_packs, list) else []:
            if isinstance(p, dict):
                packs.append({**p, "_source_repo": src})
    return items, packs


def _read_manifest_cache(cache: Path) -> Optional[dict]:
    try:
        if cache.stat().st_size > _MAX_MANIFEST_BYTES:
            return None
        data = json.loads(cache.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except (OSError, ValueError, TypeError):
        return None


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=f".{path.name}-", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp, path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise


def _fetch_remote_manifest(source: str, *, client=None) -> dict:
    """Fetch one bounded manifest, validating every redirect before request."""
    import httpx

    if not _SOURCE_RE.fullmatch(source or ""):
        raise ValueError("invalid gallery source")
    owned_client = client is None
    http = client or httpx.Client(timeout=15.0, follow_redirects=False)
    current_url = _manifest_url(source)
    payload = bytearray()
    try:
        fetched = False
        for _redirect in range(6):
            if not _safe_manifest_url(current_url):
                raise ValueError("gallery manifest URL is not from an allowed host")
            with http.stream("GET", current_url, follow_redirects=False) as response:
                if response.status_code in (301, 302, 303, 307, 308):
                    location = response.headers.get("location")
                    next_url = urljoin(current_url, location or "")
                    if not location or not _safe_manifest_url(next_url):
                        raise ValueError("gallery manifest redirected to a disallowed host")
                    current_url = next_url
                    continue
                response.raise_for_status()
                length = response.headers.get("content-length")
                if length:
                    try:
                        declared_length = int(length)
                    except ValueError:
                        declared_length = None
                    if declared_length is not None and declared_length > _MAX_MANIFEST_BYTES:
                        raise ValueError("gallery manifest exceeded the size limit")
                for chunk in response.iter_bytes():
                    if not chunk:
                        continue
                    if len(payload) + len(chunk) > _MAX_MANIFEST_BYTES:
                        raise ValueError("gallery manifest exceeded the size limit")
                    payload.extend(chunk)
                fetched = True
                break
        if not fetched:
            raise ValueError("gallery manifest followed too many redirects")
    finally:
        if owned_client:
            http.close()
    if not payload:
        raise ValueError("gallery manifest was empty")
    data = json.loads(payload)
    if not isinstance(data, dict):
        raise ValueError("gallery manifest is not a JSON object")
    return data


def _fetch_manifest(
    source: str, refresh: bool, *, now: Optional[float] = None,
) -> Optional[dict]:
    """Return a fresh manifest, with a throttled stale-cache offline fallback."""
    cache = _cache_path(source)
    cached = _read_manifest_cache(cache)
    checked_at = time.time() if now is None else float(now)
    if not refresh and cached is not None:
        try:
            if checked_at - cache.stat().st_mtime < _MANIFEST_MAX_AGE_S:
                return cached
        except OSError:
            pass  # treat a stat race as stale and try the source once
    try:
        data = _fetch_remote_manifest(source)
        encoded = json.dumps(
            data, ensure_ascii=False, separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > _MAX_MANIFEST_BYTES:
            raise ValueError("gallery manifest exceeded the cache size limit")
        _write_bytes_atomic(cache, encoded)
        # Tests inject their own clock; production's value equals wall time.
        os.utime(cache, (checked_at, checked_at))
        return data
    except Exception as e:  # offline / 404 / bad json
        logger.warning("manifest fetch failed for %s: %s", source, e)
        if cached is not None:
            # This mtime is a last-*check* marker. Advancing it on failure keeps
            # an offline app responsive while guaranteeing another check after
            # the bounded freshness interval.
            with contextlib.suppress(OSError):
                os.utime(cache, (checked_at, checked_at))
            return cached
        return None


def _load(refresh: bool) -> tuple[list[str], list, list, bool]:
    srcs = configured_sources()
    manifests = [(s, _fetch_manifest(s, refresh)) for s in srcs]
    items, packs = _merge(manifests)
    offline = all(m is None for _, m in manifests)
    return srcs, items, packs, offline


# ── Endpoints ─────────────────────────────────────────────────────────────────
@router.get("/community/sources")
def community_sources():
    """The content repos the gallery loads from (default: omnivoice-gallery)."""
    return {"sources": configured_sources()}


@router.get("/community/manifest", dependencies=[Depends(reject_cross_site_get)])
def community_manifest(refresh: bool = Query(False)):
    srcs, items, packs, offline = _load(refresh)
    return {"sources": srcs, "packs": packs, "items": items, "count": len(items), "offline": offline}


@router.get("/community/items", dependencies=[Depends(reject_cross_site_get)])
def community_items(
    use_case: Optional[str] = None,
    gender: Optional[str] = None,
    item_type: Optional[str] = Query(None, alias="type"),
    lang: Optional[str] = None,
    q: Optional[str] = None,
    limit: int = Query(60, ge=1, le=500),
    offset: int = Query(0, ge=0),
    refresh: bool = Query(False),
):
    _, items, _, _ = _load(refresh)

    def keep(it: dict) -> bool:
        f = it.get("facets", {})
        if use_case and it.get("use_case") != use_case:
            return False
        if gender and f.get("gender") != gender:
            return False
        if item_type and it.get("type") != item_type:
            return False
        if lang and it.get("language") != lang:
            return False
        if q and q.lower() not in (it.get("name", "").lower()):
            return False
        return True

    items = [it for it in items if keep(it)]
    return {"total": len(items), "limit": limit, "offset": offset, "items": items[offset:offset + limit]}


@router.get("/community/submit-url")
def community_submit_url(item_type: str = Query("preset", alias="type"), source: Optional[str] = Query(None)):
    """Build the prefilled GitHub submission URL (server-free, local-first)."""
    src = source or configured_sources()[0]
    if not _SOURCE_RE.match(src or ""):
        src = configured_sources()[0]  # ignore a malformed/untrusted source override
    template = "preset-submission.yml" if item_type == "preset" else "voice-submission.yml"
    return {"url": f"https://github.com/{src}/issues/new?template={template}"}


def _find_item(items: list[dict], item_id: str) -> dict:
    if not _ITEM_ID_RE.fullmatch(item_id or ""):
        raise HTTPException(status_code=404, detail="Item not found in the gallery.")
    item = next((it for it in items if it["id"] == item_id), None)
    if item is None:
        raise HTTPException(status_code=404, detail="Item not found in the gallery.")
    return item


def _canonical_archetype(item: dict) -> Optional[dict]:
    """The built-in archetype represented exactly by a marketplace preset."""
    if item.get("type") != "preset":
        return None
    canonical = archetypes.get_archetype(item["id"])
    if canonical is None:
        return None
    if (canonical.get("instruct") != item.get("instruct")
            or canonical.get("language") != item.get("language")):
        return None
    remote_script = (item.get("sample_script") or "").strip()
    if remote_script and remote_script != (canonical.get("sample_script") or "").strip():
        return None
    return canonical


def _preset_preview_path(item: dict) -> Path:
    fingerprint = hashlib.sha256(
        json.dumps({
            "instruct": item.get("instruct"),
            "language": item.get("language"),
            "sample_script": item.get("sample_script"),
        }, sort_keys=True).encode("utf-8")
    ).hexdigest()[:16]
    return _CACHE_DIR / "previews" / f"{item['id']}-{fingerprint}.wav"


def _voice_audio_fingerprint(item: dict) -> str:
    audio = item.get("audio") or {}
    return hashlib.sha256(
        f"{audio.get('url', '')}|{audio.get('sha256', '')}".encode("utf-8")
    ).hexdigest()[:16]


def _voice_audio_path(item: dict) -> Path:
    return _CACHE_DIR / "audio" / f"{item['id']}-{_voice_audio_fingerprint(item)}.wav"


async def _render_preset_atomic(item: dict, out_path: Path) -> Path:
    if is_playable_wav(out_path):
        return out_path
    from api.routers.archetypes import _render_archetype_wav

    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_path.parent), prefix=".preview-", suffix=".wav")
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        await _render_archetype_wav({
            "instruct": item["instruct"],
            "language": item.get("language", "English"),
            "sample_script": (
                (item.get("sample_script") or "").strip()
                or "Hello — this is a preview of this voice."
            ),
        }, tmp)
        if not is_playable_wav(tmp):
            raise RuntimeError("the voice engine produced an invalid preview WAV")
        os.replace(tmp, out_path)
        return out_path
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink()


def _download_voice_audio(item: dict, out_path: Path, *, client=None) -> None:
    """Stream one allow-listed voice clip into an atomic, size-bounded file."""
    audio = item.get("audio") or {}
    url = audio.get("url", "")
    if not _safe_audio_url(url):
        raise HTTPException(status_code=400, detail="Voice audio URL is not from an allowed host.")

    import httpx

    owned_client = client is None
    http = client or httpx.Client(timeout=30.0, follow_redirects=False)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(out_path.parent), prefix=".voice-", suffix=".part")
    total = 0
    digest = hashlib.sha256()
    try:
        with os.fdopen(fd, "wb") as handle:
            current_url = url
            downloaded = False
            for _redirect in range(6):
                with http.stream("GET", current_url, follow_redirects=False) as response:
                    if response.status_code in (301, 302, 303, 307, 308):
                        location = response.headers.get("location")
                        next_url = urljoin(current_url, location or "")
                        if not location or not _safe_audio_url(next_url):
                            raise HTTPException(
                                status_code=502,
                                detail="Community voice audio redirected to a disallowed host.",
                            )
                        current_url = next_url
                        continue
                    response.raise_for_status()
                    length = response.headers.get("content-length")
                    if length:
                        try:
                            if int(length) > _MAX_VOICE_AUDIO_BYTES:
                                raise HTTPException(
                                    status_code=502,
                                    detail="Community voice audio exceeded the download size limit.",
                                )
                        except ValueError:
                            # A non-numeric Content-Length header is the
                            # server's problem, not a reason to refuse the
                            # download — the streamed byte counter below
                            # still enforces the same cap on what actually
                            # arrives.
                            pass
                    for chunk in response.iter_bytes():
                        if not chunk:
                            continue
                        total += len(chunk)
                        if total > _MAX_VOICE_AUDIO_BYTES:
                            raise HTTPException(
                                status_code=502,
                                detail="Community voice audio exceeded the download size limit.",
                            )
                        digest.update(chunk)
                        handle.write(chunk)
                    downloaded = True
                    break
            if not downloaded:
                raise HTTPException(
                    status_code=502,
                    detail="Community voice audio followed too many redirects.",
                )
            if total == 0:
                raise HTTPException(status_code=502, detail="Community voice audio was empty.")
            expected = audio.get("sha256")
            if expected and digest.hexdigest() != expected:
                raise HTTPException(
                    status_code=502,
                    detail="Downloaded voice failed its integrity check.",
                )
            handle.flush()
            os.fsync(handle.fileno())
        if not is_playable_wav(Path(tmp_name)):
            raise HTTPException(
                status_code=502, detail="Community voice audio was not a valid WAV.",
            )
        os.replace(tmp_name, out_path)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise
    finally:
        if owned_client:
            http.close()


def _cached_voice_audio(item: dict) -> Path:
    path = _voice_audio_path(item)
    if is_playable_wav(path):
        return path
    with contextlib.suppress(OSError):
        path.unlink()
    _download_voice_audio(item, path)
    return path


def _copy_atomic(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(
        dir=str(destination.parent), prefix=f".{destination.name}-", suffix=".part",
    )
    try:
        with os.fdopen(fd, "wb") as out, source.open("rb") as src:
            shutil.copyfileobj(src, out)
            out.flush()
            os.fsync(out.fileno())
        os.replace(tmp_name, destination)
    except BaseException:
        with contextlib.suppress(OSError):
            os.unlink(tmp_name)
        raise


@router.get("/community/items/{item_id}/preview", dependencies=[Depends(reject_cross_site_get)])
async def community_preview(
    item_id: str,
    local: bool = Query(False, description="Bypass canonical gallery audio after decode failure"),
):
    """Serve every community preview through the authenticated same-origin API."""
    _, items, _, _ = await asyncio.to_thread(_load, False)
    item = _find_item(items, item_id)

    canonical = _canonical_archetype(item)
    if canonical is not None:
        # Reuse the signed-gallery/local-render fallback and cache owned by the
        # canonical endpoint rather than synthesizing the same preset twice.
        # Delegate in-process: a root-relative HTTP redirect drops supported
        # reverse-proxy path prefixes such as ``https://host/api``.
        from api.routers.archetypes import preview_archetype
        return await preview_archetype(canonical["id"], local=local)

    try:
        if item["type"] == "preset":
            path = await _render_preset_atomic(item, _preset_preview_path(item))
        else:
            path = await asyncio.to_thread(_cached_voice_audio, item)
    except HTTPException:
        raise
    except Exception as exc:
        logger.warning("Community preview unavailable (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=503, detail="This community voice preview is unavailable right now.",
        ) from exc
    return FileResponse(
        path, media_type="audio/wav",
        headers={"Cache-Control": "no-cache", "X-OmniVoice-Preview-Source": "community"},
    )


def _profile_fields(item: dict) -> tuple[str, str, Optional[str], Optional[int]]:
    if item["type"] == "preset":
        return "design", item["instruct"], json.dumps(item["attrs"]), 42
    return "clone", "", None, None


def _community_profile_audio_filename(profile_id: str, item: dict) -> str:
    safe_id = (
        profile_id if re.fullmatch(r"[A-Za-z0-9_-]{1,64}", profile_id or "")
        else hashlib.sha256(str(profile_id).encode("utf-8")).hexdigest()[:16]
    )
    if item["type"] == "voice":
        # The manifest URL/checksum fingerprint makes a changed submission
        # invalidate its already-materialized clone without a schema change.
        return f"{safe_id}-community-{_voice_audio_fingerprint(item)}.wav"
    return f"{safe_id}.wav"


def _stored_profile_audio(ref_audio_path: object) -> Optional[Path]:
    return resolve_regular_file(VOICES_DIR, ref_audio_path)


def _community_audio_is_current(row, item: dict, ref_text: str) -> bool:
    path = _stored_profile_audio(row["ref_audio_path"])
    expected_filename = _community_profile_audio_filename(row["id"], item)
    if row["ref_audio_path"] != expected_filename or not is_playable_wav(path):
        return False
    kind, instruct, _vd_states, seed = _profile_fields(item)
    inputs_match = (
        row["instruct"] == instruct
        and row["language"] == item.get("language", "Auto")
        and row["ref_text"] == ref_text
        and row["seed"] == seed
    )
    if not inputs_match:
        return False
    return True


async def _materialize_item_audio(
    item: dict, profile_id: str, *, publish: bool = True,
) -> tuple[str, Path]:
    """Copy the current manifest audio, optionally staging it for a later CAS."""
    audio_filename = _community_profile_audio_filename(profile_id, item)
    destination = Path(VOICES_DIR) / audio_filename
    audio_path = destination
    if not publish:
        destination.parent.mkdir(parents=True, exist_ok=True)
        audio_path = destination.parent / f".{Path(audio_filename).stem}-{uuid.uuid4().hex}.staged.wav"
    if item["type"] == "preset":
        cached = await _render_preset_atomic(item, _preset_preview_path(item))
    else:
        cached = await asyncio.to_thread(_cached_voice_audio, item)
    await asyncio.to_thread(_copy_atomic, cached, audio_path)
    return audio_filename, audio_path


def _community_personality(item: dict) -> str:
    source = item.get("_source_repo")
    if not isinstance(source, str) or not _SOURCE_RE.fullmatch(source):
        source = _DEFAULT_SOURCES[0]
    return f"community:{source}:{item['id']}"


def _is_materialized_community_row(row, item: dict) -> bool:
    if (
        row["personality"] != _community_personality(item)
        or row["is_locked"] or row["verified_own_voice"]
    ):
        return False
    if item["type"] == "voice":
        safe_id = Path(_community_profile_audio_filename(row["id"], item)).name.split(
            "-community-", 1,
        )[0]
        return bool(
            row["kind"] == "clone"
            and row["seed"] is None
            and not row["vd_states"]
            and row["instruct"] == ""
            and row["language"] == item.get("language", "Auto")
            and row["ref_text"] == (item.get("audio") or {}).get("ref_text", "")
            and re.fullmatch(
                rf"{re.escape(safe_id)}-community-[0-9a-f]{{16}}\.wav",
                row["ref_audio_path"] or "",
            )
        )
    try:
        states = json.loads(row["vd_states"])
    except (TypeError, ValueError):
        return False
    return bool(
        row["kind"] == "design"
        and row["seed"] == 42
        and row["ref_audio_path"] == _community_profile_audio_filename(row["id"], item)
        and row["instruct"] == item["instruct"]
        and row["language"] == item.get("language", "Auto")
        and row["ref_text"] == (item.get("sample_script") or "")
        and states == item["attrs"]
    )


def _existing_community_profile(conn, item: dict, personality: str):
    candidates = conn.execute(
        "SELECT * FROM voice_profiles WHERE personality=? ORDER BY created_at, id",
        (personality,),
    ).fetchall()
    existing = next(
        (row for row in candidates if _is_materialized_community_row(row, item)), None,
    )
    if existing is not None:
        return existing
    # Old builds stored the bare item id. Import formats preserve arbitrary
    # personality text too, so adopt only the exact shape the old materializer
    # wrote; otherwise a remote item id could rewrite a user's imported voice.
    if archetypes.get_archetype(item["id"]) is None:
        legacy = conn.execute(
            "SELECT * FROM voice_profiles WHERE personality=? LIMIT 1",
            (item["id"],),
        ).fetchone()
        if legacy is not None:
            kind, instruct, _vd_states, _seed = _profile_fields(item)
            ref_text = item.get("sample_script") or (item.get("audio") or {}).get(
                "ref_text", "",
            )
            if (
                legacy["ref_audio_path"] == f"{legacy['id']}.wav"
                and legacy["kind"] == kind
                and legacy["instruct"] == instruct
                and legacy["language"] == item.get("language", "Auto")
                and legacy["ref_text"] == ref_text
                and legacy["seed"] is None
                and not legacy["vd_states"]
                and not legacy["is_locked"]
                and not legacy["verified_own_voice"]
            ):
                return legacy
    return None


def _heal_existing_profile(
    conn, row, item: dict, ref_text: str, personality: str, audio_filename: str,
) -> None:
    kind, instruct, vd_states, seed = _profile_fields(item)
    conn.execute(
        "UPDATE voice_profiles SET kind=?, instruct=?, vd_states=?, language=?, "
        "ref_text=?, seed=?, personality=?, ref_audio_path=? WHERE id=?",
        (
            kind, instruct, vd_states, item.get("language", "Auto"), ref_text,
            seed, personality, audio_filename, row["id"],
        ),
    )


@router.post("/community/items/{item_id}/use")
async def community_use(item_id: str, name: Optional[str] = Query(None)):
    """Materialize a community item into a reusable voice profile.

    Preset → render through the archetype engine. Voice → download the
    (host-allow-listed, SHA-256-verified) reference clip. Both create a
    ``voice_profiles`` row usable everywhere voices are picked.
    """
    _, items, _, _ = await asyncio.to_thread(_load, False)
    item = _find_item(items, item_id)

    canonical = _canonical_archetype(item)
    if canonical is not None:
        from api.routers.archetypes import use_archetype
        return await use_archetype(canonical["id"], name)

    from core import event_bus
    from core.db import db_conn

    ref_text = item.get("sample_script") or (item.get("audio") or {}).get("ref_text", "")
    personality = _community_personality(item)
    with db_conn() as conn:
        existing = _existing_community_profile(conn, item, personality)

    profile_id = existing["id"] if existing is not None else str(uuid.uuid4())[:8]
    audio_path: Optional[Path] = None
    if existing is not None and _community_audio_is_current(existing, item, ref_text):
        audio_filename = existing["ref_audio_path"]
    else:
        try:
            audio_filename, audio_path = await _materialize_item_audio(
                item, profile_id, publish=existing is None,
            )
        except HTTPException:
            raise
        except Exception as e:
            logger.error("Community 'use' failed", exc_info=True)
            raise HTTPException(
                status_code=503, detail="Couldn't add this voice right now.",
            ) from e

    if existing is not None:
        with db_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = conn.execute(
                "SELECT * FROM voice_profiles WHERE id=?", (existing["id"],),
            ).fetchone()
            owned = _existing_community_profile(conn, item, personality)
            still_owned = current is not None and (
                _is_materialized_community_row(current, item)
                or (owned is not None and owned["id"] == current["id"])
            )
            if still_owned:
                if audio_path is not None:
                    destination = Path(VOICES_DIR) / audio_filename
                    os.replace(audio_path, destination)
                    audio_path = None
                _heal_existing_profile(
                    conn, current, item, ref_text, personality, audio_filename,
                )
                existing_result = {"profile_id": current["id"], "name": current["name"]}
            else:
                existing_result = None
        if existing_result is not None:
            event_bus.emit("profiles", {"action": "updated", "id": existing_result["profile_id"]})
            return existing_result
        profile_id = str(uuid.uuid4())[:8]
        audio_filename = _community_profile_audio_filename(profile_id, item)
        destination = Path(VOICES_DIR) / audio_filename
        if audio_path is None:
            audio_filename, audio_path = await _materialize_item_audio(item, profile_id)
        else:
            os.replace(audio_path, destination)
            audio_path = destination

    if audio_path is None:  # defensive: a new profile always materialized above
        raise RuntimeError("new community profile has no materialized audio")
    profile_name = (name or item["name"]).strip() or item["name"]
    kind, instruct, vd_states, seed = _profile_fields(item)
    try:
        with db_conn() as conn:
            conn.execute("BEGIN IMMEDIATE")
            duplicate = _existing_community_profile(conn, item, personality)
            if duplicate is not None:
                duplicate_audio = duplicate["ref_audio_path"]
                if not _community_audio_is_current(duplicate, item, ref_text):
                    duplicate_audio = _community_profile_audio_filename(duplicate["id"], item)
                    duplicate_path = Path(VOICES_DIR) / duplicate_audio
                    _copy_atomic(audio_path, duplicate_path)
                _heal_existing_profile(
                    conn, duplicate, item, ref_text, personality, duplicate_audio,
                )
                with contextlib.suppress(OSError):
                    audio_path.unlink()
                duplicate_result = {"profile_id": duplicate["id"], "name": duplicate["name"]}
            else:
                duplicate_result = None
            if duplicate_result is None:
                conn.execute(
                    "INSERT INTO voice_profiles "
                    "(id, name, ref_audio_path, ref_text, instruct, language, seed, personality, "
                    "created_at, kind, vd_states) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (profile_id, profile_name, audio_filename, ref_text, instruct,
                     item.get("language", "Auto"), seed, personality, time.time(), kind, vd_states),
                )
    except Exception:
        with contextlib.suppress(OSError):
            audio_path.unlink()
        raise
    if duplicate_result is not None:
        event_bus.emit("profiles", {"action": "updated", "id": duplicate_result["profile_id"]})
        return duplicate_result
    event_bus.emit("profiles", {"action": "created", "id": profile_id})
    return {"profile_id": profile_id, "name": profile_name}
