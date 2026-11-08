"""scripts/release.py: version suggestions, lockstep bump and changelog section."""
from __future__ import annotations

import datetime
import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
TODAY = datetime.date(2026, 10, 23)


@pytest.fixture(scope="module")
def release():
    spec = importlib.util.spec_from_file_location("release_script", ROOT / "scripts/release.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    ("current", "channel", "tags", "expected"),
    [
        # package.json already names the upcoming release: v0.5.7 is the new tag.
        ("0.5.7", "stable", ["v0.5.6"], "0.5.7"),
        ("0.5.7", "beta", ["v0.5.6"], "0.5.7-beta.1"),
        ("0.5.7", "beta", ["v0.5.6", "v0.5.7-beta.1"], "0.5.7-beta.2"),
        ("0.5.7", "beta", ["v0.5.7-beta.9"], "0.5.7-beta.10"),
        ("0.5.7-beta.2", "stable", ["v0.5.6", "v0.5.7-beta.2"], "0.5.7"),
        # the base is already released: move to the next patch
        ("0.5.7", "stable", ["v0.5.7"], "0.5.8"),
        ("0.5.7", "beta", ["v0.5.7"], "0.5.8-beta.1"),
        ("0.5.7", "stable", [], "0.5.7"),
    ],
)
def test_next_version(release, current, channel, tags, expected):
    assert release.next_version(current, channel, tags) == expected


def test_latest_stable_ignores_betas_and_sorts_numerically(release):
    tags = ["v0.5.9", "v0.5.10", "v0.5.11-beta.1", "v0.4.0", "nightly"]
    assert release.latest_stable(tags) == "v0.5.10"
    assert release.latest_stable(["v1.0.0-beta.1"]) is None


def test_next_version_rejects_other_shapes(release):
    with pytest.raises(ValueError):
        release.next_version("0.5.8-rc.1", "beta")
    with pytest.raises(ValueError):
        release.next_version("0.5.8", "nightly")


def test_bump_files_moves_all_three_and_only_the_version(release, tmp_path):
    (tmp_path / "backend/core").mkdir(parents=True)
    (tmp_path / "package.json").write_text(
        '{\n  "name": "x",\n  "version": "0.5.7",\n  "dependencies": {"v": "1.0.0"}\n}\n'
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "x"\nversion = "0.5.7"\n[tool]\nversion = "9"\n'
    )
    (tmp_path / "backend/core/version.py").write_text('_FALLBACK_VERSION = "0.5.7"\n')
    release.bump_files(tmp_path, "0.5.8-beta.1")

    assert json.loads((tmp_path / "package.json").read_text(encoding="utf-8"))["version"] == "0.5.8-beta.1"
    assert 'version = "0.5.8-beta.1"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert 'version = "9"' in (tmp_path / "pyproject.toml").read_text(encoding="utf-8")
    assert '_FALLBACK_VERSION = "0.5.8-beta.1"' in (tmp_path / "backend/core/version.py").read_text(encoding="utf-8")


def test_bump_files_fails_loudly_when_a_mirror_is_missing(release, tmp_path):
    (tmp_path / "backend/core").mkdir(parents=True)
    (tmp_path / "package.json").write_text('{\n  "version": "0.5.7"\n}\n')
    (tmp_path / "pyproject.toml").write_text("[project]\n")
    (tmp_path / "backend/core/version.py").write_text("")
    with pytest.raises(ValueError, match="pyproject.toml"):
        release.bump_files(tmp_path, "0.5.8")


CHANGELOG = "# Changelog\n\n## [Unreleased]\n\n**Highlights**\n\n- A (#1)\n\n## [0.5.7] — 2026-10-01\n\n- Old (#2)\n"


def test_stable_renames_unreleased_and_starts_a_fresh_one(release):
    text = release.prepare_changelog(CHANGELOG, "0.5.8", TODAY)
    assert text.index("## [Unreleased]") < text.index("## [0.5.8] — 2026-10-23") < text.index("## [0.5.7]")
    unreleased, rest = text.split("## [0.5.8]")
    assert "- A (#1)" not in unreleased and "- A (#1)" in rest


def test_beta_copies_unreleased_so_it_stays_for_the_final_release(release):
    text = release.prepare_changelog(CHANGELOG, "0.5.8-beta.1", TODAY)
    assert text.count("- A (#1)") == 2
    assert text.index("## [Unreleased]") < text.index("## [0.5.8-beta.1] — 2026-10-23")


def test_existing_section_is_left_alone_and_empty_unreleased_is_refused(release):
    done = release.prepare_changelog(CHANGELOG, "0.5.7", TODAY)
    assert done == CHANGELOG
    with pytest.raises(ValueError, match="no \\[Unreleased\\] entries"):
        release.prepare_changelog("## [Unreleased]\n\n## [0.5.7] — x\n\n- a\n", "0.5.8", TODAY)


