"""A full disk is named on every user-facing failure surface.

Driving the real app on a full disk, uploading audio on the Transcriptions page
returned HTTP 500 from ``POST /transcribe`` with ``error_class: DISK_FULL`` (the
error journal knew) but only the generic "VoiceStudio hit an internal error"
detail: ``failure.classify`` had no DISK_FULL class, so the global 500 handler,
the streaming generate frame, ``build_failure`` (dub/batch events) and the dub
transcription stream all fell back to their floor messages.

App modules are resolved per test (see ``test_host_memory_failure_2462``): a
module bound at collection can go stale when another suite reloads it.
"""
from __future__ import annotations

import errno
import importlib
import types

import pytest


@pytest.fixture
def failure():
    return importlib.import_module("core.failure")


@pytest.fixture
def public():
    return importlib.import_module("core.public_errors")


ENOSPC = OSError(errno.ENOSPC, "No space left on device")


@pytest.mark.parametrize(
    "reason",
    [
        ENOSPC,
        "[Errno 28] No space left on device: '/tmp/tmpab12.wav'",
        "OSError: [Errno 122] Disk quota exceeded",
        "OSError: [Errno 69] Disc quota exceeded",
        "[WinError 112] There is not enough space on the disk",
        "[WinError 39] The disk is full",
        # Rust (hf-xet / safetensors) spelling.
        "IO error: No space left on device (os error 28)",
    ],
)
def test_every_disk_full_spelling_classifies(failure, reason):
    assert failure.classify(reason) == "DISK_FULL"


def test_the_errno_alone_is_enough_through_a_wrapper(failure):
    """A localized or replaced message still carries the errno in the chain."""
    try:
        try:
            raise OSError(errno.ENOSPC, "volume plein")
        except OSError as inner:
            raise RuntimeError("chunk write failed") from inner
    except RuntimeError as outer:
        assert failure.classify(outer) == "DISK_FULL"


def test_a_path_in_the_os_sentence_cannot_claim_the_failure(failure):
    """An AppImage mount path used to reach the WebKit white-screen rule first."""
    reason = "[Errno 28] No space left on device: '/tmp/.mount_VoiceStudio.AppImage/x.wav'"
    assert failure.classify(reason) == "DISK_FULL"


def test_memory_classes_keep_precedence(failure):
    assert failure.classify("CUDA out of memory. Tried to allocate 2.00 GiB") == "GPU_OOM"
    assert failure.classify(MemoryError()) == "HOST_MEMORY_EXHAUSTED"


@pytest.mark.parametrize(
    "spelling",
    [
        "[Errno 28] No space left on device",
        "[Errno 122] Disk quota exceeded",
        "[Errno 69] Disc quota exceeded",
        "[WinError 112] There is not enough space on the disk",
        "[WinError 39] The disk is full",
    ],
)
def test_a_video_download_keeps_its_folder_specific_class(failure, spelling):
    """#1225: the download class names the job folder — a sharper remedy —
    whichever disk-full spelling the OS used."""
    reason = f"Unable to download video: {spelling}"
    assert failure.classify(reason) == "VIDEO_DOWNLOAD_OS_ERROR"


@pytest.mark.parametrize(
    "reason",
    [
        "[WinError 390] unrelated",
        "[Errno 280] unrelated",
        "[Errno 39] Directory not empty",  # ENOTEMPTY on Linux, not WinError 39
        "Not enough disk space to install: needs 3 GB",  # our own preflight
    ],
)
def test_neighbouring_codes_are_not_read_as_a_full_disk(failure, reason):
    assert failure.classify(reason) != "DISK_FULL"
    assert not failure.is_disk_full_error(reason)


def test_it_is_a_context_free_class_with_a_voicestudio_hint(failure):
    assert "DISK_FULL" in failure._CONTEXT_FREE_HINT_CLASSES
    hint = failure._HINTS["DISK_FULL"]
    assert "disk is full" in hint.lower()
    assert "Settings → Storage" in hint


def test_the_500_payload_names_the_class_and_hint(public):
    payload = public.public_exception_response(
        ENOSPC, fallback="VoiceStudio hit an internal error; check the backend log for details."
    )
    assert payload["docs_topic"] == "DISK_FULL"
    assert "disk is full" in payload["hint"].lower()
    assert payload["hint"] in payload["detail"]
    # No exception text is copied.
    assert "No space left" not in payload["detail"]


def test_the_streaming_generate_frame_names_it_too(public):
    frame = public.stream_generation_failure(ENOSPC)
    assert frame["docs_topic"] == "DISK_FULL"
    assert frame["docs_url"].endswith("troubleshooting.md#generation-failure-diagnosis")


def test_dub_and_batch_failure_events_carry_the_hint(failure):
    fields = failure.build_failure(ENOSPC, stage="extract")
    assert fields["docs_topic"] == "DISK_FULL"
    assert fields["hint"] == failure._HINTS["DISK_FULL"]


def test_the_dub_transcription_stream_names_a_full_disk(public):
    code = public.transcription_failure_code(ENOSPC)
    assert code == "transcription_disk_full"
    assert "disk is full" in public.stream_failure(code)["detail"]


def test_journal_and_taxonomy_agree(failure):
    journal = importlib.import_module("core.error_journal")
    assert journal.classify_exception(ENOSPC) == failure.classify(ENOSPC) == "DISK_FULL"


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    from main import app
    import core.db

    core.db.init_db()
    return TestClient(app, client=("127.0.0.1", 50000), raise_server_exceptions=False)


def test_post_transcribe_on_a_full_disk_returns_the_hint(client, monkeypatch, tmp_path):
    """The reported request, end to end through the global 500 handler."""
    capture = importlib.import_module("api.routers.capture")
    target = tmp_path / "upload.wav"
    target.write_bytes(b"")

    class _FullDiskTemp:
        name = str(target)

        def write(self, _data):
            raise OSError(errno.ENOSPC, "No space left on device")

        def close(self):
            pass

    fake = types.SimpleNamespace(NamedTemporaryFile=lambda **_k: _FullDiskTemp())
    monkeypatch.setattr(capture, "tempfile", fake)
    res = client.post("/transcribe", files={"audio": ("a.wav", b"RIFF", "audio/wav")})
    assert res.status_code == 500
    body = res.json()
    assert body["error_class"] == "DISK_FULL"
    assert body["docs_topic"] == "DISK_FULL"
    assert "disk is full" in body["hint"].lower()
    assert body["hint"] in body["detail"]
