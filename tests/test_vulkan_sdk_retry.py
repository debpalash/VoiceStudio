"""The release SDK install retries transient failures without hiding a final failure."""
from pathlib import Path

import yaml


def test_windows_vulkan_sdk_install_retries_and_fails_after_last_attempt():
    root = Path(__file__).resolve().parents[1]
    workflow = yaml.safe_load((root / ".github/workflows/build-omnivoice-tts.yml").read_text(encoding="utf-8"))
    steps = workflow["jobs"]["build-omnivoice-tts"]["steps"]
    attempts = [step for step in steps if "install-vulkan-sdk-action@" in step.get("uses", "")]
    assert len(attempts) == 3
    assert [step["id"] for step in attempts] == ["vulkan_sdk_1", "vulkan_sdk_2", "vulkan_sdk_3"]
    assert all(step.get("continue-on-error") is True for step in attempts[:2])
    assert attempts[2].get("continue-on-error", False) is False
    assert "steps.vulkan_sdk_1.outcome == 'failure'" in attempts[1]["if"]
    assert "steps.vulkan_sdk_2.outcome == 'failure'" in attempts[2]["if"]
    assert all(step["with"] == attempts[0]["with"] for step in attempts)
