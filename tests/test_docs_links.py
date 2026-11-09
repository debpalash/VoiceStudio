"""Relative links in the Markdown docs must point at a file and heading that exist.

Renames (OmniVoice -> VoiceStudio, moved guides, reworded headings) silently
break in-repo links; this keeps every relative link and anchor resolvable.

The scan covers the repository-root pages too (#2903). ``CHANGELOG.md`` and
``SPONSORS.md`` sat outside it, so links there kept pointing at files a later
release deleted while the covered pages stayed clean. A released changelog
entry is a historical record and is never repointed at a different file that
happens to exist today — name the removed path as inline code instead.
"""
import re
from collections import Counter
from functools import cache
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]
_DOCS = sorted(
    {
        *(_ROOT / "docs").rglob("*.md"),
        *(_ROOT / ".github").glob("*.md"),
        _ROOT / "README.md",
        _ROOT / "CHANGELOG.md",
        _ROOT / "SPONSORS.md",
    }
)
_LINK = re.compile(r"(?<!!)\[[^\]]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_HEADING = re.compile(r"#{1,6}\s+(.*?)\s*#*\s*$")
_HTML_ANCHOR = re.compile(r"<a\s+(?:id|name)=\"([^\"]+)\"")


def _prose_lines(path: Path):
    fenced = False
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        elif not fenced:
            yield number, line


def _slug(heading: str) -> str:
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"<[^>]+>|`", "", heading).strip().lower()
    return re.sub(r"[^\w\- ]", "", text).replace(" ", "-")


@cache
def _anchors(path: Path) -> frozenset[str]:
    seen: Counter[str] = Counter()
    anchors = set()
    for _, line in _prose_lines(path):
        if match := _HEADING.match(line):
            slug = _slug(match.group(1))
            anchors.add(slug if not seen[slug] else f"{slug}-{seen[slug]}")
            seen[slug] += 1
        anchors.update(_HTML_ANCHOR.findall(line))
    return frozenset(anchors)


def test_slug_matches_github():
    assert _slug("7. Fact-check: the pyvideotrans ↔ VoiceStudio integration") == (
        "7-fact-check-the-pyvideotrans--voicestudio-integration"
    )
    assert _slug("License & responsible use") == "license--responsible-use"


def test_scan_covers_the_repo_root_pages():
    """The root pages are in scope, so a link rotting there fails the suite.

    Without this, dropping a page from ``_DOCS`` silently stops checking it —
    which is how the #2903 links survived the sweep that fixed the rest.
    """
    covered = {doc.relative_to(_ROOT).as_posix() for doc in _DOCS}
    for page in ("README.md", "CHANGELOG.md", "SPONSORS.md"):
        assert page in covered, f"{page} is not scanned for broken links"


def test_relative_doc_links_resolve():
    broken = []
    for doc in _DOCS:
        for number, line in _prose_lines(doc):
            for target in _LINK.findall(line):
                if re.match(r"[a-zA-Z][a-zA-Z0-9+.-]*:", target):
                    continue  # external URL or mailto:
                path, _, fragment = target.partition("#")
                resolved = (doc.parent / path).resolve() if path else doc
                where = f"{doc.relative_to(_ROOT)}:{number} -> {target}"
                if not resolved.exists():
                    broken.append(f"{where} (missing file)")
                elif fragment and resolved.suffix == ".md" and fragment.lower() not in _anchors(resolved):
                    broken.append(f"{where} (missing heading)")
    assert not broken, "Broken relative doc links:\n" + "\n".join(broken)
