"""External-tool stderr must be scrubbed before it reaches a client.

An ffmpeg/ffprobe/yt-dlp failure message carries the command's own view of the
world: absolute paths under the user's home, the URL it was fetching (which can
carry credentials in a mirror or proxy configuration), and provider error bodies.
`core/scrub.scrub_text` is the repository's redaction chokepoint and the global 500
handler already uses it; these detail strings were built from raw `stderr`, so the
text reached the UI toast and the prefilled bug report the user is invited to
publish.

The tests drive the helpers directly with a synthetic stderr line rather than
running ffmpeg, so they need no media and no binaries.
"""
from __future__ import annotations

import pathlib
import re

_ROUTERS = pathlib.Path(__file__).resolve().parents[1] / "backend" / "api" / "routers"

#: A `detail=` / `detail =` assignment that embeds decoded subprocess output must
#: pass through the redactor. Anchoring on the assignment (not the word "detail")
#: keeps a match on a branch or loop that merely mentions it -- gallery.py tests a
#: substring against the raw text on purpose, before the value is scrubbed for the
#: response.
_DETAIL_ASSIGN = re.compile(r"\bdetail\s*=")
_DECODE = re.compile(r"stderr\.decode|error_text")


def _unscrubbed_stderr_details() -> list[str]:
    offenders = []
    for path in sorted(_ROUTERS.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        index = 0
        while index < len(lines):
            if _DETAIL_ASSIGN.search(lines[index]):
                # The statement may continue on following lines; join until it ends.
                statement = lines[index]
                end = index + 1
                while end < len(lines) and not statement.rstrip().endswith((",", ")",)):
                    statement += " " + lines[end].strip()
                    end += 1
                if _DECODE.search(statement) and "scrub_text" not in statement:
                    offenders.append(f"{path.name}:{index + 1}: {statement.strip()[:90]}")
                index = end
                continue
            index += 1
    return offenders


def test_the_scan_finds_the_sites_it_is_meant_to_check():
    # A vacuous scanner would pass forever; prove it reads a realistic statement.
    statement = 'raise HTTPException(status_code=500, detail=f"Download failed: {error_text}")'
    assert _DECODE.search(statement) and "scrub_text" not in statement
    assert "scrub_text" in 'detail=f"x: {scrub_text(error_text)}"'


def test_no_router_returns_raw_subprocess_stderr_to_a_client():
    offenders = _unscrubbed_stderr_details()
    assert not offenders, (
        "These error details interpolate raw subprocess stderr, which can carry home "
        "paths, credential-bearing URLs and provider error text into a toast the user "
        "may paste into a public issue. Wrap the value in scrub_text():\n"
        + "\n".join(offenders)
    )


def test_scrub_text_redacts_what_these_messages_carry():
    """The redactor must remove the paths and URLs a tool's stderr carries.

    URL userinfo (`https://user:token@host`) is covered by the scrubber change in
    the same audit train; this asserts the path half, which is what the routers
    below rely on today.
    """
    from core.scrub import scrub_text

    line = "ERROR: unable to open /home/someone/media/clip.mp4 (HTTP 403)"
    scrubbed = scrub_text(line)
    assert "/home/someone" not in scrubbed
    assert "clip.mp4" in scrubbed
