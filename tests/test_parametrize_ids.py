"""Long parameters must not end up in PYTEST_CURRENT_TEST (Windows caps env vars at 32 767 chars)."""
import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "_root_conftest", Path(__file__).resolve().parents[1] / "conftest.py"
)
_root = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_root)


def test_long_string_parameter_gets_a_short_id():
    assert _root.pytest_make_parametrize_id(None, "y" * 200_000, "text") == "text-len200000"


@pytest.mark.parametrize("val", ["short", 3, None])
def test_ordinary_parameters_keep_the_default_id(val):
    assert _root.pytest_make_parametrize_id(None, val, "v") is None


@pytest.mark.parametrize("text", ["y" * 40_000])
def test_a_huge_parameter_fits_the_windows_environment(text, request):
    # The id comes from the hook (pytest's own shortening would differ), and
    # it keeps PYTEST_CURRENT_TEST far below the Windows limit.
    assert request.node.nodeid.endswith("[text-len40000]")
    assert len(request.node.nodeid) < 32_767
