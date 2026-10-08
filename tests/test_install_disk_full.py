"""A full disk mid-install must fail fast with an actionable message (not retry
five times, not blame the network)."""
import errno

import pytest


@pytest.mark.parametrize(
    "reason",
    [
        OSError(errno.ENOSPC, "No space left on device"),
        OSError("[Errno 28] No space left on device: '/x/blobs/a.incomplete'"),
        "[WinError 112] There is not enough space on the disk",
        "OSError: [Errno 122] Disk quota exceeded",
        "OSError: [Errno 69] Disc quota exceeded",
    ],
)
def test_disk_full_is_recognised(reason):
    from core.failure import is_disk_full_error

    assert is_disk_full_error(reason)


def test_disk_full_is_found_through_a_wrapped_cause():
    from core.failure import is_disk_full_error

    try:
        try:
            raise OSError(errno.ENOSPC, "No space left on device")
        except OSError as inner:
            raise RuntimeError("download failed") from inner
    except RuntimeError as wrapped:
        assert is_disk_full_error(wrapped)


@pytest.mark.skipif(not hasattr(errno, "EDQUOT"), reason="EDQUOT is absent on Windows")
def test_platform_quota_errno_is_disk_full():
    from core.failure import is_disk_full_error

    assert is_disk_full_error(OSError(errno.EDQUOT, "quota"))


def test_other_failures_are_not_disk_full():
    from core.failure import is_disk_full_error

    assert not is_disk_full_error(ConnectionResetError("peer closed connection"))
    assert not is_disk_full_error("Not enough disk space to install: needs 3 GB")
    assert not is_disk_full_error(None)


def test_model_install_does_not_retry_a_full_disk():
    from api.routers.setup.download import _is_retryable_download_error

    assert not _is_retryable_download_error(OSError(errno.ENOSPC, "No space left on device"))
    assert _is_retryable_download_error(ConnectionResetError(errno.ECONNRESET, "reset"))


def test_disk_full_message_names_free_space_and_cache(tmp_path):
    from api.routers.setup.models import disk_full_message

    text = disk_full_message(cache_dir=str(tmp_path))
    assert str(tmp_path) in text and "Free up space" in text


def test_sidecar_install_reports_full_disk_from_uv_output():
    from collections import deque

    from services import sidecar_install as si

    assert si._output_shows_disk_full(
        deque(["Downloading torch", "error: failed to write: No space left on device (os error 28)"])
    )
    assert not si._output_shows_disk_full(deque(["connection reset by peer"]))


def test_earlier_recovered_disk_full_does_not_relabel_a_later_uv_failure(monkeypatch):
    """A clone that logged ENOSPC then recovered stays in the shared job log;
    only the failing process's own output may decide the diagnosis."""
    from collections import deque

    from services import sidecar_install as si

    job = {"engine_id": "x", "log": deque(["clone: No space left on device", "fallback clone ok"])}

    class _Proc:
        stdout = iter(["error: no matching distribution\n"])

        def wait(self, timeout=None):
            return 1

    monkeypatch.setattr(si, "spawn_owned", lambda *a, **k: _Proc())
    assert si._run_logged(job, ["uv"], timeout=5) == 1
    assert list(job["_last_run_output"]) == ["error: no matching distribution\n"]
    assert not si._output_shows_disk_full(job["_last_run_output"])
