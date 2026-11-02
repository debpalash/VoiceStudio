"""Release notes get a Contributors block from the tag comparison, automatically.

v0.5.7-beta.1 shipped with notes copied from [Unreleased], which name only the
people whose entries were credited by hand, so the maintainers and most community
authors appeared nowhere. prepare_electron_release.py now adds the list itself.
"""
from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def release(monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    spec = importlib.util.spec_from_file_location(
        "prepare_release_contributors_test", ROOT / "scripts/prepare_electron_release.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class FakeGh:
    """Answers the two `gh api` calls the helper makes and records them."""

    def __init__(self, base="v0.5.6", authors=(), pr_authors=()):
        self.base, self.authors, self.pr_authors, self.calls = base, list(authors), list(pr_authors), []

    def __call__(self, *args):
        self.calls.append(args)
        if args[0] == "pr":
            return "\n".join(self.pr_authors) + "\n"
        path = args[1]
        if path.endswith("/releases/latest"):
            return self.base + " 2026-09-23T08:53:32Z\n"
        if "/compare/" in path:
            return "\n".join(self.authors) + "\n"
        raise AssertionError(f"unexpected gh call: {args}")


def test_authors_are_ranked_by_commits_and_include_maintainers(release):
    gh = FakeGh(authors=["debpalash"] * 5 + ["velixio"] * 3 + ["rudycelekli"] * 3 + ["zed"])
    block = release.contributors_section("o/r", "v0.5.7", gh)
    assert block.startswith("### Contributors\n")
    # most commits first; a tie (velixio and rudycelekli, 3 each) is alphabetical
    assert "@debpalash, @rudycelekli, @velixio, @zed" in block


def test_bots_agents_and_unlinked_placeholders_are_never_credited(release):
    """Real v0.5.7-beta.1 data contained @claude and @invalid-email-address."""
    noise = ["dependabot[bot]", "github-actions[bot]", "claude", "Claude", "copilot",
             "invalid-email-address", "app/dependabot", "coderabbitai"]
    gh = FakeGh(authors=noise + ["velixio"], pr_authors=noise)
    assert release.contributors_section("o/r", "v0.5.7", gh).endswith("@velixio\n")
    assert release.contributors_section("o/r", "v0.5.7", FakeGh(authors=noise, pr_authors=noise)) == ""


def test_pr_authors_with_unlinked_commits_are_still_credited_after_the_committers(release):
    gh = FakeGh(authors=["debpalash", "velixio", "velixio"], pr_authors=["JoshuaWIls", "velixio", "alice"])
    block = release.contributors_section("o/r", "v0.5.7", gh)
    assert block.endswith("@velixio, @debpalash, @alice, @JoshuaWIls\n")


def test_the_range_starts_at_the_latest_stable_release_for_betas_and_stables(release):
    for tag in ("v0.5.7-beta.3", "v0.5.7"):
        gh = FakeGh(base="v0.5.6", authors=["velixio"])
        release.contributors_section("o/r", tag, gh)
        assert gh.calls[1][1] == f"repos/o/r/compare/v0.5.6...{tag}"
        assert "--paginate" in gh.calls[1]
        pr_call = gh.calls[2]
        assert pr_call[0] == "pr" and "merged:>=2026-09-23" in pr_call


def _prepare(release, monkeypatch, tmp_path, changelog, **kwargs):
    monkeypatch.setattr(release, "verify_release", lambda *a, **k: None)
    monkeypatch.chdir(tmp_path)
    Path("package.json").write_text(json.dumps({"version": "1.2.3-beta.1"}))
    Path("CHANGELOG.md").write_text(changelog)
    assets = tmp_path / "assets"
    assets.mkdir(exist_ok=True)
    for suffix in ("mac-arm64.dmg", "mac-x64.dmg", "linux-x64.deb"):
        (assets / f"VoiceStudio-1.2.3-beta.1-{suffix}").write_bytes(b"x")
    release.prepare(assets, "v1.2.3-beta.1", **kwargs)
    return (assets / "RELEASE_NOTES.md").read_text(encoding="utf-8")


CHANGELOG = "## [Unreleased]\n\n## [1.2.3-beta.1] — 2026-10-24\n\n- A fix (#1)\n\n## [1.2.2] — 2026-10-01\n\n- Old\n"


def test_release_notes_get_the_contributors_block_appended(release, monkeypatch, tmp_path):
    notes = _prepare(release, monkeypatch, tmp_path, CHANGELOG, repo="o/r",
                     gh=FakeGh(authors=["velixio", "debpalash", "debpalash"]))
    assert notes.startswith("- A fix (#1)\n\n### Contributors\n")
    assert notes.endswith("@debpalash, @velixio\n")


def test_a_hand_written_contributors_section_is_never_duplicated(release, monkeypatch, tmp_path):
    changelog = CHANGELOG.replace("- A fix (#1)\n", "- A fix (#1)\n\n### Contributors\n\n- @velixio — release tooling\n")
    gh = FakeGh(authors=["someone"])
    notes = _prepare(release, monkeypatch, tmp_path, changelog, repo="o/r", gh=gh)
    assert notes.count("Contributors") == 1 and "@someone" not in notes
    assert gh.calls == []


def test_without_a_repo_the_notes_are_the_changelog_section_exactly(release, monkeypatch, tmp_path):
    gh = FakeGh(authors=["velixio"])
    assert _prepare(release, monkeypatch, tmp_path, CHANGELOG, gh=gh) == "- A fix (#1)\n"
    assert gh.calls == []


def test_a_failing_lookup_never_blocks_the_release(release, monkeypatch, tmp_path, capsys):
    def broken(*args):
        raise subprocess.CalledProcessError(1, args)

    notes = _prepare(release, monkeypatch, tmp_path, CHANGELOG, repo="o/r", gh=broken)
    assert notes == "- A fix (#1)\n"
    assert "Contributors section skipped" in capsys.readouterr().err


def test_the_release_workflow_passes_the_repository_to_both_prepare_calls():
    workflow = (ROOT / ".github/workflows/electron-release.yml").read_text(encoding="utf-8")
    calls = [l for l in workflow.splitlines() if "prepare_electron_release.py --assets" in l]
    assert len(calls) == 2
    assert all('--repo "$GITHUB_REPOSITORY"' in l for l in calls)
