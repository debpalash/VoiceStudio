"""Cap the request body of the audio-upload API routes before it is parsed.

FastAPI parses a multipart form before the handler runs, and Starlette spools
file parts to a temporary file with no size limit, so a handler's own cap
only refuses an oversized upload after the temporary volume has taken all of
it. This middleware counts the bytes as they arrive (and trusts a declared
``Content-Length`` to refuse early) on the routes that accept audio uploads.

The 413 is raised as an ``HTTPException`` from ``receive``: FastAPI re-raises
those from body parsing, so each API's route class renders it in its own
error shape (``status``/``error`` name the slug for ElevenLabs and OpenAI).
"""
from __future__ import annotations

import re

from starlette.exceptions import HTTPException

#: Matches the handlers' own per-file caps (2 GB), plus room for the other
#: form fields and multipart framing.
MAX_UPLOAD_BYTES = 2 * 1024 * 1024 * 1024
FORM_OVERHEAD_BYTES = 1024 * 1024

UPLOAD_PATHS = re.compile(
    r"^(?:/v1/audio/(?:transcriptions|translations)"
    r"|/elevenlabs/v1/(?:speech-to-text|audio-isolation"
    r"|(?:speech-to-speech|voice-changer)/[^/]+(?:/stream)?))/?$"
)


def _too_large() -> HTTPException:
    return HTTPException(status_code=413, detail={
        "status": "file_too_large",
        "error": "file_too_large",
        "message": "The upload is larger than 2 GB.",
    })


class UploadSizeLimitMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if (scope["type"] != "http" or scope.get("method") != "POST"
                or not UPLOAD_PATHS.match(scope.get("path") or "")):
            await self.app(scope, receive, send)
            return
        limit = MAX_UPLOAD_BYTES + FORM_OVERHEAD_BYTES
        declared = dict(scope.get("headers") or []).get(b"content-length", b"")
        oversized = declared.isdigit() and int(declared) > limit
        received = 0

        async def limited_receive():
            nonlocal received
            if oversized:
                raise _too_large()
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body") or b"")
                if received > limit:
                    raise _too_large()
            return message

        await self.app(scope, limited_receive, send)
