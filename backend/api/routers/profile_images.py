"""Keyless portrait search with bounded, normalized, safe thumbnails."""
import asyncio
import base64
import html
import re
from html.parser import HTMLParser
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query

from core.browser_guard import reject_cross_site_get
from core.profile_images import MAX_IMAGE_BYTES, normalize_portrait

router = APIRouter()


def trusted_thumbnail(url: str) -> bool:
    try:
        parsed = urlsplit(url)
        google = parsed.hostname in {
            f"encrypted-tbn{i}.gstatic.com" for i in range(4)
        }
        openverse = (
            parsed.hostname == "api.openverse.org"
            and re.fullmatch(r"/v1/images/[0-9a-f-]+/thumb/?", parsed.path) is not None
        )
        return (
            parsed.scheme == "https"
            and not parsed.username
            and not parsed.password
            and parsed.port in (None, 443)
            and (google or openverse)
        )
    except ValueError:
        return False


def google_thumbnails(document: str) -> list[tuple[str, str]]:
    """Extract result thumbnails only; never download third-party originals."""
    results = []
    seen = set()

    def add(title, source):
        if not isinstance(source, str) or source in seen:
            return
        if not (trusted_thumbnail(source) or source.startswith("data:image/jpeg;base64,")):
            return
        seen.add(source)
        if len(results) < 20:
            results.append((title or "", source))

    class Images(HTMLParser):
        def handle_starttag(self, tag, attrs):
            if tag == "img":
                values = dict(attrs)
                add(values.get("alt"), values.get("src") or values.get("data-src"))

    Images().feed(document)
    # Google also assigns thumbnails from script strings after rendering.
    decoded = html.unescape(document)
    for escaped, literal in ((r"\u003d", "="), (r"\u0026", "&"), (r"\/", "/")):
        decoded = decoded.replace(escaped, literal)
    for match in re.finditer(r'https://encrypted-tbn[0-3]\.gstatic\.com/[^\s"\'<>\\]+|data:image/jpeg;base64,[A-Za-z0-9+/=]+', decoded):
        add("", match.group())
    return results


async def openverse_thumbnails(client: httpx.AsyncClient, name: str) -> list[tuple[str, str]]:
    """Public-domain/CC portrait fallback when Google returns its JS-only shell.

    Openverse requires no user credential, excludes sensitive results by
    default, and can restrict results to licenses that allow modification and
    commercial use. We still fetch only its own thumbnail proxy.
    """
    response = await client.get(
        "https://api.openverse.org/v1/images/",
        headers={
            "User-Agent": "VoiceStudio/0.5 (+https://github.com/debpalash/VoiceStudio)",
            "Accept": "application/json",
        },
        params={
            "q": name,
            "page_size": 20,
            "mature": "false",
            "extension": "jpg,png",
            "aspect_ratio": "square",
            "license_type": "commercial,modification",
        },
    )
    response.raise_for_status()
    if len(response.content) > 4 * 1024 * 1024:
        raise ValueError("Search response too large")
    payload = response.json()
    rows = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        raise ValueError("Invalid search response")
    results = []
    seen = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        source = row.get("thumbnail")
        if not isinstance(source, str) or source in seen or not trusted_thumbnail(source):
            continue
        seen.add(source)
        title = str(row.get("title") or name)
        creator = str(row.get("creator") or "").strip()
        license_name = str(row.get("license") or "").upper()
        credit = " · ".join(value for value in (creator, license_name) if value)
        results.append((f"{title} — {credit}" if credit else title, source))
    return results


@router.get("/profile-images/search", dependencies=[Depends(reject_cross_site_get)])
async def search_profile_images(name: str = Query(min_length=1, max_length=100)):
    if not name.strip():
        raise HTTPException(422, detail={"code": "image_search_failed"})
    async with httpx.AsyncClient(
        timeout=15,
        follow_redirects=False,
        headers={
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 Chrome/140.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    ) as client:
        results: list[tuple[str, str]] = []
        try:
            async with client.stream("GET", "https://www.google.com/search", params={
                "q": name.strip(), "udm": "2", "safe": "active", "tbs": "ift:jpg",
            }) as response:
                response.raise_for_status()
                document = bytearray()
                async for chunk in response.aiter_bytes():
                    document.extend(chunk)
                    if len(document) > 4 * 1024 * 1024:
                        raise ValueError("Search page too large")
            page = document.decode("utf-8", errors="replace")
            results = google_thumbnails(page)
        except (httpx.HTTPError, ValueError, TypeError):
            # Search providers can change their anonymous HTML or reject a
            # non-browser request. The fallback below keeps this explicit,
            # user-triggered feature useful without requiring credentials.
            results = []

        if not results:
            try:
                results = await openverse_thumbnails(client, name.strip())
            except (httpx.HTTPError, ValueError, TypeError):
                results = []
        if not results:
            raise HTTPException(502, detail={"code": "image_search_failed"})

        async def thumbnail(title, url):
            try:
                if url.startswith("data:image/jpeg;base64,"):
                    encoded = url.partition(",")[2]
                    if len(encoded) > MAX_IMAGE_BYTES * 4 // 3 + 4:
                        return None
                    data = base64.b64decode(encoded, validate=True)
                else:
                    if not trusted_thumbnail(url):
                        return None
                    async with client.stream("GET", url) as image:
                        image.raise_for_status()
                        data = bytearray()
                        async for chunk in image.aiter_bytes():
                            data.extend(chunk)
                            if len(data) > MAX_IMAGE_BYTES:
                                return None
                normalized = await asyncio.to_thread(normalize_portrait, bytes(data))
                return {"title": title[:200], "data": base64.b64encode(normalized).decode("ascii")}
            except (httpx.HTTPError, HTTPException, ValueError, TypeError):
                return None

        images = []
        for start in range(0, len(results), 5):
            batch = await asyncio.gather(*(thumbnail(*result) for result in results[start:start + 5]))
            images.extend(image for image in batch if image)
            if len(images) >= 5:
                break
        return {"images": images[:5]}
