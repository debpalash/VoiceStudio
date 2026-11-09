"""Local tooling and CI run the same Node major (single source: .nvmrc).

Node 26 on a contributor machine failed ~15 Electron tests that pass on CI's
Node 22 (error messages from rejected promises differ), which reads as a real
regression. `.nvmrc` lets nvm/fnm/volta pick 22; this test keeps every
workflow's `node-version` on the same major.
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_workflows_use_the_nvmrc_node_major():
    pinned = (ROOT / ".nvmrc").read_text(encoding="utf-8").strip()
    assert re.fullmatch(r"\d+", pinned), ".nvmrc must hold a bare Node major"
    drift = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.yml")):
        for number, line in enumerate(workflow.read_text(encoding="utf-8").splitlines(), 1):
            match = re.search(r"node-version:\s*['\"]?([0-9]+)", line)
            if match and match.group(1) != pinned:
                drift.append(f"{workflow.name}:{number} node {match.group(1)}")
    assert not drift, f"Workflows must use Node {pinned} like .nvmrc: {drift}"