@pytest.mark.parametrize("version", ["0.5.8", "0.5.8-beta.3"])
def test_generated_changelog_passes_the_release_notes_extraction(release, version):
    """prepare_electron_release.py extracts `## [version]`; the section must be non-empty."""
    import re

    text = release.prepare_changelog(CHANGELOG, version, TODAY)
    section = re.search(
        rf"^## \[{re.escape(version)}\][^\n]*\n(.*?)(?=^## |\Z)", text, re.M | re.S
    )
    assert section and section[1].strip()


def _drive(release, monkeypatch, answers, *, dry_run, secrets_missing=(), branch="main", fail_on=None, calls=None, guessed=False, clean=False, main_version="0.5.7", remote_branch="", open_pr=""):
    """Run release() with scripted prompts and every side effect recorded, not run."""
    import argparse

    calls = [] if calls is None else calls
    feed = iter(answers)

    def fake_sh(*cmd, **kw):
        if cmd[:2] == ("git", "show"):
            return json.dumps({"version": main_version})
        if cmd[:3] == ("git", "tag", "-l"):
            return "v0.5.5 v0.5.6"
        if cmd[:2] == ("git", "branch"):
            return branch
        if cmd[:2] == ("git", "rev-list"):
            return "34"
        if cmd[:2] == ("git", "status"):
            return "" if clean else " M package.json"
        if cmd[:2] == ("git", "ls-remote") and "--heads" in cmd:
            calls.append(cmd)
            return remote_branch
        if cmd[:3] == ("gh", "pr", "list"):
            calls.append(cmd)
            return open_pr
        calls.append(cmd)
        if fail_on and fail_on in cmd:
            import subprocess

            raise subprocess.CalledProcessError(1, cmd)
        return ""

    monkeypatch.setattr("builtins.input", lambda prompt="": next(feed))
    monkeypatch.setattr(release, "check_ready", lambda tag=None: None)
    monkeypatch.setattr(release, "ensure_main", lambda: calls.append(("ensure_main",)))
    monkeypatch.setattr(release, "unsigned_platforms", lambda: (list(secrets_missing), guessed))
    monkeypatch.setattr(release, "sh", fake_sh)
    monkeypatch.setattr(release, "bump_files", lambda *a: calls.append(("bump", *a[1:])))
    monkeypatch.setattr(release, "dispatch_publish", lambda *a: calls.append(("dispatch", *a)))
    monkeypatch.setattr(
        release.Path, "write_text", lambda self, *a, **k: calls.append(("write", self.name))
    )
    monkeypatch.setattr(release, "prepare_changelog", lambda text, *a: text)
    monkeypatch.setattr(release, "read_fragments", lambda: [])
    monkeypatch.setattr(release, "remove_fragments", lambda: [])
    monkeypatch.setattr(release.Path, "read_text", lambda self, *a, **k: "")
    release.release(argparse.Namespace(dry_run=dry_run))
    return calls


def test_dry_run_shows_the_plan_and_changes_nothing(release, monkeypatch, capsys):
    calls = _drive(release, monkeypatch, ["beta", "", "draft"], dry_run=True, branch="feat/x")
    assert [c for c in calls if c[:2] != ("git", "fetch")] == []  # a fetch is read-only
    out = capsys.readouterr().out
    assert "Last release: v0.5.6    package.json on main: 0.5.7" in out
    assert "New release: v0.5.7-beta.1" in out and "format X.Y.Z-beta.N" in out
    assert "new tag   v0.5.7-beta.1   (last release v0.5.6)" in out
    assert "switch from feat/x to main, fast-forward main by 34 commits" in out
    assert "prerelease, never latest" in out and "nothing changed" in out


def test_a_wrong_confirmation_cancels_before_any_change(release, monkeypatch):
    with pytest.raises(SystemExit, match="Cancelled"):
        _drive(release, monkeypatch, ["stable", "", "draft", "yes"], dry_run=False)


def test_a_version_of_the_other_channel_is_refused(release, monkeypatch):
    with pytest.raises(SystemExit, match="not a beta version"):
        _drive(release, monkeypatch, ["beta", "9.9.9"], dry_run=True)


def test_draft_pushes_the_tag_but_never_dispatches_a_publish(release, monkeypatch):
    tag = "v0.5.7-beta.1"
    calls = _drive(release, monkeypatch, ["beta", "", "draft", tag], dry_run=False,
                   main_version="0.5.7-beta.1")
    assert ("ensure_main",) in calls
    assert ("git", "push", "origin", tag) in calls
    assert not any(c[0] == "dispatch" for c in calls)


