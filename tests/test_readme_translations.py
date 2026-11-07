"""The translated READMEs live in docs/readme/; their relative links must still resolve."""
from __future__ import annotations

import html
import re
import shlex
from collections import Counter
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[1]
_TRANSLATIONS = sorted((_ROOT / "docs" / "readme").glob("README_*.md"))
_TARGET = re.compile(r'(?:src(?:set)?="|href="|\]\()([^")\s]+)')
_FENCE = re.compile(r"^```([^\n]*)\n(.*?)^```[ \t]*$", re.MULTILINE | re.DOTALL)
_EXPECTED = {
    f"README_{code}.md"
    for code in ("AR", "CN", "DE", "ES", "FR", "HI", "ID", "IT", "JA", "KO",
                 "NL", "PL", "PT", "RU", "TH", "TR", "TW", "UK", "VI")
}


def test_translations_are_present_and_the_root_readme_links_to_them():
    assert _EXPECTED <= {p.name for p in _TRANSLATIONS}, "English plus 19 translations must be available"
    root = (_ROOT / "README.md").read_text(encoding="utf-8")
    for path in _TRANSLATIONS:
        assert f'href="docs/readme/{path.name}"' in root
    assert not list(_ROOT.glob("README_*.md")), "translations belong in docs/readme/, not the repo root"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_every_translation_links_to_english_and_every_other_translation(path):
    """The language switcher must stay complete when a translation is added."""
    text = path.read_text(encoding="utf-8")
    expected = {"../../README.md"} | {p.name for p in _TRANSLATIONS if p != path}
    missing = sorted(t for t in expected if f'href="{t}"' not in text)
    assert not missing, f"{path.name} language switcher is missing: {missing}"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_every_relative_link_in_a_translation_resolves(path):
    missing = []
    for target in _TARGET.findall(path.read_text(encoding="utf-8")):
        if re.match(r"(https?:|mailto:|#|//|data:)", target):
            continue
        if not (path.parent / target.split("#")[0]).resolve().exists():
            missing.append(target)
    assert not missing, f"{path.name} links to files that do not exist: {missing}"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_translations_preserve_install_commands(path):
    """Translated shell comments are allowed; commands, flags and URLs stay intact."""
    source = _FENCE.findall((_ROOT / "README.md").read_text(encoding="utf-8"))
    translated = _FENCE.findall(path.read_text(encoding="utf-8"))
    assert len(translated) == len(source), f"{path.name} omits a command or copyable example"
    for (source_kind, original), (kind, localized) in zip(source, translated):
        assert kind == source_kind
        if kind in {"sh", "bash", "powershell"}:
            assert shlex.split(localized, comments=True) == shlex.split(original, comments=True), path.name
        else:
            assert localized == original, f"{path.name} changes a copyable URL or agent prompt"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_translations_cover_every_readme_section(path):
    source = _FENCE.sub("", (_ROOT / "README.md").read_text(encoding="utf-8"))
    translated = _FENCE.sub("", path.read_text(encoding="utf-8"))
    def levels(text):
        return re.findall(r"^(#{1,6})[ \t]+[^\s#]", text, re.MULTILINE)
    assert levels(translated) == levels(source), f"{path.name} omits or rearranges README sections"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_translations_preserve_inline_commands(path):
    source = _FENCE.sub("", (_ROOT / "README.md").read_text(encoding="utf-8"))
    translated = _FENCE.sub("", path.read_text(encoding="utf-8"))
    def inline(text):
        return Counter(re.findall(r"`([^`]+)`", text))
    assert inline(translated) == inline(source), f"{path.name} changes or omits an inline command"
    for skill in ("voicestudio", "voicestudio-maintainer"):
        assert f"**{skill}**" in translated, f"{path.name} translates a skill identifier"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_translations_preserve_documentation_links(path):
    def links(file):
        text = _FENCE.sub("", file.read_text(encoding="utf-8"))
        result = Counter()
        for target in re.findall(r"\[[^\]\n]+\]\(([^)\s]+)\)", text):
            if re.match(r"(https?:|mailto:|#)", target):
                result[target] += 1
            else:
                name, separator, anchor = target.partition("#")
                relative = (file.parent / name).resolve().relative_to(_ROOT).as_posix()
                result[relative + separator + anchor] += 1
        return result

    assert links(path) == links(_ROOT / "README.md"), f"{path.name} omits or changes a documentation link"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
def test_translation_navigation_anchors_resolve(path):
    text = _FENCE.sub("", path.read_text(encoding="utf-8"))
    anchors = set(re.findall(r'\bid="([^"]+)"', text))
    for heading in re.findall(r"^#{1,6}\s+(.+)$", text, re.MULTILINE):
        heading = html.unescape(re.sub(r"<[^>]+>|[`*]", "", heading)).lower()
        anchors.add(re.sub(r"[^\w\s-]", "", heading).strip().replace(" ", "-"))
    missing = [target for target in _TARGET.findall(text) if target.startswith("#") and target[1:] not in anchors]
    assert not missing, f"{path.name} has broken navigation anchors: {missing}"


@pytest.mark.parametrize("path", _TRANSLATIONS, ids=lambda p: p.name)
@pytest.mark.parametrize("anchor, english", [("what-you-can-do", "What you can do"), ("platforms", "Platforms")])
def test_translated_section_anchors_stay_on_their_sections(path, anchor, english):
    """Adding a section must not leave a saved link pointing at the wrong heading."""
    source = _FENCE.sub("", (_ROOT / "README.md").read_text(encoding="utf-8"))
    translated = _FENCE.sub("", path.read_text(encoding="utf-8"))
    marker = f'<a id="{anchor}"></a>'
    match = re.search(re.escape(marker) + r"\s*\n(?=## )", translated)
    assert match, f"{path.name}: #{anchor} must sit directly above a section heading"
    preceding = len(re.findall(r"^## ", translated[:match.start()], re.MULTILINE))
    expected = len(re.findall(r"^## ", source[:source.index(f"## {english}\n")], re.MULTILINE))
    assert preceding == expected, f"{path.name}: #{anchor} is on the wrong section"
