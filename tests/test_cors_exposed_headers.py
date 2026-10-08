"""Every response header the renderer reads must be CORS-exposed.

A header the backend sets but CORS does not expose reads as ``null`` in a
browser UI served from another origin (the web UI, a remote backend), so the
take's id, seed and routing silently vanish there while working in the
same-origin desktop app. This scans the renderer for header reads and checks
them against the backend's single expose list.
"""
from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLIENT_ROOTS = (ROOT / "electron/src/renderer", ROOT / "electron/src/shared")

# Readable cross-origin without being exposed (Fetch "CORS-safelisted").
SAFELISTED = {
    "cache-control", "content-language", "content-length", "content-type",
    "expires", "last-modified", "pragma",
}
_READ = re.compile(
    r"""(?:headers\.get|header(?:Int|Float))\(\s*(?:headers\s*,\s*)?['"`]([A-Za-z0-9-]+)['"`]""",
)


def _client_header_reads() -> dict[str, set[str]]:
    reads: dict[str, set[str]] = {}
    for root in CLIENT_ROOTS:
        for path in root.rglob("*"):
            if path.suffix not in {".ts", ".tsx", ".js", ".jsx"} or ".test." in path.name:
                continue
            for name in _READ.findall(path.read_text(encoding="utf-8")):
                reads.setdefault(name.lower(), set()).add(str(path.relative_to(ROOT)))
    return reads


def test_scan_finds_the_generate_take_headers():
    reads = _client_header_reads()
    for name in ("x-audio-id", "x-seed", "x-omnivoice-routing", "x-gen-time"):
        assert name in reads, f"header scan no longer sees {name}; fix the regex"


def test_every_header_the_client_reads_is_exposed():
    from core.csrf import CORS_EXPOSED_HEADERS

    main_source = (ROOT / "backend/main.py").read_text(encoding="utf-8")
    marker = re.search(r'BACKEND_MARKER_HEADER = "([^"]+)"', main_source).group(1)
    assert "expose_headers=[*CORS_EXPOSED_HEADERS, BACKEND_MARKER_HEADER]" in main_source

    exposed = {name.lower() for name in CORS_EXPOSED_HEADERS} | {marker.lower()}
    missing = {
        name: sorted(files)
        for name, files in _client_header_reads().items()
        if name not in exposed and name not in SAFELISTED
    }
    assert not missing, f"add these to core.csrf.CORS_EXPOSED_HEADERS: {missing}"