def test_publish_without_signing_is_refused_unless_unsigned_is_accepted(release, monkeypatch):
    tag = "v0.5.7"
    with pytest.raises(SystemExit, match="configure signing"):
        _drive(release, monkeypatch, ["stable", "", "publish", "no"], dry_run=False,
               secrets_missing=["Windows"])
    calls = _drive(release, monkeypatch, ["stable", "", "publish", "yes", tag], dry_run=False,
                   secrets_missing=["Windows"])
    assert ("dispatch", tag, ["Windows"]) in calls


def _menu(release, monkeypatch, keys, default=0):
    feed = iter(keys)
    monkeypatch.setattr(release, "interactive", lambda: True)
    monkeypatch.setattr(release, "read_key", lambda: next(feed))
    options = [("stable", "a"), ("beta", "b"), ("other", "c")]
    return release.select_option("Channel?", options, default)


@pytest.mark.parametrize(
    ("keys", "expected"),
    [
        (["enter"], "stable"),
        (["down", "enter"], "beta"),
        (["down", "down", "enter"], "other"),
        (["down", "down", "down", "enter"], "stable"),  # wraps around
        (["up", "enter"], "other"),
        (["j", "j", "k", "enter"], "beta"),
        (["x", "", "enter"], "stable"),  # unknown keys are ignored
    ],
)
def test_select_moves_with_arrows_and_vim_keys(release, monkeypatch, keys, expected):
    assert _menu(release, monkeypatch, keys) == expected


def test_select_collapses_to_the_chosen_answer(release, monkeypatch, capsys):
    _menu(release, monkeypatch, ["down", "enter"])
    out = capsys.readouterr().out
    assert out.endswith("Channel? beta\n") and "\x1b[?25h" in out


@pytest.mark.parametrize("key", ["esc", "q", "interrupt"])
def test_select_cancels_on_escape_and_restores_the_cursor(release, monkeypatch, capsys, key):
    with pytest.raises(KeyboardInterrupt):
        _menu(release, monkeypatch, [key])
    assert "\x1b[?25h" in capsys.readouterr().out


def test_select_falls_back_to_a_typed_answer_without_a_terminal(release, monkeypatch):
    monkeypatch.setattr(release, "interactive", lambda: False)
    monkeypatch.setattr("builtins.input", lambda prompt="": "beta")
    assert release.select_option("Channel?", [("stable", "a"), ("beta", "b")]) == "beta"


@pytest.mark.skipif(sys.platform == "win32", reason="needs a POSIX pseudo-terminal")
def test_menu_reads_real_arrow_key_bytes_from_a_terminal():
    """Regression: buffered stdin swallowed the rest of an arrow's escape sequence,
    so every arrow press read as a lone Esc and cancelled the menu."""
    import os
    import pty
    import re
    import select
    import time

    driver = (
        "import importlib.util\n"
        "s = importlib.util.spec_from_file_location('r', 'scripts/release.py')\n"
        "m = importlib.util.module_from_spec(s); s.loader.exec_module(m)\n"
        "print('RESULT=' + m.select_option('Channel?', [('stable', 'x'), ('beta', 'y')]))\n"
        "print('RESULT=' + m.select_option('Mode?', [('draft', 'x'), ('publish', 'y')]))\n"
    )
    pid, fd = pty.fork()
    if pid == 0:
        os.chdir(ROOT)
        os.execvp(sys.executable, [sys.executable, "-c", driver])
    output = b""

    def pump(seconds: float) -> None:
        nonlocal output
        end = time.time() + seconds
        while time.time() < end:
            if select.select([fd], [], [], 0.05)[0]:
                try:
                    output += os.read(fd, 4096)
                except OSError:
                    return

    pump(1.0)
    os.write(fd, b"\x1b[B")  # down
    pump(0.3)
    os.write(fd, b"\r")
    pump(0.5)
    os.write(fd, b"\x1b[B\x1b[A\x1b[B")  # down, up, down
    pump(0.3)
    os.write(fd, b"\r")
    pump(0.8)
    os.waitpid(pid, 0)
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", output.decode(errors="replace"))
    assert re.findall(r"RESULT=(\w+)", text) == ["beta", "publish"], text


def test_all_platforms_signed_asks_nothing_and_does_not_allow_unsigned(release, monkeypatch, capsys):
    tag = "v0.5.7"
    calls = _drive(release, monkeypatch, ["stable", "", "publish", tag], dry_run=False)
    assert ("dispatch", tag, []) in calls
    assert "macOS and Windows secrets are configured" in capsys.readouterr().out


