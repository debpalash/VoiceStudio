"""CHANGELOG.md quiet-style linter — deterministic CI for the release-notes rule.

CLAUDE.md, "Release notes / changelog" (hard rule, owner-restyled 2026-07-17):
change entries are **quiet and scannable** — an optional tagged-release
introduction may precede the short `**Highlights**` bullet list, followed by `### Changed` / `### Added` / … subsections where each entry is a
single one-liner carrying its `(#NNN)` ref and `— thanks @user!` credit where
applicable. No multi-line paragraphs, no raw commit dumps.

Scope: the file is newest-first, so the linter checks `## [Unreleased]` and any
released section until it reaches the first release dated before the restyle
(2026-07-17). Everything older is grandfathered in the old bold-lead style —
and because new sections are always inserted at the top, nothing new can hide
behind an old date or a typo'd heading.
"""

import datetime
import os
import re

_REPO_CHANGELOG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "CHANGELOG.md"
)

_RULE = 'CLAUDE.md "Release notes / changelog" (hard rule, owner-restyled 2026-07-17)'

# Sections released before the owner restyle keep their old bold-lead style.
_STYLE_EPOCH = datetime.date(2026, 7, 17)

# Longest a one-liner entry may run. Generous — the point is to fail paragraph
# entries, not to golf good one-liners.
_MAX_ENTRY_CHARS = 400

# Entry subsections whose bullets must carry a `(#N)` ref or a
# `— thanks @user!` credit. Changed/Docs/CI/License lines are often
# owner-authored housekeeping without an issue, so only these two.
# Unreleased CI entries are also checked below; published infrastructure
# notes remain grandfathered so old releases do not need invented references.
_REF_REQUIRED_SECTIONS = {"Added", "Fixed"}

# Owner-authored infra entries allowed without a ref/credit (direct-to-main
# work with no issue or PR to point at). Match is by substring; keep this list
# short and delete entries once they ship in a tagged release.
_REF_ALLOWLIST = (
    # owner commit 7036e101 — first-run consent prompt, committed straight to main
    "First-run consent question for the existing opt-in analytics",
    # owner commits ce842737 + dc766baf — Colab notebook, committed straight to main
    "Official Google Colab notebook",
    # owner-directed architecture work (remote/goal.md → goal_v2.md), no issue
    "Remote workers** sends individual jobs to GPUs",
    # owner-requested workspace promotion (engines + model store out of Settings), no issue
    "Model Catalogue** — a workspace of its own",
    # owner-authored hardening committed without a public issue
    "The local API refuses requests sent by other websites",
    "URL imports accept only http(s) links",
    "Gallery, dubbing, voice-profile and batch uploads accept audio and video files only",
    # owner-authored download hardening, no public issue
    "Model downloads keep every file inside the model cache",
    # owner-authored entries without a public reference
    "Remote workers open only the files a task sends as inputs",
    "Remote worker registration signs a fresh single-use challenge",
    "The desktop app attaches only to a local backend that identifies itself",
    "Dubbing and batch jobs accept only plain language codes",
    "Saved voice, gallery and call-recording files are read only from inside",
    "Engine install folders can no longer be set to a typed path",
)

_HEADING = re.compile(r"^## \[([^\]]+)\](?:\s*[—–-]\s*(.*))?$")
_REF_AT_END = re.compile(r"\(#\d+(?:,\s*#\d+)*\)\s*$")
_CREDIT = re.compile(r"— thanks @\w[\w-]*")


def _section_date(suffix):
    if not suffix:
        return None
    try:
        return datetime.date.fromisoformat(suffix.strip())
    except ValueError:
        return None


