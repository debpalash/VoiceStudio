"""#1812 — an accelerated GPU with a caveat must name the RIGHT remedy.

`_check_gpu_routing` and the setup wizard's preflight both treated "accelerated
but with a routing_reason" as one case and printed the driver/architecture
remedy: "may fail at kernel launch — update drivers / reinstall torch for this
GPU architecture." A perfectly healthy CUDA card that merely has less free VRAM
than the engine wants also carries a reason, so a low-VRAM caveat told the user
to reinstall torch. That sends someone to rebuild a working toolchain over what
is really "close some other models first".

The two are now split on KERNEL_RISK_MARKER, which is the only marker that
means the kernels themselves may not launch.
"""
import pytest

from core import diagnose
from core.diagnose import WARN, OK
from core.device_caps import KERNEL_RISK_MARKER


def _verdict(monkeypatch, reason, status="accelerated"):
    from services import tts_backend

    monkeypatch.setattr(
        tts_backend,
        "gpu_routing_verdict",
        lambda: {
            "routing_status": status,
            "engine": "omnivoice",
            "effective_device": "cuda",
            "routing_reason": reason,
            "host_family": "cuda",
        },
    )


def test_kernel_risk_keeps_the_driver_remedy(monkeypatch):
    _verdict(monkeypatch, f"sm_120 is not in the build's archs — {KERNEL_RISK_MARKER}")
    check = diagnose._check_gpu_routing()
    assert check["status"] == WARN
    assert "update drivers" in check["hint"]


def test_low_vram_does_not_tell_the_user_to_reinstall_torch(monkeypatch):
    _verdict(monkeypatch, "6.0 GB free is below the 8.0 GB this engine wants")
    check = diagnose._check_gpu_routing()
    assert check["status"] == WARN
    assert "reinstall torch" not in check["hint"]
    assert "update drivers" not in check["hint"]
    assert "lighter engine" in check["hint"]


def test_a_clean_accelerated_host_stays_ok(monkeypatch):
    _verdict(monkeypatch, None)
    check = diagnose._check_gpu_routing()
    assert check["status"] == OK


@pytest.mark.parametrize(
    "reason,expect_driver_remedy",
    [
        (f"arch mismatch — {KERNEL_RISK_MARKER}", True),
        ("low free VRAM for this engine", False),
    ],
)
def test_preflight_routes_the_same_way_as_the_diagnostic(
    monkeypatch, reason, expect_driver_remedy
):
    # The wizard's preflight carries its own copy of this branch; the two must
    # not drift, or the same host gets different advice in Settings than in
    # first-run setup.
    from api.routers.setup import wizard
    from services import tts_backend

    monkeypatch.setattr(
        tts_backend,
        "gpu_routing_verdict",
        lambda: {
            "routing_status": "accelerated",
            "engine": "omnivoice",
            "effective_device": "cuda",
            "routing_reason": reason,
            "host_family": "cuda",
        },
    )
    rows = wizard.preflight()["checks"]
    row = next(r for r in rows if r["id"] == "gpu_routing")
    assert row["status"] == "warn"
    assert ("reinstall torch" in (row["fix"] or "")) is expect_driver_remedy


def test_kernel_mismatch_cpu_fallback_names_the_wheel_not_the_driver(monkeypatch):
    # #3067: the badge now falls back to CPU for a card the wheel has no
    # kernels for; the hint must not send the user to pick another engine or
    # update a driver that is fine.
    _verdict(
        monkeypatch,
        f"Quadro M5000M (sm_52) not in this torch build's archs — {KERNEL_RISK_MARKER}; running on CPU",
        status="cpu_fallback",
    )
    check = diagnose._check_gpu_routing()
    assert check["status"] == WARN
    assert "driver update will not help" in check["hint"]
    assert "Pick an engine" not in check["hint"]


def test_preflight_gpu_row_does_not_say_cuda_ready_without_kernels(monkeypatch):
    # #3067 / review: the hardware row must agree with the routing row.
    from api.routers.setup import wizard
    import services.media_tools as media_tools

    monkeypatch.setattr(wizard, "_network_check", lambda: {
        "id": "network", "label": "Network", "status": "pass",
        "detail": "stubbed", "fix": None, "mirror_reachable": True,
    })
    monkeypatch.setattr(media_tools, "summary", lambda auto_acquire=True: None)
    monkeypatch.setattr(wizard, "_detect_gpu", lambda: {
        "vendor": "nvidia", "backend": "cuda", "available": True,
        "driver": "580.1", "device_name": "Quadro M5000M", "notes": [],
        "kernels_missing": "sm_52",
    })
    resp = wizard.preflight()
    checks = resp["checks"] if isinstance(resp, dict) else resp.checks
    gpu = next(
        (c if isinstance(c, dict) else c.model_dump())
        for c in checks
        if (c if isinstance(c, dict) else c.model_dump())["id"] == "gpu"
    )
    assert gpu["status"] == "warn"
    assert "CUDA ready" not in gpu["detail"]
    assert "driver update will not help" in gpu["fix"].lower()
