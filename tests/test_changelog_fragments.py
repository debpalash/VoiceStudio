"""Changelog fragments (`changelog.d/`): lint every fragment and prove assembly."""

import os
import re
import sys

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import changelog_fragments as cf  # noqa: E402
from test_changelog_style import (  # noqa: E402
    _CREDIT, _MAX_ENTRY_CHARS, _REF_AT_END, lint_changelog,
)

SAMPLE = """# Changelog

## [Unreleased]

**Highlights**

- One highlight

### Changed

- Existing change (#1)

### Fixed

- Existing fix (#2)

## [0.5.7] — 2026-10-01

### Fixed

- Old (#3)
"""


def _write(tmp_path, name, text):
    (tmp_path / name).write_text(text, encoding="utf-8")


def test_every_repo_fragment_is_one_valid_line():
    problems = []
    for path in cf.fragment_files():
        entry = cf.entry_of(path)
        kind = cf.NAME.match(path.name).group("type")
        ident = re.match(r"\d*", cf.NAME.match(path.name).group("id")).group()
        if len(entry) > _MAX_ENTRY_CHARS:
            problems.append(f"{path.name}: longer than {_MAX_ENTRY_CHARS} characters")
        if kind in ("added", "fixed") and not (_REF_AT_END.search(entry) or _CREDIT.search(entry)):
            problems.append(f"{path.name}: needs a (#N) reference or a thanks credit")
        if ident and not re.search(rf"#{ident}\b", entry):
            problems.append(f"{path.name}: should reference its own PR (#{ident})")
    assert not problems, "\n".join(problems)


def test_stray_files_in_the_fragment_directory_are_named_correctly():
    allowed = {"README.md"}
    bad = [
        p.name for p in (cf.FRAGMENT_DIR).iterdir()
        if p.is_file() and p.name not in allowed and not cf.NAME.match(p.name)
    ]
    assert not bad, f"not <id>.<type>.md: {bad}"


def test_repo_fragments_fold_into_a_changelog_that_passes_the_style_lint():
    with open(os.path.join(ROOT, "CHANGELOG.md"), encoding="utf-8") as fh:
        folded = cf.apply_fragments(fh.read(), cf.read_fragments())
    assert not lint_changelog(folded)


def test_entries_land_under_their_heading_newest_first(tmp_path):
    _write(tmp_path, "10.fixed.md", "- Older fix (#10)\n")
    _write(tmp_path, "11.fixed.md", "Newer fix (#11)\n")
    out = cf.apply_fragments(SAMPLE, cf.read_fragments(tmp_path))
    fixed = out.split("### Fixed\n\n", 1)[1]
    assert fixed.startswith("- Newer fix (#11)\n- Older fix (#10)\n- Existing fix (#2)\n")


def test_missing_sections_are_created_in_canonical_order(tmp_path):
    _write(tmp_path, "20.added.md", "New thing (#20)\n")
    _write(tmp_path, "21.ci.md", "Faster runs (#21)\n")
    out = cf.apply_fragments(SAMPLE, cf.read_fragments(tmp_path))
    unreleased = out.split("## [0.5.7]")[0]
    order = re.findall(r"^### (\w+)", unreleased, re.M)
    assert order == ["Changed", "Added", "Fixed", "CI"]
    assert "## [0.5.7] — 2026-10-01\n\n### Fixed\n\n- Old (#3)" in out


def test_assembly_is_idempotent_and_leaves_released_sections_alone(tmp_path):
    _write(tmp_path, "30.changed.md", "Tweaked (#30)\n")
    frags = cf.read_fragments(tmp_path)
    once = cf.apply_fragments(SAMPLE, frags)
    assert cf.apply_fragments(once, frags) == once
    assert once.endswith("- Old (#3)\n")


def test_multi_line_fragment_is_rejected(tmp_path):
    _write(tmp_path, "40.fixed.md", "One (#40)\nTwo\n")
    with pytest.raises(ValueError, match="exactly one line"):
        cf.read_fragments(tmp_path)


def test_removal_keeps_the_readme(tmp_path):
    _write(tmp_path, "README.md", "doc")
    _write(tmp_path, "50.docs.md", "Doc (#50)\n")
    assert cf.remove_fragments(tmp_path) == ["50.docs.md"]
    assert (tmp_path / "README.md").exists()


def test_a_bullet_elsewhere_does_not_hide_a_fragment(tmp_path):
    text = SAMPLE.replace("- One highlight", "- Same words (#9)")
    _write(tmp_path, "9.fixed.md", "Same words (#9)\n")
    out = cf.apply_fragments(text, cf.read_fragments(tmp_path))
    assert out.count("Same words (#9)") == 2


def test_suffixed_fragment_names_keep_their_pr_number():
    assert re.match(r"\d*", cf.NAME.match("3263-b.docs.md").group("id")).group() == "3263"
