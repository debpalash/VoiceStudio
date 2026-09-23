"""Runtime API-base injection for the served SPA (Docker / reverse-proxy).

`VITE_*` vars are inlined at build time, so a prebuilt image cannot take an
API-base override from `docker run -e`. When `OMNIVOICE_PUBLIC_API_BASE` is set,
the backend injects it into `index.html` as `window.__OMNIVOICE_API_BASE__`,
which the SPA's API resolver reads first. These helpers are pure so they can be
unit-tested without booting the app.
"""
from __future__ import annotations

import json
import os
import re


def frontend_dist_dir() -> str:
    """The built SPA served at "/" when it exists (Docker, source builds).

    Resolved relative to the backend package root, exactly where ``main``
    looks — one seam so tests can serve a real SPA mount without building it.
    """
    backend_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(backend_root, "..", "frontend", "dist")

# Operator-controlled value, but validate to a plain http(s) URL with no
# whitespace, quotes, or angle brackets so it can never break out of the
# injected <script> element.
_URL_RE = re.compile(r"^https?://[^\s<>\"']+$")


def is_valid_public_api_base(value: str) -> bool:
    """True if `value` is a safe http(s) URL we can inject into HTML."""
    return bool(value) and bool(_URL_RE.match(value))


def inject_api_base(html_doc: str, api_base: str) -> str:
    """Insert `window.__OMNIVOICE_API_BASE__` right after the SPA's <head>.

    `api_base` is JSON-encoded (neutralising quotes); the caller is expected to
    have validated it via `is_valid_public_api_base` first. Falls back to
    prepending the snippet if the document has no <head>.
    """
    snippet = f"<script>window.__OMNIVOICE_API_BASE__={json.dumps(api_base)};</script>"
    if "<head>" in html_doc:
        return html_doc.replace("<head>", "<head>" + snippet, 1)
    return snippet + html_doc
