"""Native timeout budgets must not be presented as verified execution devices."""
from types import SimpleNamespace

import pytest

from test_health_device_label import _caps, _resolved_body, engine, main  # noqa: F401


@pytest.mark.parametrize("family", ["mps", "cpu", "rocm"])
@pytest.mark.parametrize("explicit_cpu", [False, True])
def test_omnivoice_native_budget_distinguishes_unknown_from_explicit_cpu(
    main, engine, monkeypatch, family, explicit_cpu,
):
    """A real unverified native profile cannot prove either Metal or CPU execution."""
    from engines.omnivoice_gguf import compute

    monkeypatch.setattr(compute, "selected_vulkan_adapter", lambda: None)
    monkeypatch.setattr(compute, "cpu_selection_explicit", lambda: explicit_cpu)
    engine["id"] = "omnivoice-gguf"
    engine["caps"] = _caps(family)
    engine["profile"] = compute.runtime_profile(
        SimpleNamespace(gpu_compat=("vulkan", "cpu")), engine["caps"],
    )
    monkeypatch.setattr(main, "_torch_device_label", lambda: pytest.fail("Native device guessed from Torch"))
    assert _resolved_body(main)["device"] == ("cpu" if explicit_cpu else "unknown")


@pytest.mark.parametrize(
    "verified,backend,expected",
    [(False, None, "unknown"), (False, "cpu", "cpu"), (None, None, "cpu")],
)
def test_only_unknown_native_cpu_budget_is_withheld(main, engine, verified, backend, expected):
    """Explicit native CPU and ordinary static CPU routing retain their labels."""
    engine["profile"] = {
        "effective_device": "cpu", "routing_status": "cpu_only",
        "runtime_device_verified": verified, "runtime_backend": backend,
    }
    assert _resolved_body(main)["device"] == expected
