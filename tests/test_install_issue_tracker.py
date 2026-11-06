"""Keep docs/install/issue-tracker.md consistent as reports and fixes change."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TRACKER = ROOT / "docs" / "install" / "issue-tracker.md"
GUIDE = ROOT / "docs" / "install" / "troubleshooting.md"
STATUSES = {
    "Open",
    "Partly fixed",
    "Fixed on main",
    "Fixed in release",
    "Declined",
    "Closed, cause unknown",
}
NEEDS_FIX_LINK = {"Partly fixed", "Fixed on main", "Fixed in release"}
ID_PATTERN = re.compile(r"`(INST-([A-Z]+)-\d{3})`")


def _table(heading: str) -> list[list[str]]:
    """Return the body rows of the first table under a ``##`` heading."""
    section = TRACKER.read_text(encoding="utf-8").split(f"\n## {heading}\n", 1)[1]
    section = section.split("\n## ", 1)[0]
    lines = [line for line in section.splitlines() if line.startswith("|")]
    return [[cell.strip() for cell in line.strip("|").split(" | ")] for line in lines[2:]]


def _ranked() -> list[dict[str, str]]:
    columns = ("rank", "id", "severity", "problem", "status", "fix", "next", "help", "reports")
    rows = _table("Ranked list")
    assert all(len(row) == len(columns) for row in rows), "ranked rows must keep every column"
    return [dict(zip(columns, row)) for row in rows]


def _reports() -> dict[str, list[int]]:
    return {
        row[0].strip("`"): [int(number) for number in re.findall(r"#(\d+)(?=\]|,|$)", row[1])]
        for row in _table("Reports for each issue")
    }


def test_ids_are_unique_and_use_a_known_category():
    categories = {row[0].strip("`") for row in _table("Categories")}
    ids = [ID_PATTERN.fullmatch(row["id"]) for row in _ranked()]
    assert all(ids), "every ID must look like INST-<category>-<number>"
    names = [match.group(1) for match in ids]
    assert len(names) == len(set(names))
    assert {match.group(2) for match in ids} <= categories


def test_list_is_ranked_by_severity():
    rows = _ranked()
    assert [int(row["rank"]) for row in rows] == list(range(1, len(rows) + 1))
    severities = [row["severity"] for row in rows]
    assert severities == sorted(severities)
    assert set(severities) <= {"P0", "P1", "P2", "P3"}


def test_statuses_are_known_and_summary_matches():
    rows = _ranked()
    assert {row["status"] for row in rows} <= STATUSES
    summary = {row[0]: int(row[1]) for row in _table("Summary")}
    assert summary == {
        status: sum(row["status"] == status for row in rows) for status in STATUSES
    }


def test_fixed_issues_link_a_pull_request():
    missing = [
        row["id"]
        for row in _ranked()
        if row["status"] in NEEDS_FIX_LINK and "/pull/" not in row["fix"]
    ]
    assert not missing


def test_issues_without_a_fix_say_what_happens_next():
    missing = [
        row["id"]
        for row in _ranked()
        if row["status"] in {"Open", "Partly fixed", "Declined"} and not row["next"]
    ]
    assert not missing


def test_every_report_belongs_to_exactly_one_issue():
    reports = _reports()
    numbers = [number for group in reports.values() for number in group]
    assert len(numbers) == len(set(numbers))
    rows = _ranked()
    assert set(reports) == {row["id"].strip("`") for row in rows}
    for row in rows:
        assert int(row["reports"]) == len(reports[row["id"].strip("`")]), row["id"]


def test_help_links_resolve():
    text = TRACKER.read_text(encoding="utf-8")
    guide = GUIDE.read_text(encoding="utf-8")
    anchors = set()
    for heading in re.findall(r"^#{2,4} (.+)$", guide, re.MULTILINE):
        slug = re.sub(r"[^\w\- ]", "", heading.replace("`", "").strip().lower())
        anchors.add(slug.replace(" ", "-"))
    for path, anchor in re.findall(r"\]\(((?:\.\./)?[\w-]+\.md)(?:#([\w-]+))?\)", text):
        assert (TRACKER.parent / path).is_file(), path
        if anchor:
            assert path == "troubleshooting.md" and anchor in anchors, anchor
