"""No tracked path that Windows cannot check out.

Windows rejects `< > : " | ? *` and control characters in filenames, treats a
trailing space or dot on a path component as invalid, and reserves the device
names CON/PRN/AUX/NUL/COM1-9/LPT1-9. A literal backslash is legal on Linux and
macOS but is the directory separator on Windows, so Git refuses it too (that
one is `core.protectNTFS`, on by default there). Git on Windows refuses such a
path with
`error: invalid path ...` and exits 128 — during **checkout**, before any
build or test step runs. So a single stray file like `:memory:.ses` (a sqlite
session artifact named after the `:memory:` DSN, committed by accident on the
#1798 branch) turns every Windows job red with an error that names a file
nobody edited, while Linux and macOS stay green.

Nothing else catches this: the file need not be referenced by any code, and
the platforms that can check it out do not care. This scans the index on every
platform so the failure surfaces as a named test rather than as a checkout
crash on one leg of the matrix.
"""
import re
import subprocess

import pytest
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]

# Characters git-for-Windows rejects outright, plus C0 controls. The backslash
# is in here because git stores paths with `/` separators, so a `\` that
# survives into a path component is part of a NAME — legal to commit from
# Linux, impossible to check out on Windows.
_BAD_CHARS = re.compile(r'[<>:"|?*\\\x00-\x1f]')
# Reserved DOS device names, with or without an extension (`NUL`, `nul.txt`).
_RESERVED = re.compile(r"^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)", re.IGNORECASE)


def _tracked_paths():
    out = subprocess.run(
        ["git", "-C", str(_ROOT), "ls-files", "-z"],
        capture_output=True, text=True, check=True,
    ).stdout
    return [p for p in out.split("\0") if p]


def windows_hostile_reason(rel):
    """Why Windows cannot check `rel` out, or None if it can.

    Pure so the rule itself is testable: the repo cannot carry a fixture for
    each hostile shape without becoming the very thing this test rejects.
    """
    for part in rel.split("/"):
        if _BAD_CHARS.search(part):
            return f"illegal character in {part!r}"
        if part != part.rstrip(" ."):
            return f"component {part!r} ends in a space or dot"
        if _RESERVED.match(part):
            return f"reserved device name {part!r}"
    return None


def test_no_windows_hostile_tracked_paths():
    # This test file names the offending characters in its own source, but its
    # *path* is what is checked — every tracked path is scanned, none skipped.
    offenders = [
        f"{rel} ({reason})"
        for rel in _tracked_paths()
        if (reason := windows_hostile_reason(rel))
    ]
    assert not offenders, (
        "Tracked paths Windows cannot check out — git exits 128 during checkout "
        "and every Windows CI job fails before it starts:\n  "
        + "\n  ".join(offenders)
    )


@pytest.mark.parametrize(
    "rel",
    [
        ":memory:.ses",          # the artifact that started this (#1798 branch)
        "a/b:c.txt",             # colon anywhere in the path, not just the root
        "docs/what?.md",         # the rest of the reserved punctuation
        "src/a<b>.py",
        'src/say"hi".py',
        "src/a|b.py",
        "src/a*b.py",
        "notes/back\\slash.md",  # legal on POSIX, a separator on Windows
        "docs/notes ",           # a component may not END in a space…
        "docs/notes.",           # …or a dot
        "docs/dir /x.md",        # directory components included
        "src/NUL",               # reserved device names, bare…
        "src/con.txt",           # …and with an extension, case-insensitively
        "src/COM1.log",
        "src/lpt9",
    ],
)
def test_rejects_every_shape_windows_refuses(rel):
    assert windows_hostile_reason(rel), f"{rel!r} should have been rejected"


@pytest.mark.parametrize(
    "rel",
    [
        "backend/core/version.py",
        "docs/adr/0001-something.md",
        "frontend/src/i18n/locales/zh-CN.json",
        "tests/test_no_windows_hostile_paths.py",
        "scripts/build-omnivoice-tts.sh",
        "bin/omnivoice-tts-linux-aarch64",
        "docs/console.md",       # merely CONTAINS a device name — fine
        "src/nulls.py",
        "src/com10.py",          # only COM1-9 are reserved
    ],
)
def test_accepts_ordinary_paths(rel):
    assert windows_hostile_reason(rel) is None, f"{rel!r} was rejected in error"
