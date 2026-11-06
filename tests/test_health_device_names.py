"""A host name shared across accelerator families cannot identify an engine GPU."""
from dataclasses import replace

import pytest

from test_health_device_label import _caps, _resolved_body, engine, main  # noqa: F401


@pytest.mark.parametrize(
    ("family", "families", "host_name", "runtime_name", "expected"),
    [
        ("xpu", ("cuda", "xpu", "cpu"), "NVIDIA RTX", None, "xpu"),
        ("cuda", ("cuda", "xpu", "cpu"), "Intel Arc", None, "cuda"),
        ("mps", ("xpu", "mps", "cpu"), "Intel Arc", None, "mps"),
        ("rocm", ("rocm", "cpu"), "AMD Radeon", None, "rocm (AMD Radeon)"),
        ("xpu", ("xpu", "cpu"), "Intel Arc", None, "xpu (Intel Arc)"),
        ("xpu", ("cuda", "xpu", "cpu"), "NVIDIA RTX", "Intel Arc", "xpu (Intel Arc)"),
    ],
)
def test_health_uses_only_a_name_identified_for_the_engine_family(
    main, engine, family, families, host_name, runtime_name, expected,
):
    """Ambiguous host names stay bare; per-runtime and single-family names survive."""
    engine["caps"] = replace(_caps(family, host_name), available_families=families)
    engine["profile"] = {
        "effective_device": family,
        "routing_status": "ok",
        "runtime_device_name": runtime_name,
    }
    assert _resolved_body(main)["device"] == expected
