"""Expected OS/network conditions get a specific closed code, not UNKNOWN."""
import errno
import importlib
import json
from pathlib import Path

import pytest

SCHEMA = json.loads(
    (Path(__file__).resolve().parents[1] / "config" / "diagnostics.json").read_text(encoding="utf-8")
)


@pytest.fixture
def reports():
    # Resolved per test: another test may replace the sys.modules entry.
    return importlib.import_module("core.diagnostic_reports")


@pytest.mark.parametrize("number", [errno.ENETUNREACH, errno.EHOSTUNREACH, errno.ENETDOWN])
def test_unreachable_network_oserror_is_network_error(reports, number):
    fields = reports._exception_fields(OSError(number, "private"), reports._schema())
    assert fields["error_type"] == "OSError"
    assert fields["error_code"] == "NETWORK_ERROR"


def test_disk_full_still_disk_full_and_plain_oserror_stays_unclassified(reports):
    schema = reports._schema()
    assert reports._exception_fields(OSError(errno.ENOSPC, "x"), schema)["error_code"] == "DISK_FULL"
    assert "error_code" not in reports._exception_fields(OSError(errno.EACCES, "x"), schema)


def test_new_codes_are_in_the_closed_policy():
    for code in ("GPU_JOB_TIMEOUT", "MAIN_UNCAUGHT_EXCEPTION", "MAIN_UNHANDLED_REJECTION",
                 "GPU_PROCESS_GONE", "UTILITY_PROCESS_GONE", "OTHER_CHILD_PROCESS_GONE"):
        assert code in SCHEMA["error_codes"]
