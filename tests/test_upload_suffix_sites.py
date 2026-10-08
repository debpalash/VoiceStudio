"""Client file names reach the disk only through the shared suffix helpers.

``os.path.splitext`` treats a backslash as a path separator only on Windows, so
a name like ``a.w\\..\\x`` yields a different extension per OS. ``upload_suffix``
and ``media_upload_suffix`` take everything after the last dot, so such a name is
refused the same way everywhere. A route that still calls ``splitext`` on an
upload name must validate the result against an allowlist; every such site is
listed here so a new one is reviewed rather than copied.
"""
from __future__ import annotations

import re
from pathlib import Path

ROUTERS = Path(__file__).resolve().parents[1] / "backend" / "api" / "routers"

# file -> number of ``splitext(...filename...)`` calls, each followed by an
# allowlist check (``ext not in allowed`` or the shared media policy).
_REVIEWED = {
    "audiobook.py": 1,   # cover: .jpg/.jpeg/.png only
    "dub_core.py": 3,    # upload allowlists (media/audio) and a path already contained to PREVIEW_DIR
    "gallery.py": 1,     # media_extension(..., MEDIA_EXTS, ...)
    "generation.py": 1,  # reference-audio type allowlist
    "profiles.py": 2,    # _REF_AUDIO_EXTS allowlists (create, replace)
    "watermark.py": 1,   # audio formats allowlist
}
# The extension of a client name (``[1]``); ``[0]`` stems are not used as suffixes.
_PATTERN = re.compile(r"splitext\([^\n]*filename[^\n]*\)\[1\]")


def test_upload_names_are_not_split_with_os_path_unreviewed():
    found = {}
    for path in sorted(ROUTERS.rglob("*.py")):
        count = len(_PATTERN.findall(path.read_text(encoding="utf-8")))
        if count:
            found[path.relative_to(ROUTERS).as_posix()] = count
    assert found == _REVIEWED, (
        "Use core.path_security.upload_suffix or core.media_types.media_upload_suffix "
        f"for client file names (or review the allowlist and list the site here): {found}"
    )