def test_missing_windows_signing_is_named_and_needs_an_explicit_yes(release, monkeypatch, capsys):
    tag = "v0.5.7-beta.1"
    calls = _drive(release, monkeypatch, ["beta", "", "publish", "yes", tag], dry_run=False,
                   secrets_missing=["Windows"], main_version="0.5.7-beta.1")
    out = capsys.readouterr().out
    assert "macOS    signed" in out and "Windows  NO signing secrets" in out
    assert "UNSIGNED: Windows" in out
    assert ("dispatch", tag, ["Windows"]) in calls


def test_unreadable_secrets_are_reported_as_unknown_not_as_signed(release, monkeypatch, capsys):
    with pytest.raises(SystemExit, match="Cancelled"):
        _drive(release, monkeypatch, ["stable", "", "publish", "no"], dry_run=False,
               secrets_missing=["macOS", "Windows"], guessed=True)
    assert "could not read repo secrets" in capsys.readouterr().out


def test_draft_never_asks_about_signing(release, monkeypatch):
    calls = _drive(release, monkeypatch, ["beta", "", "draft", "v0.5.7-beta.1"], dry_run=False,
                   secrets_missing=["macOS", "Windows"])
    assert not any(c[0] == "dispatch" for c in calls)


def test_signing_names_match_what_the_release_workflow_reads(release):
    assert set(release.PLATFORMS) == set(release.SIGNING_ENV_NAMES)
    workflow = (ROOT / ".github/workflows/electron-release.yml").read_text(encoding="utf-8")
    for names in release.SIGNING_ENV_NAMES.values():
        for name in names:
            assert f"secrets.{name}" in workflow, f"{name} is not used by electron-release.yml"


def test_a_version_already_on_main_is_tagged_without_touching_main(release, monkeypatch, capsys):
    """The second run, after the release PR merged: tag main, change nothing."""
    tag = "v0.5.7-beta.1"
    calls = _drive(release, monkeypatch, ["beta", "", "draft", tag], dry_run=False,
                   main_version="0.5.7-beta.1")
    assert not any(c[:2] in {("git", "commit"), ("git", "switch")} for c in calls)
    assert not any(c[0] in {"bump", "write"} for c in calls)
    assert ("git", "tag", tag) in calls and ("git", "push", "origin", tag) in calls
    assert "main already has 0.5.7-beta.1" in capsys.readouterr().out


def test_a_new_version_opens_a_release_pull_request_and_never_pushes_main(release, monkeypatch, capsys):
    """main only changes through pull requests: the bump goes to release/<tag>."""
    tag = "v0.5.7-beta.1"
    calls = _drive(release, monkeypatch, ["beta", "", "publish", tag], dry_run=False)
    branch = f"release/{tag}"
    assert ("git", "switch", "-c", branch) in calls
    assert ("git", "commit", "-m", f"chore(release): {tag}") in calls
    assert ("git", "push", "-u", "origin", branch) in calls
    assert any(c[:3] == ("gh", "pr", "create") for c in calls)
    assert ("git", "push", "origin", "main") not in calls
    assert not any(c[:2] == ("git", "tag") for c in calls)
    assert not any(c[0] == "dispatch" for c in calls)
    assert "run `make release` again" in capsys.readouterr().out


def test_a_rerun_after_a_failed_pull_request_step_finishes_it_without_bumping_again(release, monkeypatch):
    """The push landed but `gh pr create` failed: the rerun reuses the branch."""
    tag = "v0.5.7-beta.1"
    branch = f"release/{tag}"
    calls = _drive(release, monkeypatch, ["beta", "", "draft", tag], dry_run=False,
                   remote_branch=f"abc123\trefs/heads/{branch}")
    assert ("git", "switch", "-c", branch, "--track", f"origin/{branch}") in calls
    assert not any(c[0] in {"bump", "write"} or c[:2] == ("git", "commit") for c in calls)
    assert ("git", "push", "-u", "origin", branch) in calls
    assert any(c[:3] == ("gh", "pr", "create") for c in calls)


def test_an_open_release_pull_request_is_not_opened_twice(release, monkeypatch):
    tag = "v0.5.7-beta.1"
    calls = _drive(release, monkeypatch, ["beta", "", "draft", tag], dry_run=False,
                   remote_branch=f"abc123\trefs/heads/release/{tag}", open_pr="3500")
    assert not any(c[:3] == ("gh", "pr", "create") for c in calls)


def test_release_folds_fragments_into_unreleased_before_the_section_is_made(release):
    from changelog_fragments import apply_fragments

    text = "## [Unreleased]\n\n### Fixed\n\n- Old (#1)\n\n## [0.5.6] — 2026-01-01\n\n- x\n"
    folded = apply_fragments(text, [("Fixed", "New (#2)")])
    out = release.prepare_changelog(folded, "0.5.8", TODAY)
    assert "## [0.5.8] — " in out and "- New (#2)\n- Old (#1)" in out.split("## [0.5.6]")[0]
