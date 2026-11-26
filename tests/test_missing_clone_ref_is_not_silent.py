"""A clone reference whose file is gone must not silently become a default voice.

Reported on Discord (in Russian):

    если переозвучивать отдельные предложения то голос не берется из видео,
    приходится все переозвучивать чтобы клон голоса сработал

    "If you re-dub individual sentences, the voice isn't taken from the video —
     you have to re-dub everything for the voice clone to work."

Clone references are FILE PATHS into the job's extracted-clip directory, and the
whole job dict — those paths included — is persisted to ``dub_history.job_data``
so saved projects reopen after a restart. The job therefore outlives its clips.
Reopen a saved dub once the clip directory has been cleaned, regenerate one
line, and every resolution branch happily hands the engine a path that is no
longer there.

Nothing checked it, and an engine given a missing reference renders **uncloned**
rather than failing — so the line comes back in a default voice, matching
nothing else in the dub, with no error anywhere. Re-running the full dub
re-extracts the clips, which is exactly why that appears to fix it and is the
workaround the reporter found on their own.

The change is diagnostic only: the reference is passed to the engine unchanged.
Nulling it would not alter what the user hears, and would decide on the
engine's behalf that a path it cannot ``stat`` is unusable. The defect is the
silence, not the fallback — so this does not change what happens, it changes
whether anyone can find out why.
"""
import importlib
import logging
import os
import sys

import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))


@pytest.fixture
def dg():
    """Resolve at call time — sibling suites reload/purge ``services.*``."""
    mod = importlib.import_module("api.routers.dub_generate")
    mod._MISSING_REF_WARNED.clear()
    yield mod
    mod._MISSING_REF_WARNED.clear()


def test_an_existing_reference_passes_through(dg, tmp_path):
    clip = tmp_path / "speaker1.wav"
    clip.write_bytes(b"RIFF....WAVE")
    assert dg.warn_if_ref_missing(str(clip), job_id="j", seg_id=1) == str(clip)


def test_a_missing_reference_is_still_passed_to_the_engine(dg, tmp_path):
    """Diagnostic only, on purpose.

    Nulling it would not change what the user hears — the engine already falls
    back — and it would decide on the engine`s behalf that a reference it
    cannot ``stat`` is unusable, which is untrue for anything resolved inside a
    sidecar`s own namespace. The defect is the silence, not the fallback.
    """
    gone = str(tmp_path / "gone.wav")
    assert dg.warn_if_ref_missing(gone, job_id="j", seg_id=1) == gone


def test_a_missing_reference_is_logged(dg, tmp_path, caplog):
    """The reported case: the path survived in the saved job, the file did not."""
    gone = str(tmp_path / "cleaned-up" / "speaker1.wav")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing(gone, job_id="job7", seg_id=42)
    assert "segment 42" in caplog.text, caplog.text
    assert "(speaker1.wav)" in caplog.text, "the log must name the file that vanished"
    assert str(tmp_path) not in caplog.text, "the log must not carry the user's folders"
    assert "DEFAULT voice" in caplog.text, (
        "the log has to say what the user will actually hear, or it reads as "
        "housekeeping rather than as the explanation for their bug"
    )


def test_no_reference_at_all_is_not_a_warning(dg, caplog):
    """A segment with no clone binding is the ordinary case — designed voices,
    preset voices, plain TTS. Warning there would bury the real one."""
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        assert dg.warn_if_ref_missing(None, job_id="j", seg_id=1) is None
        assert dg.warn_if_ref_missing("", job_id="j", seg_id=2) == ""
    assert caplog.text == ""


def test_each_segment_warns_once(dg, tmp_path, caplog):
    """A 300-segment dub whose clip directory was cleaned should produce one
    line per segment, not one per retry of that segment."""
    gone = str(tmp_path / "gone.wav")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        for _ in range(5):
            dg.warn_if_ref_missing(gone, job_id="job7", seg_id=42)
    assert caplog.text.count("segment 42") == 1


