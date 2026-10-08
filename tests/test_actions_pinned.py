"""Every remote GitHub Action must be pinned to a full commit SHA.

A tag (``@v4``) or branch (``@stable``) is a mutable pointer: whoever controls
the action's repository can move it, and the next CI run executes the new code
with this repo's secrets (Docker Hub / GHCR push tokens, release upload). A
40-hex commit SHA is immutable. The trailing ``# vX.Y.Z`` comment keeps the pin
human-readable and lets Dependabot (``.github/dependabot.yml``) bump it.

Local (``./``) actions and ``docker://`` images are exempt.
"""
from __future__ import annotations

import collections
import pathlib
import re

import yaml

_ROOT = pathlib.Path(__file__).resolve().parent.parent
_GITHUB = _ROOT / ".github"
_SHA = re.compile(r"^[^@\s]+@[0-9a-f]{40}$")


def _files() -> list[pathlib.Path]:
    found = sorted((_GITHUB / "workflows").glob("*.y*ml"))
    found += sorted(_GITHUB.rglob("action.y*ml"))  # composite actions
    return found


def _uses(doc: dict) -> list[str]:
    """Collect ``uses:`` values from jobs/steps (workflows) and runs.steps (composite)."""
    out: list[str] = []
    steps: list = []
    for job in (doc.get("jobs") or {}).values():
        if isinstance(job, dict):
            if "uses" in job:  # reusable workflow call
                out.append(str(job["uses"]))
            steps += job.get("steps") or []
    steps += (doc.get("runs") or {}).get("steps") or []
    out += [str(s["uses"]) for s in steps if isinstance(s, dict) and "uses" in s]
    return out


def _remote(ref: str) -> bool:
    return not ref.startswith(("./", "docker://"))


def _collect() -> dict[pathlib.Path, list[str]]:
    result = {}
    for path in _files():
        doc = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        result[path] = [u for u in _uses(doc) if _remote(u)]
    return result


def test_scanner_finds_actions():
    # Guard against a vacuous pass if the layout or parsing ever changes.
    assert sum(len(v) for v in _collect().values()) > 0


def test_remote_actions_pinned_to_commit_sha():
    bad = [
        f"{p.relative_to(_ROOT)}: {u}"
        for p, refs in _collect().items()
        for u in refs
        if not _SHA.match(u)
    ]
    assert not bad, (
        "Pin these actions to a full 40-char commit SHA with a trailing "
        "'# vX.Y.Z' comment (resolve via `git ls-remote --tags "
        "https://github.com/<owner>/<repo>` and use the peeled ^{} SHA):\n"
        + "\n".join(bad)
    )


def _missing_version_comments(text: str, refs: list[str]) -> list[str]:
    """References whose ``uses:`` lines lack a trailing comment.

    Matches plain, single- and double-quoted scalars. Every collected
    reference must be found on as many lines as it is used, so a form this
    scan cannot read fails instead of passing unchecked.
    """
    bad = []
    for u, count in collections.Counter(refs).items():
        lines = re.findall(
            rf"""^[ \t]*(?:-[ \t]+)?uses:[ \t]*(["']?){re.escape(u)}\1([^\n]*)""", text, re.MULTILINE
        )
        if len(lines) < count or any(not re.match(r"\s+#\s*\S", rest) for _, rest in lines):
            bad.append(u)
    return bad


def test_version_comment_scan_reads_quoted_scalars():
    sha = "actions/checkout@" + "0" * 40
    for quote in ("", "'", '"'):
        line = f"      - uses: {quote}{sha}{quote}"
        assert _missing_version_comments(line + " # v5.0.0\n", [sha]) == []
        assert _missing_version_comments(line + "\n", [sha]) == [sha]
    assert _missing_version_comments("", [sha]) == [sha]  # unreadable form fails closed


def test_pins_carry_version_comment():
    bad = [
        f"{path.relative_to(_ROOT)}: {u}"
        for path, refs in _collect().items()
        for u in _missing_version_comments(path.read_text(encoding="utf-8"), refs)
    ]
    assert not bad, "Add a '# vX.Y.Z' comment after each SHA pin:\n" + "\n".join(bad)


def test_dependabot_tracks_github_actions():
    cfg = yaml.safe_load((_GITHUB / "dependabot.yml").read_text(encoding="utf-8"))
    ecosystems = {u.get("package-ecosystem") for u in cfg.get("updates", [])}
    assert "github-actions" in ecosystems
