"""A dub that crashed mid-render resumes from its finished segments (#3924).

Fresh speech stays staged until the track is published; the journal only keeps
recovery copies that the next render of the same job may reuse when every
synthesis input is identical.
"""
import asyncio
import hashlib
import json
from types import SimpleNamespace

import pytest
import torch

from schemas.requests import DubRequest

SEGMENTS = [dict(start=i, end=i + 1, text=f"line {i}") for i in range(4)]
IDS = ["a", "b", "c", "d"]


@pytest.fixture
def dub(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg

    job = {"duration": 4.0, "dubbed_tracks": {}, "segments": []}
    path = tmp_path / "job"
    path.mkdir()
    events, generated = [], []
    state = {"fail_on_call": None, "cancel_after": None}

    def generate(**kwargs):
        generated.append(kwargs)
        if state["fail_on_call"] == len(generated):
            raise RuntimeError("engine crashed")
        return torch.ones(1, 24000) * 0.1

    backend = SimpleNamespace(sample_rate=24000, generate=generate, applies_own_mastering=True)

    async def resolve():
        return "omnivoice", backend

    class Tasks:
        def is_cancelled(self, *_):
            return state["cancel_after"] is not None and len(generated) >= state["cancel_after"]

        async def add_task(self, tid, kind, func, *args):
            async for event in func(*args):
                if event.startswith("data: "):
                    events.append(json.loads(event[6:]))

    monkeypatch.setattr(dg, "_resolve_dub_execution", resolve)
    monkeypatch.setattr(dg, "_get_job", lambda _: job)
    monkeypatch.setattr(dg, "_save_job", lambda *_: None)
    monkeypatch.setattr(dg, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(dg, "dub_seg_path", lambda _, sid: str(path / f"seg_{sid}.wav"))
    monkeypatch.setattr(dg, "task_manager", Tasks())
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(dg, "mark_synthetic", lambda a, *args, **kw: a)
    monkeypatch.setattr(dg, "get_effect_chain", lambda _: None)
    monkeypatch.setattr(dg, "normalize_audio", lambda a, **kw: a)

    def run(**kwargs):
        body = dict(segments=SEGMENTS, segment_ids=IDS, language_code="en", num_step=4)
        body.update(kwargs)
        events.clear()
        generated.clear()
        asyncio.run(dg.dub_generate("job", DubRequest(**body)))
        return list(events)

    journal = path / ".resume" / "en"
    return SimpleNamespace(
        run=run, job=job, path=path, generated=generated, state=state, journal=journal, dg=dg
    )


def _crash_after(dub, finished):
    """A render that dies once ``finished`` segments are done."""
    dub.state["fail_on_call"] = finished + 1
    events = dub.run()
    dub.state["fail_on_call"] = None
    assert any(e["type"] == "error" for e in events)
    assert not any(e["type"] == "done" for e in events)
    return events


def test_crashed_render_reuses_finished_segments(dub):
    _crash_after(dub, 2)
    # Nothing finished is visible to previews or partial regeneration.
    assert not list(dub.path.glob("seg_en_*.wav"))
    assert not dub.job.get("seg_hashes_by_lang")
    assert sorted(p.name for p in dub.journal.glob("*.json")) == ["seg_a.json", "seg_b.json"]

    events = dub.run()
    assert any(e["type"] == "done" for e in events)
    assert [g["text"] for g in dub.generated] == ["line 2", "line 3"]
    # Published normally: all four are cached with fingerprints, and the
    # recovery copies are gone.
    assert sorted(p.name for p in dub.path.glob("seg_en_*.wav")) == [f"seg_en_{i}.wav" for i in IDS]
    assert set(dub.job["seg_hashes_by_lang"]["en"]) == set(IDS)
    assert not (dub.path / ".resume").exists()


def test_reused_segment_is_byte_identical_to_what_was_rendered(dub):
    _crash_after(dub, 2)
    recovered = (dub.journal / "seg_a.wav").read_bytes()
    dub.run()
    assert (dub.path / "seg_en_a.wav").read_bytes() == recovered


@pytest.mark.parametrize(
    "change",
    [
        dict(num_step=8),
        dict(guidance_scale=3.5),
        dict(speed=1.3),
        dict(instruct="whisper"),
        dict(language="Spanish"),
    ],
)
def test_changed_inputs_rerender(dub, change):
    _crash_after(dub, 2)
    dub.run(**change)
    assert len(dub.generated) == 4


def test_changed_text_rerenders_only_that_segment(dub):
    _crash_after(dub, 2)
    segments = [dict(s) for s in SEGMENTS]
    segments[1]["text"] = "edited line"
    dub.run(segments=segments)
    assert [g["text"] for g in dub.generated] == ["edited line", "line 2", "line 3"]


def test_other_track_language_does_not_reuse_speech(dub):
    _crash_after(dub, 2)
    dub.run(language_code="fr")
    assert len(dub.generated) == 4


def test_truncated_recovered_wav_is_rerendered(dub):
    _crash_after(dub, 2)
    wav = dub.journal / "seg_a.wav"
    wav.write_bytes(wav.read_bytes()[:-5000])
    dub.run()
    assert [g["text"] for g in dub.generated] == ["line 0", "line 2", "line 3"]


def test_truncated_wav_with_a_matching_record_is_still_rejected(dub):
    """A file whose record was rewritten to match still fails the payload check."""
    _crash_after(dub, 2)
    wav = dub.journal / "seg_b.wav"
    wav.write_bytes(wav.read_bytes()[:-5000])
    meta_path = dub.journal / "seg_b.json"
    meta = json.loads(meta_path.read_text())
    meta["sha256"] = hashlib.sha256(wav.read_bytes()).hexdigest()
    meta_path.write_text(json.dumps(meta))
    dub.run()
    assert [g["text"] for g in dub.generated] == ["line 1", "line 2", "line 3"]


def test_unreadable_record_falls_back_to_normal_render(dub):
    _crash_after(dub, 2)
    (dub.journal / "seg_a.json").write_text("{not json")
    events = dub.run()
    assert any(e["type"] == "done" for e in events)
    assert [g["text"] for g in dub.generated] == ["line 0", "line 2", "line 3"]


def test_journal_failure_never_fails_the_dub(dub, monkeypatch):
    from services import dub_resume

    def boom(*_a, **_k):
        raise OSError("disk full")

    monkeypatch.setattr(dub_resume.DubResumeJournal, "prune", boom)
    events = dub.run()
    assert any(e["type"] == "done" for e in events)
    assert len(dub.generated) == 4
    assert not (dub.path / ".resume").exists()


def test_cancelled_render_promotes_nothing(dub):
    dub.state["cancel_after"] = 2
    events = dub.run()
    dub.state["cancel_after"] = None
    assert events[-1]["type"] == "cancelled"
    assert not any(e["type"] == "done" for e in events)
    assert not list(dub.path.glob("seg_en_*.wav"))
    assert not list(dub.path.glob(".render-*"))
    assert not dub.job["dubbed_tracks"]
    assert not dub.job.get("seg_hashes_by_lang")


def test_recovery_copies_do_not_count_as_cached_speech(dub):
    """Reusing a recovered segment is not the user accepting it."""
    _crash_after(dub, 2)
    assert not dub.job.get("seg_hashes_by_lang")
    assert not list(dub.path.glob("seg_en_*.wav"))


def test_rvc_and_preview_renders_do_not_journal(dub, monkeypatch):
    dub.state["fail_on_call"] = 3
    dub.run(preview=True)
    assert not (dub.path / ".resume").exists()
    monkeypatch.setattr(dub.dg, "rvc_is_enabled", lambda: True)
    monkeypatch.setattr(dub.dg, "apply_rvc", lambda _: None)
    dub.run()
    assert not (dub.path / ".resume").exists()


def test_entries_for_removed_segments_are_pruned(dub):
    _crash_after(dub, 2)
    dub.run(segments=SEGMENTS[2:], segment_ids=IDS[2:])
    assert not (dub.path / ".resume").exists()


def test_job_deleted_mid_render_leaves_no_recovery_copies(dub, monkeypatch):
    from services import dub_pipeline

    _crash_after(dub, 2)
    assert dub.journal.exists()
    monkeypatch.setitem(dub_pipeline._withdrawn_jobs, "job", 0.0)
    dub.dg._drop_resume_journal_if_withdrawn("job", "en")
    assert not (dub.path / ".resume").exists()