def lint_changelog(text):
    """Return a list of violation strings (empty = clean)."""
    violations = []

    # Split into version sections, newest first; stop at the first release
    # dated before the restyle epoch.
    sections = []  # (heading_line, lineno, body_lines)
    current = None
    for lineno, line in enumerate(text.splitlines(), start=1):
        m = _HEADING.match(line)
        if m:
            version, suffix = m.group(1), m.group(2)
            date = _section_date(suffix)
            if version != "Unreleased" and date is not None and date < _STYLE_EPOCH:
                current = None  # grandfathered — and everything below it too
                break
            current = (line, lineno, [])
            sections.append(current)
        elif current is not None:
            current[2].append((lineno, line))

    for heading, _, body in sections:
        subsection = None  # current "### X" title, None = section preamble
        has_subsections = any(ln.startswith("### ") for _, ln in body)
        has_highlights = any(ln.strip() == "**Highlights**" for _, ln in body)
        if has_subsections and not has_highlights:
            violations.append(
                f"{heading}: entry subsections exist but no `**Highlights**` "
                f"block — quiet style opens with a short Highlights bullet "
                f"list ({_RULE})."
            )
        # The presentation rule starts with v0.5.4. Published history and the
        # growing Unreleased section retain their existing cardinality.
        version = _HEADING.match(heading).group(1)
        version_parts = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", version)
        if has_highlights and version_parts and tuple(map(int, version_parts.groups())) >= (0, 5, 4):
            in_highlights = False
            highlight_count = 0
            for _, line in body:
                if line.strip() == "**Highlights**":
                    in_highlights = True
                elif in_highlights and line.startswith("### "):
                    break
                elif in_highlights and line.startswith("- "):
                    highlight_count += 1
            if not 3 <= highlight_count <= 5:
                violations.append(
                    f"{heading}: expected 3–5 Highlight bullets, found {highlight_count}."
                )
        last_bullet = None  # (lineno, text) of the most recent bullet
        for lineno, line in body:
            if line.startswith("### "):
                subsection = line[4:].strip()
                last_bullet = None
                continue
            if not line.strip():
                continue
            if line.startswith("- "):
                last_bullet = (lineno, line)
                if len(line) > _MAX_ENTRY_CHARS:
                    violations.append(
                        f"line {lineno}: entry runs {len(line)} chars — "
                        f"one-liners only, max ~{_MAX_ENTRY_CHARS} "
                        f"({_RULE}): {line[:120]}…"
                    )
                if (
                    (subsection in _REF_REQUIRED_SECTIONS
                     or (heading == "## [Unreleased]" and subsection == "CI"))
                    and not _REF_AT_END.search(line)
                    and not _CREDIT.search(line)
                    and not any(a in line for a in _REF_ALLOWLIST)
                ):
                    violations.append(
                        f"line {lineno} (### {subsection}): entry has neither "
                        f"a trailing `(#N)` ref nor a `— thanks @user!` credit "
                        f"({_RULE}; owner-authored infra lines may instead be "
                        f"added to _REF_ALLOWLIST in "
                        f"tests/test_changelog_style.py): {line}"
                    )
                continue
            # Non-blank, non-bullet, non-heading line.
            if line[0] in " \t":
                # Indented continuation — a wrapped multi-line bullet.
                if last_bullet is not None:
                    violations.append(
                        f"line {lineno}: continuation of the entry on line "
                        f"{last_bullet[0]} — entries must be a SINGLE line, "
                        f"no wrapped bullets ({_RULE}): {line.strip()[:120]}"
                    )
                    last_bullet = None  # report each wrapped bullet once
                continue
            if subsection is not None:
                # Prose paragraph inside an entry subsection — the old
                # bold-lead paragraph style.
                violations.append(
                    f"line {lineno} (### {subsection}): prose paragraph "
                    f"between entries — quiet style is one-liner bullets "
                    f"only ({_RULE}): {line[:120]}"
                )
            # Preamble prose before the first ### (release intro) is allowed.
    return violations


def test_repo_changelog_is_quiet_style():
    with open(_REPO_CHANGELOG, encoding="utf-8") as fh:
        violations = lint_changelog(fh.read())
    assert not violations, (
        "CHANGELOG.md violates the quiet release-notes style:\n  "
        + "\n  ".join(violations)
    )


# ── linter self-tests: each rule must actually fire ──────────────────────────


def _duplicate_versions(text):
    """Versions with more than one section, read by the app's own parser
    (core.changelog), which strips whitespace and a leading "v" and skips
    headings such as [Unreleased], so `[v0.5.2]` and `[0.5.2]` are one version."""
    from core.changelog import parse_changelog

    versions = [r["version"] for r in parse_changelog(text, limit_versions=10**6)]
    return sorted({v for v in versions if versions.count(v) > 1})


def test_every_version_has_one_section():
    """release.yml publishes the FIRST `## [X.Y.Z]` section as the release
    body and stops at the next heading, so a second section for the same
    version silently drops out of the notes (v0.5.2 was prepared twice)."""
    with open(_REPO_CHANGELOG, encoding="utf-8") as fh:
        dupes = _duplicate_versions(fh.read())
    assert not dupes, f"CHANGELOG.md has more than one section for: {dupes}"


def test_duplicate_versions_are_found_however_the_heading_is_written():
    text = (
        "# Changelog\n\n## [Unreleased]\n\n## [v0.5.2] — 2026-09-10\n\n- a (#1)\n\n"
        "## [ 0.5.2 ] — 2026-09-02\n\n- b (#2)\n\n## [0.5.1] — 2026-08-28\n\n- c (#3)\n"
    )
    assert _duplicate_versions(text) == ["0.5.2"]


