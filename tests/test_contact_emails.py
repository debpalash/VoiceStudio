"""VoiceStudio publishes exactly four contact addresses (owner-set 2026-10-02;
sponsor@ added by the owner in #3674, 2026-10-29).

    hi@voicestudio.sh        general, business, licensing, collaboration
    partner@voicestudio.sh   brand and company partnerships, sponsor receipts
    security@voicestudio.sh  vulnerability reports
    sponsor@voicestudio.sh   sponsorship inquiries

Any other real address in a shipped file (docs, UI, locales, package metadata,
legal text) is a stale or personal contact. Tests and fixtures may use
reserved example domains (RFC 2606 / RFC 6761) or fake addresses, and docs may
show GitHub no-reply commit addresses.
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_CONTACTS = {
    "hi@voicestudio.sh",
    "partner@voicestudio.sh",
    "security@voicestudio.sh",
    "sponsor@voicestudio.sh",
}
_EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")
_RESERVED_DOMAIN = re.compile(r"(^|\.)(example\.(com|net|org)|example|test|invalid|localhost|local)$", re.I)
# GitHub no-reply commit addresses are identities, not contact addresses.
_NOREPLY_DOMAIN = "users.noreply.github.com"
_TEST_PATH = re.compile(r"(^|/)(tests?|__tests__|fixtures)/|\.test\.[a-z]+$|_test\.py$")
# Vendored copyright notices retain upstream identities, not project contacts.
_NATIVE_LICENCE_PATH = re.compile(
    r"^mobile/(?:ios/VoiceStudio/Resources/NativeLicences/[^/]+\.txt"
    r"|android/app/src/main/assets/licenses/native-[^/]+\.txt"
    # LAME (LGPL) is vendored unmodified for MP3 export; its source headers name its authors.
    r"|ios/VoiceStudioKit/Sources/VSNative/Lame/.+)$"
)


def _tracked_text_files():
    names = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    for name in filter(None, names):
        path = _REPO / name
        # Submodule gitlinks are directories; Windows raises PermissionError, not
        # IsADirectoryError, when one is opened.
        if not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue


def test_only_project_contact_addresses_are_published():
    stray = []
    for name, text in _tracked_text_files():
        if _NATIVE_LICENCE_PATH.fullmatch(name):
            continue
        for address in _EMAIL.findall(text):
            domain = address.rsplit("@", 1)[1]
            if (address.lower() in _CONTACTS or _RESERVED_DOMAIN.search(domain) or domain.lower() == _NOREPLY_DOMAIN
                    or _TEST_PATH.search(name)):
                continue
            stray.append(f"{name}: {address}")
    assert not stray, "Use hi@, partner@, security@ or sponsor@voicestudio.sh:\n" + "\n".join(stray)