def test_different_segments_each_warn(dg, tmp_path, caplog):
    """...but per-segment, since which lines lost their reference is the
    diagnostic — "some of them" is not actionable."""
    gone = str(tmp_path / "gone.wav")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing(gone, job_id="job7", seg_id=1)
        dg.warn_if_ref_missing(gone, job_id="job7", seg_id=2)
    assert "segment 1" in caplog.text and "segment 2" in caplog.text


def test_jobs_do_not_share_the_warning_memo(dg, tmp_path, caplog):
    """Two dubs can legitimately use the same segment ids."""
    gone = str(tmp_path / "gone.wav")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing(gone, job_id="jobA", seg_id=1)
        dg.warn_if_ref_missing(gone, job_id="jobB", seg_id=1)
    assert caplog.text.count("segment 1") == 2


def test_forgetting_a_job_lets_it_warn_again(dg, tmp_path, caplog):
    gone = str(tmp_path / "gone.wav")
    dg.warn_if_ref_missing(gone, job_id="job7", seg_id=1)
    dg.forget_missing_ref_warnings("job7")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing(gone, job_id="job7", seg_id=1)
    assert "segment 1" in caplog.text


def test_an_unreadable_path_is_treated_as_missing(dg, monkeypatch, caplog):
    """A dead network mount or a permissions failure reaches the engine the
    same way a deleted file does, so it must not escape through an OSError."""
    monkeypatch.setattr(dg.os.path, "exists",
                        lambda p: (_ for _ in ()).throw(OSError("stale NFS handle")))
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing("/mnt/gone/clip.wav", job_id="j", seg_id=3)
    assert "segment 3" in caplog.text


def test_the_render_path_actually_calls_the_gate(dg):
    """The check is only worth having where the paths are used. Pin that both
    call sites route through it, since a resolution branch added later would
    otherwise quietly bypass it."""
    import inspect

    src = inspect.getsource(dg)
    # Once in the full render, once in the single-segment preview.
    assert src.count("warn_if_ref_missing(") >= 3, (
        "expected the definition plus both call sites; a generate path that "
        "does not verify its reference is the bug this file exists for"
    )


def test_distinct_paths_each_warn_even_under_one_segment_key(dg, tmp_path, caplog):
    """The single-segment preview endpoint has no segment identity to pass — it
    is a "render this text" call — so every preview shared the key "preview"
    and only the FIRST missing reference in a job was ever reported. Every
    later one, with a different path, was silenced (greptile).

    A distinct path is distinct information wherever it comes from.
    """
    a = str(tmp_path / "speaker1.wav")
    b = str(tmp_path / "speaker2.wav")
    # Only the file name is logged; the full path stays the de-duplication key.
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        dg.warn_if_ref_missing(a, job_id="job7", seg_id="preview")
        dg.warn_if_ref_missing(b, job_id="job7", seg_id="preview")
    assert "(speaker1.wav)" in caplog.text and "(speaker2.wav)" in caplog.text, (
        "a second missing reference under the same key was swallowed:\n" + caplog.text
    )


def test_the_same_path_under_one_key_still_warns_once(dg, tmp_path, caplog):
    """...without giving up the de-duplication it exists for."""
    same = str(tmp_path / "speaker1.wav")
    with caplog.at_level(logging.WARNING, logger="omnivoice.dub"):
        for _ in range(4):
            dg.warn_if_ref_missing(same, job_id="job7", seg_id="preview")
    assert caplog.text.count("(speaker1.wav)") == 1


def test_the_preview_request_can_carry_a_segment_id(dg):
    """Optional and diagnostic-only, so old callers are unaffected — but a
    caller that supplies it gets the line named instead of a bare "preview"."""
    req = dg.SegmentPreviewRequest(text="hello")
    assert req.segment_id is None
    assert dg.SegmentPreviewRequest(text="hello", segment_id="42").segment_id == "42"
