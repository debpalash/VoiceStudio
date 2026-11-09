"""CHANGELOG.md → structured release notes (core.changelog) — feat/safe-updates.

The parser must handle BOTH bullet styles that exist in the real changelog:
recent sections write each bullet as one long line; older sections hard-wrap
bullets across indented continuation lines. It also feeds the Settings →
Updates "What's new" viewer, so the shipped CHANGELOG.md itself is a fixture.
"""
import os

from core import changelog

_REPO_CHANGELOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md"
)

_SAMPLE = """# Changelog

All notable changes to OmniVoice Studio.

## [Unreleased]

- **Not shipped yet.** Should never appear in the viewer.

## [0.3.9] — 2026-07-02

The dictation release — one-paragraph headline.

### Added

- **Dictation, rebuilt.** Live waveform, ~0.5 s commit, clean punctuation. (#123)
- **LLM provider testing.** Latency + classified errors. (#887)

### Fixed

- **CUDA transcription works on packaged installs.** The compat libs now install at launch. (#827, #869)

## [0.3.8] — 2026-07-01

A stability-focused release that makes first-run and Windows "just work," ships
**live dictation** and more.

### Added

- **"Autofit" translation quality — the dub keeps the video's timing.** A new
  quality alongside Fast and Cinematic: the LLM rewrites each translated line so
  it fits. (#838)
- **A new LLM Providers settings page.** One page for **16 providers**. (#850)

## [0.3.7] — 2026-06-20

### Fixed

- **Old bug.** Squashed. (#700)

## [0.3.6] — 2026-06-15

### Changed

- **Older still.** (#600)
"""


def test_parses_versions_newest_first_and_skips_unreleased():
    releases = changelog.parse_changelog(_SAMPLE)
    assert [r["version"] for r in releases] == ["0.3.9", "0.3.8", "0.3.7", "0.3.6"]
    assert releases[0]["date"] == "2026-07-02"
    assert all("Not shipped yet" not in b
               for r in releases for s in r["sections"] for b in s["bullets"])


def test_single_line_bullets_parse_intact():
    r = changelog.parse_changelog(_SAMPLE)[0]
    assert r["intro"] == "The dictation release — one-paragraph headline."
    added = r["sections"][0]
    assert added["title"] == "Added"
    assert added["bullets"][0].startswith("**Dictation, rebuilt.**")
    assert added["bullets"][1].endswith("(#887)")
    assert [s["title"] for s in r["sections"]] == ["Added", "Fixed"]


def test_wrapped_bullets_are_joined_to_one_logical_line():
    r = changelog.parse_changelog(_SAMPLE)[1]  # 0.3.8, the wrapped style
    bullets = r["sections"][0]["bullets"]
    assert len(bullets) == 2
    # Continuation lines join with single spaces — no newlines, no double spaces.
    assert "\n" not in bullets[0]
    assert "quality alongside Fast and Cinematic" in bullets[0]
    assert bullets[0].endswith("(#838)")
    # The wrapped intro paragraph joins too.
    assert r["intro"].startswith("A stability-focused release")
    assert "ships **live dictation**" in r["intro"]


def test_limit_versions_caps_output():
    assert len(changelog.parse_changelog(_SAMPLE, limit_versions=2)) == 2
    assert len(changelog.parse_changelog(_SAMPLE, limit_versions=50)) == 4


def test_real_repo_changelog_parses():
    """The shipped changelog is the production input — it must parse into
    non-empty structured releases with the house sections."""
    with open(_REPO_CHANGELOG, encoding="utf-8") as fh:
        releases = changelog.parse_changelog(fh.read(), limit_versions=5)
    assert len(releases) == 5
    for r in releases:
        assert r["version"][0].isdigit()
        assert r["sections"], f"release {r['version']} parsed with no sections"
        assert all(s["bullets"] for s in r["sections"])
    # Newest-first ordering matches the file order.
    versions = [r["version"] for r in releases]
    assert versions == sorted(versions, key=lambda v: [int(x) for x in v.split("-")[0].split(".")], reverse=True)


def test_changelog_path_env_override(tmp_path, monkeypatch):
    f = tmp_path / "CHANGELOG.md"
    f.write_text("## [1.0.0] — 2027-01-01\n### Added\n- **X.** (#1)\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_CHANGELOG", str(f))
    assert changelog.changelog_path() == str(f)
    monkeypatch.setenv("OMNIVOICE_CHANGELOG", str(tmp_path / "missing.md"))
    assert changelog.changelog_path() is None


_CHANNELS = """# Changelog

## [Unreleased]

- Not yet released

## [0.5.7] — 2026-10-17

- Drafted, never tagged

## [0.5.7-beta.3] — 2026-10-26

- Third beta

## [0.5.7-beta.2] — 2026-10-24

- Second beta

## [0.5.6] — 2026-09-23

- Stable
"""


def test_notes_never_list_a_version_newer_than_the_running_app():
    versions = [r["version"] for r in changelog.parse_changelog(_CHANNELS, 5, "0.5.7-beta.2")]
    assert versions == ["0.5.7-beta.2", "0.5.6"]


def test_a_stable_app_skips_beta_sections():
    versions = [r["version"] for r in changelog.parse_changelog(_CHANNELS, 5, "0.5.6")]
    assert versions == ["0.5.6"]
    versions = [r["version"] for r in changelog.parse_changelog(_CHANNELS, 5, "0.5.7")]
    assert versions == ["0.5.7", "0.5.6"]


def test_unknown_app_version_keeps_every_release():
    assert len(changelog.parse_changelog(_CHANNELS, 5, None)) == 4
    assert len(changelog.parse_changelog(_CHANNELS, 5, "dev-build")) == 4


def test_intro_drops_download_table_and_images():
    text = """## [1.0.0] — 2026-10-01

**Headline.** The release.

**Download**

| Platform | Installer |
| --- | --- |
| Windows x64 | [Installer](https://example.test/a.exe) |

![screenshot](https://example.test/a.png)

**Upgrading:** Install over the old app.

### Fixed

- A fix. (#1)
"""
    [release] = changelog.parse_changelog(text)
    assert release["intro"] == "**Headline.** The release.\n\n**Upgrading:** Install over the old app."
