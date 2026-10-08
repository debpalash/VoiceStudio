"""#2365 — first-run setup must gate Intel Macs before any download.

PyTorch ships no macOS x86_64 wheels, so the dependency set can never
resolve there (#889). The preflight previously had no platform gate, so
``ok`` stayed true and setup burned a multi-GB ``uv sync`` before dying
on the resolver error. A ``platform`` fail check now blocks Continue on
the wizard's first step, where the remote-backend guidance is shown.
Local inference stays unsupported — this only moves the failure earlier.
"""

from api.routers.setup import wizard


def _platform_check(monkeypatch, platform_machine: str, sys_platform: str) -> dict:
    # Keep the preflight hermetic: stub the probes that hit the network or
    # auto-acquire media tools, so each platform assertion stays fast and offline.
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass",
        "detail": "stubbed", "fix": None, "mirror_reachable": True,
    })
    import services.media_tools as media_tools
    monkeypatch.setattr(media_tools, "summary", lambda auto_acquire=True: None)
    monkeypatch.setattr(wizard._platform, "machine", lambda: platform_machine)
    monkeypatch.setattr(wizard.sys, "platform", sys_platform)
    resp = wizard.preflight()
    checks = resp["checks"] if isinstance(resp, dict) else resp.checks
    for c in checks:
        c = c if isinstance(c, dict) else c.model_dump()
        if c["id"] == "platform":
            return c, resp["ok"] if isinstance(resp, dict) else resp.ok
    raise AssertionError("no platform check in preflight response")


def test_intel_mac_fails_preflight_before_any_download(monkeypatch):
    """The #2365 report: darwin + x86_64 must fail with remote-backend guidance."""
    check, ok = _platform_check(monkeypatch, "x86_64", "darwin")
    assert check["status"] == "fail"
    assert ok is False
    assert "remote backend" in (check["fix"] or "")


def test_apple_silicon_has_no_platform_gate(monkeypatch):
    """Apple Silicon must not see the gate at all — no platform check appended."""
    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass",
        "detail": "stubbed", "fix": None, "mirror_reachable": True,
    })
    import services.media_tools as media_tools
    monkeypatch.setattr(media_tools, "summary", lambda auto_acquire=True: None)
    monkeypatch.setattr(wizard._platform, "machine", lambda: "arm64")
    monkeypatch.setattr(wizard.sys, "platform", "darwin")
    resp = wizard.preflight()
    checks = resp["checks"] if isinstance(resp, dict) else resp.checks
    ids = [(c if isinstance(c, dict) else c.model_dump())["id"] for c in checks]
    assert "platform" not in ids
