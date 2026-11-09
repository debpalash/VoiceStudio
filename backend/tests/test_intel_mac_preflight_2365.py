import pytest

from api.routers.setup import wizard


@pytest.fixture
def run_macos_preflight(monkeypatch):
    monkeypatch.setattr(wizard.sys, "platform", "darwin")
    monkeypatch.setattr(wizard._platform, "platform", lambda terse=True: "macOS-test")
    monkeypatch.setattr(wizard, "_ram_gb", lambda: 24.25)
    monkeypatch.setattr(wizard, "_disk_free_gb", lambda _cache: 81.75)
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": "none",
        "backend": "cpu",
        "available": False,
        "driver": "",
        "device_name": "Test CPU",
        "notes": [],
    })
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network",
        "label": "Network",
        "status": "pass",
        "detail": "stubbed",
        "fix": None,
    })
    monkeypatch.setattr("services.media_tools.summary", lambda **_options: {"stub": True})

    def invoke(machine):
        monkeypatch.setattr(wizard._platform, "machine", lambda: machine)
        result = wizard.preflight()
        return result.model_dump() if hasattr(result, "model_dump") else result

    return invoke


def test_x86_macos_returns_a_terminal_platform_result(run_macos_preflight):
    payload = run_macos_preflight("x86_64")
    by_id = {item["id"]: item for item in payload["checks"]}

    assert payload["media_tools"] is None
    assert payload["ok"] is False
    assert by_id["platform"]["status"] == "fail"
    assert "remote backend" in by_id["platform"]["fix"]


def test_arm_macos_continues_without_a_platform_failure(run_macos_preflight):
    payload = run_macos_preflight("arm64")
    check_ids = [item["id"] for item in payload["checks"]]

    assert "platform" not in check_ids
    assert payload["media_tools"] == {"stub": True}
