"""Issue templates may only apply labels documented in docs/agents/triage-labels.md.

The bug and install forms once applied a bare ``triage`` label that no triage
skill or maintainer query used, so new reports never reached the
``needs-triage`` queue.
"""

from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]


def test_issue_template_labels_are_documented():
    documented = set(
        re.findall(r"`([^`]+)`", (ROOT / "docs/agents/triage-labels.md").read_text(encoding="utf-8"))
    )
    undocumented = {}
    for template in sorted((ROOT / ".github/ISSUE_TEMPLATE").glob("*.yml")):
        labels = (yaml.safe_load(template.read_text(encoding="utf-8")) or {}).get("labels") or []
        if isinstance(labels, str):
            labels = [label.strip() for label in labels.split(",")]
        missing = [label for label in labels if label not in documented]
        if missing:
            undocumented[template.name] = missing
    assert not undocumented, (
        f"Issue templates apply labels missing from docs/agents/triage-labels.md: {undocumented}"
    )
    assert "needs-triage" in documented