_GOOD = """# Changelog

## [Unreleased]

**Highlights**

- Something plain and short

### Added

- A neat feature, one line, with its ref (#123)
- Community contribution — thanks @someone! (#124)
- First-run consent question for the existing opt-in analytics (allowlisted)

### Changed

- Housekeeping line, refs not required here

### Fixed

- A bug squashed (#125, #126)

## [0.9.9] — 2026-01-01

### Added

- **Old bold-lead style.** Grandfathered: this section predates the restyle,
  wrapped lines and all. No ref, no credit, no Highlights.
"""


def test_linter_accepts_quiet_sample_and_grandfathers_old_sections():
    assert lint_changelog(_GOOD) == []


def test_linter_flags_missing_highlights():
    bad = "## [Unreleased]\n\n### Fixed\n\n- A bug (#1)\n"
    v = lint_changelog(bad)
    assert len(v) == 1 and "**Highlights**" in v[0]


def test_linter_flags_wrapped_multiline_entry():
    bad = (
        "## [Unreleased]\n\n**Highlights**\n\n- Hi\n\n### Fixed\n\n"
        "- A bug whose description carries its ref (#1)\n"
        "  but wraps onto a second indented line anyway\n"
    )
    v = lint_changelog(bad)
    assert len(v) == 1 and "SINGLE line" in v[0]


def test_linter_flags_overlong_entry():
    bad = (
        "## [Unreleased]\n\n**Highlights**\n\n- Hi\n\n### Fixed\n\n- "
        + "x" * _MAX_ENTRY_CHARS
        + " (#1)\n"
    )
    v = lint_changelog(bad)
    assert len(v) == 1 and "one-liners only" in v[0]


def test_linter_flags_paragraph_between_entries():
    bad = (
        "## [Unreleased]\n\n**Highlights**\n\n- Hi\n\n### Added\n\n"
        "- Fine entry (#1)\n\nA bold-lead paragraph explaining at length.\n"
    )
    v = lint_changelog(bad)
    assert len(v) == 1 and "prose paragraph" in v[0]


def test_linter_flags_missing_ref_only_where_required():
    bad = (
        "## [Unreleased]\n\n**Highlights**\n\n- Hi\n\n"
        "### Changed\n\n- No ref needed here\n\n"
        "### Fixed\n\n- Fixed something with no ref\n"
    )
    v = lint_changelog(bad)
    assert len(v) == 1 and "(#N)" in v[0] and "Fixed something" in v[0]


def test_linter_scopes_by_date_not_position():
    """A post-epoch release is linted; the first pre-epoch one ends the scope."""
    bad = (
        "## [1.0.1] — 2026-08-01\n\n### Fixed\n\n- New-era entry with no ref\n\n"
        "## [1.0.0] — 2026-07-01\n\n### Fixed\n\n- Old-era entry with no ref\n"
    )
    v = lint_changelog(bad)
    assert len(v) == 2  # missing Highlights + missing ref, 1.0.1 only
    assert all("New-era" in x or "Highlights" in x for x in v)
    assert not any("Old-era" in x for x in v)


def test_unreleased_ci_entries_require_a_reference():
    text = "## [Unreleased]\n\n**Highlights**\n\n- Summary\n\n### CI\n\n- Packaging fix\n"
    assert any("(#N)" in error for error in lint_changelog(text))
    assert lint_changelog(text.replace("- Packaging fix", "- Packaging fix (#2157)")) == []


def test_tagged_release_highlights_cardinality_ignores_the_introduction():
    """New releases enforce 3–5 Highlights without counting intro bullets."""
    for count in range(7):
        text = (
            "## [0.5.4] — 2026-09-17\n\nRelease introduction.\n\n"
            "- [Download](https://example.com/app)\n\n"
            "**Highlights**\n\n"
            + "- A user-visible improvement\n" * count
            + "\n### Fixed\n\n- A regression fixed (#2179)\n"
        )
        violations = lint_changelog(text)
        if 3 <= count <= 5:
            assert violations == []
        else:
            assert any("3–5 Highlight bullets" in item for item in violations)


def test_highlight_cardinality_preserves_published_and_work_in_progress_notes():
    """The new tagged-release limit must not rewrite historical or draft notes."""
    for heading in ("## [0.5.3] — 2026-09-17", "## [Unreleased]"):
        text = heading + "\n\n**Highlights**\n\n- One item\n\n### Fixed\n\n- Fix (#1)\n"
        assert lint_changelog(text) == []
