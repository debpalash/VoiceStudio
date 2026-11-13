"""Static file mounts for user content that can never run as a page.

``/audio``, ``/voice_audio`` and ``/demo_audio`` serve files whose bytes came
from people (uploads, imported bundles) or from the engines. They are served
from the backend's own origin, which the backend trusts on loopback, so a file
the browser rendered as HTML or SVG could script the whole API. Every response
from these mounts therefore carries:

* ``X-Content-Type-Options: nosniff``, so the declared type is final;
* ``Content-Security-Policy: default-src 'none'; sandbox``, so even a document
  that is rendered can run no script and load nothing;
* a declared type that is audio, video or a raster image, and otherwise
  ``application/octet-stream`` with ``Content-Disposition: attachment``.

``<audio>``/``<video>``/``<img>`` elements and ``fetch()`` are unaffected by
these headers, so in-app playback and downloads keep working.
"""
from __future__ import annotations

from starlette.staticfiles import StaticFiles
from starlette.types import Scope

_INLINE_PREFIXES = ("audio/", "video/")
_INLINE_IMAGES = frozenset({"image/png", "image/jpeg", "image/webp", "image/gif"})
_CSP = "default-src 'none'; sandbox"


def _inline(content_type: str) -> bool:
    base = content_type.split(";", 1)[0].strip().lower()
    return base.startswith(_INLINE_PREFIXES) or base in _INLINE_IMAGES


class SafeMediaFiles(StaticFiles):
    async def get_response(self, path: str, scope: Scope):
        response = await super().get_response(path, scope)
        headers = response.headers
        headers["X-Content-Type-Options"] = "nosniff"
        headers["Content-Security-Policy"] = _CSP
        content_type = headers.get("content-type", "")
        if 200 <= response.status_code < 300 and not _inline(content_type):
            headers["content-type"] = "application/octet-stream"
            headers["content-disposition"] = "attachment"
        return response
