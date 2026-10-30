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
import sys
from pathlib import Path

import pytest

_MODULE = sys.modules[__name__]
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
    entries = subprocess.run(
        ["git", "-C", str(_REPO), "ls-files", "--stage", "-z"], capture_output=True, text=True, check=True
    ).stdout.split("\0")
    for entry in filter(None, entries):
        metadata, name = entry.split("\t", 1)
        if metadata.split()[0] == "160000":
            continue
        path = _REPO / name
        if path.is_dir():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
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


@pytest.mark.parametrize("gitlink_is_directory", [True, False])
def test_tracked_text_files_skip_gitlinks_and_nontext_files(tmp_path, monkeypatch, gitlink_is_directory):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True)

    git("init", "-q")
    git("config", "user.name", "Contact Test")
    git("config", "user.email", "contact@example.com")
    (tmp_path / "README.md").write_text("hi@voicestudio.sh\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(b"\xff\xfe")
    git("add", ".")
    git("commit", "-qm", "source")
    source = git("rev-parse", "HEAD").strip()
    git("update-index", "--add", "--cacheinfo", f"160000,{source},submodule")
    if gitlink_is_directory:
        (tmp_path / "submodule").mkdir()
        (tmp_path / "submodule" / "NOTICE.txt").write_text("vendor@example.com\n", encoding="utf-8")
    else:
        (tmp_path / "submodule").write_text("vendor@example.com\n", encoding="utf-8")
    (tmp_path / "untracked.txt").write_text("not tracked\n", encoding="utf-8")
    monkeypatch.setattr(_MODULE, "_REPO", tmp_path)
    assert dict(_tracked_text_files()) == {"README.md": "hi@voicestudio.sh\n"}


@pytest.mark.parametrize("source_is_directory", [False, True])
def test_tracked_text_files_skip_directories_without_hiding_unreadable_source(tmp_path, monkeypatch, source_is_directory):
    def listing(*args, **kwargs):
        assert args[0] == ["git", "-C", str(tmp_path), "ls-files", "--stage", "-z"]
        names = "100644 " + "a" * 40 + " 0\tREADME.md\0"
        return subprocess.CompletedProcess(args[0], 0, stdout=names)

    def unreadable(path, *args, **kwargs):
        raise PermissionError(str(path))

    if source_is_directory:
        (tmp_path / "README.md").mkdir()
    else:
        (tmp_path / "README.md").write_text("hi@voicestudio.sh", encoding="utf-8")
    monkeypatch.setattr(_MODULE, "_REPO", tmp_path)
    monkeypatch.setattr(subprocess, "run", listing)
    monkeypatch.setattr(Path, "read_text", unreadable)
    if source_is_directory:
        assert list(_tracked_text_files()) == []
    else:
        with pytest.raises(PermissionError, match="README.md"):
            list(_tracked_text_files())


@pytest.mark.parametrize("name", ["README.md", "backend/main.py", "docs/install/windows.md"])
def test_real_source_contacts_are_rejected(name, monkeypatch):
    monkeypatch.setattr(_MODULE, "_tracked_text_files", lambda: [(name, "legacy@voicestudio.sh")])
    with pytest.raises(AssertionError, match="legacy@voicestudio.sh"):
        test_only_project_contact_addresses_are_published()


@pytest.mark.parametrize("name, text", [
    ("README.md", "HI@voicestudio.sh partner@voicestudio.sh security@voicestudio.sh sponsor@voicestudio.sh"),
    ("docs/install/windows.md", "author@example.org 12345+author@users.noreply.github.com"),
    ("tests/test_fixture.py", "fixture@voicestudio.sh"),
    ("mobile/ios/VoiceStudio/Resources/NativeLicences/library.txt", "copyright@vendor.sh"),
    ("mobile/android/app/src/main/assets/licenses/native-library.txt", "copyright@vendor.sh"),
])
def test_allowed_contact_and_attribution_categories_remain_accepted(name, text, monkeypatch):
    monkeypatch.setattr(_MODULE, "_tracked_text_files", lambda: [(name, text)])
    test_only_project_contact_addresses_are_published()
