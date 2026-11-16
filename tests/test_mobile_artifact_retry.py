"""Transient artifact upload failure must not require repeating native tests."""
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]
UPLOADS = (
    ("android", "android-reports", "failure() && steps.pass.outputs.hit != 'true'"),
    ("ios", "ios-screenshots", "always() && steps.pass.outputs.hit != 'true'"),
    ("ios", "VoiceStudio-ios-unsigned", "steps.pass.outputs.hit != 'true'"),
    ("android-screenshots", "android-play-screenshots", "always()"),
)


@pytest.mark.parametrize("job,artifact,original_condition", UPLOADS)
def test_mobile_upload_has_one_bounded_terminal_retry(
    job: str, artifact: str, original_condition: str,
) -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"][job]["steps"]
    uploads = [step for step in steps if step.get("with", {}).get("name") == artifact]
    assert len(uploads) == 2, f"{artifact}: exactly one retry is required"
    first, retry = uploads
    assert first["if"] == original_condition
    assert first["continue-on-error"] is True
    assert first["id"]
    expression = "!cancelled() && steps." + first["id"] + ".outcome == 'failure'"
    condition = ("steps.pass.outputs.hit != 'true' && " + expression
                 if any(step.get("id") == "pass" for step in steps) else "${{ " + expression + " }}")
    # Outcome survives continue-on-error; conclusion would hide the failure.
    assert retry["if"] == condition
    assert retry.get("continue-on-error", False) is False
    assert retry["uses"] == first["uses"]
    assert retry["with"] == first["with"], "Retry must retain name, paths and collision policy"
    assert "overwrite" not in first["with"]
    index = steps.index(first)
    backoff = steps[index + 1]
    assert backoff["if"] == condition
    assert backoff["run"].strip() == "sleep 10"
    assert steps[index + 2] is retry
    assert backoff.get("continue-on-error", False) is False
    markers = [
        i for i, step in enumerate(steps)
        if step.get("uses", "").startswith("actions/cache/save@")
        and step.get("with", {}).get("key") == "${{ steps.pass.outputs.key }}"
    ]
    if markers:
        assert markers == [len(steps) - 1], "Pass marker stays last, after terminal upload"
        assert steps[-1]["if"] == "steps.pass.outputs.hit != 'true' && steps.pass.outputs.record == 'true'"


def test_every_mobile_artifact_upload_belongs_to_a_bounded_pair() -> None:
    workflow = yaml.safe_load((ROOT / ".github/workflows/mobile.yml").read_text(encoding="utf-8"))
    actual = [
        (job, step["with"]["name"])
        for job, config in workflow["jobs"].items()
        for step in config.get("steps", [])
        if step.get("uses", "").startswith("actions/upload-artifact@")
    ]
    expected = [(job, artifact) for job, artifact, _ in UPLOADS for _attempt in range(2)]
    assert sorted(actual) == sorted(expected)
