"""A dub that crashed mid-render resumes from its finished segments (#3924).

Fresh speech stays staged until the track is published; the journal only keeps
recovery copies that the next render of the same job may reuse when every
synthesis input is identical.
"""
import asyncio
import hashlib
import json
import os
from types import SimpleNamespace

import pytest
import torch

SEGMENTS = [dict(start=i, end=i + 1, text=f"line {i}") for i in range(4)]
IDS = ["a", "b", "c", "d"]


@pytest.fixture
def dub(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg

    job = {"duration": 4.0, "dubbed_tracks": {}, "segments": []}
    path = tmp_path / "job"
    path.mkdir()
    checkpoint = tmp_path / "fixture-model"
    checkpoint.mkdir()
    (checkpoint / "model.safetensors").write_bytes(b"fixture weights")
    monkeypatch.setenv("OMNIVOICE_MODEL", str(checkpoint))
    events, generated = [], []
    state = {"fail_on_call": None, "cancel_after": None, "model_identity": "checkpoint-a"}

    def generate(**kwargs):
        generated.append(kwargs)
        if state["fail_on_call"] == len(generated):
            raise RuntimeError("engine crashed")
        return torch.ones(1, 24000) * 0.1

    backend = SimpleNamespace(
        sample_rate=24000, generate=generate, applies_own_mastering=True,
        model_identity=lambda: state["model_identity"],
    )

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

    async def run_async(**kwargs):
        from schemas.requests import DubRequest

        body = dict(segments=SEGMENTS, segment_ids=IDS, language_code="en", num_step=4)
        body.update(kwargs)
        events.clear()
        generated.clear()
        await dg.dub_generate("job", DubRequest(**body))
        return list(events)

    def run(**kwargs):
        return asyncio.run(run_async(**kwargs))

    journal = path / ".resume" / "en"
    return SimpleNamespace(
        run=run, run_async=run_async, job=job, path=path, generated=generated,
        state=state, journal=journal, dg=dg,
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


def test_changed_loaded_model_does_not_reuse_speech(dub):
    """Curated checkpoints can share an engine, class and sample rate."""
    _crash_after(dub, 2)
    dub.state["model_identity"] = "checkpoint-b"
    dub.run()
    assert len(dub.generated) == 4


def test_changed_omnivoice_environment_checkpoint_does_not_reuse_speech(dub, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL", "models/checkpoint-a")
    _crash_after(dub, 2)
    monkeypatch.setenv("OMNIVOICE_MODEL", "models/checkpoint-b")
    dub.run()
    assert len(dub.generated) == 4


def test_changed_environment_checkpoint_weights_do_not_reuse_speech(dub, monkeypatch, tmp_path):
    folder = tmp_path / "checkpoint"
    folder.mkdir()
    weights = folder / "model.safetensors"
    weights.write_bytes(b"checkpoint-a")
    monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))
    _crash_after(dub, 2)
    weights.write_bytes(b"replacement-checkpoint-b")
    dub.run()
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
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    meta["sha256"] = hashlib.sha256(wav.read_bytes()).hexdigest()
    meta_path.write_text(json.dumps(meta), encoding="utf-8")
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


@pytest.mark.parametrize("failure_point", ["prune", "recover", "model_folder"])
def test_resume_failures_do_not_log_private_paths(dub, monkeypatch, caplog, failure_point, tmp_path):
    from services import dub_resume

    def fail(*_args, **_kwargs):
        raise PermissionError("/home/resume-private-user/recordings/private.wav")

    if failure_point == "model_folder":
        folder = tmp_path / "checkpoint"
        folder.mkdir()
        monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))
        monkeypatch.setattr("core.model_fingerprint.tree_fingerprint", fail)
    else:
        monkeypatch.setattr(dub_resume.DubResumeJournal, failure_point, fail)
    caplog.set_level("DEBUG", logger="omnivoice.dub")
    events = dub.run()
    assert any(event["type"] == "done" for event in events)
    assert "resume-private-user" not in caplog.text
    assert "PermissionError" in caplog.text


def test_staging_write_failure_does_not_log_private_paths(dub, monkeypatch, caplog):
    real = dub.dg.atomic_save_wav
    failed = []

    def fail_once(*args, **kwargs):
        if not failed:
            failed.append(True)
            raise PermissionError("/home/resume-private-user/recordings/private.wav")
        return real(*args, **kwargs)

    monkeypatch.setattr(dub.dg, "atomic_save_wav", fail_once)
    caplog.set_level("WARNING", logger="omnivoice.dub")
    dub.run()
    assert "could not stage speech" in caplog.text
    assert "resume-private-user" not in caplog.text


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


@pytest.mark.parametrize("regen_only", [["a"], []])
def test_explicit_partial_render_never_reuses_recovery_copies(dub, regen_only):
    _crash_after(dub, 2)
    # No published natural cache exists, so cache repair expands this request
    # to a full render. Its explicit regeneration intent must still win.
    dub.run(regen_only=regen_only)
    assert len(dub.generated) == 4


def test_unreadable_model_fingerprint_never_records_reusable_speech(dub, monkeypatch, tmp_path):
    folder = tmp_path / "checkpoint"
    folder.mkdir()
    monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))

    def fail(_folder):
        raise PermissionError("weights unavailable")

    monkeypatch.setattr("core.model_fingerprint.tree_fingerprint", fail)
    _crash_after(dub, 2)
    assert not list(dub.journal.glob("*.json"))
    dub.run()
    assert len(dub.generated) == 4


def test_journal_writes_run_outside_the_event_loop(dub, monkeypatch):
    import threading
    from services import dub_resume

    loop_thread = threading.get_ident()
    write_threads = []
    original = dub_resume.DubResumeJournal.record

    def record(self, *args):
        write_threads.append(threading.get_ident())
        return original(self, *args)

    monkeypatch.setattr(dub_resume.DubResumeJournal, "record", record)
    events = dub.run()
    assert any(event["type"] == "done" for event in events)
    assert len(write_threads) == 4
    assert all(thread != loop_thread for thread in write_threads)


@pytest.mark.asyncio
async def test_cancelled_journal_write_finishes_before_withdrawn_job_cleanup(dub, monkeypatch):
    import threading
    from services import dub_pipeline, dub_resume

    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    original = dub_resume.DubResumeJournal.record
    loop_thread = threading.get_ident()
    cleanup_threads = []
    original_cleanup = dub.dg._drop_resume_journal_if_withdrawn

    def cleanup(*args):
        cleanup_threads.append(threading.get_ident())
        return original_cleanup(*args)

    def record(self, *args):
        started.set()
        assert release.wait(10), "test did not release journal writer"
        try:
            return original(self, *args)
        finally:
            finished.set()

    monkeypatch.setattr(dub_resume.DubResumeJournal, "record", record)
    monkeypatch.setattr(dub.dg, "_drop_resume_journal_if_withdrawn", cleanup)
    draining = asyncio.Queue()
    original_wait = asyncio.wait

    async def acknowledged_wait(*args, **kwargs):
        draining.put_nowait(None)
        return await original_wait(*args, **kwargs)

    monkeypatch.setattr(dub.dg.asyncio, "wait", acknowledged_wait)
    task = asyncio.create_task(dub.run_async())
    try:
        assert await asyncio.to_thread(started.wait, 10)
        monkeypatch.setitem(dub_pipeline._withdrawn_jobs, "job", 0.0)
        task.cancel()
        await asyncio.wait_for(draining.get(), 10)
        assert not task.done()
        task.cancel()  # Repeated cancellation must still drain the writer.
        await asyncio.wait_for(draining.get(), 10)
        assert not task.done()
    finally:
        release.set()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert finished.is_set()
    assert cleanup_threads and all(thread != loop_thread for thread in cleanup_threads)
    assert not (dub.path / ".resume").exists()
    assert not list(dub.path.glob(".render-*"))


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


@pytest.mark.parametrize("marked_at_crash", [False, True])
def test_watermark_state_change_rerenders(dub, monkeypatch, marked_at_crash):
    monkeypatch.setattr(dub.dg, "will_mark", lambda: marked_at_crash)
    _crash_after(dub, 2)
    monkeypatch.setattr(dub.dg, "will_mark", lambda: not marked_at_crash)
    dub.run()
    assert len(dub.generated) == 4


def test_same_watermark_state_still_resumes(dub, monkeypatch):
    monkeypatch.setattr(dub.dg, "will_mark", lambda: True)
    _crash_after(dub, 2)
    dub.run()
    assert len(dub.generated) == 2


def test_mastering_or_effect_chain_change_rerenders(dub, monkeypatch):
    _crash_after(dub, 2)
    monkeypatch.setattr(dub.dg, "get_effect_chain", lambda _: [{"type": "reverb"}])
    dub.run()
    assert len(dub.generated) == 4


def test_local_model_folder_change_rerenders(dub, monkeypatch, tmp_path):
    from services import local_model_paths

    monkeypatch.delenv("OMNIVOICE_MODEL", raising=False)
    folder = tmp_path / "weights"
    folder.mkdir()
    (folder / "model.safetensors").write_bytes(b"v1")
    monkeypatch.setattr(local_model_paths, "get", lambda family, engine: str(folder))
    _crash_after(dub, 2)
    dub.run()
    assert len(dub.generated) == 2  # unchanged folder resumes
    dub.state["fail_on_call"] = 3
    dub.run()
    dub.state["fail_on_call"] = None
    (folder / "model.safetensors").write_bytes(b"version two")
    dub.run()
    assert len(dub.generated) == 4


def test_environment_checkpoint_ignores_unused_saved_folder_changes(dub, monkeypatch, tmp_path):
    from services import local_model_paths

    folder = tmp_path / "unused-model"
    folder.mkdir()
    weights = folder / "model.safetensors"
    weights.write_bytes(b"v1")
    monkeypatch.setattr(local_model_paths, "get", lambda family, engine: str(folder))
    active = tmp_path / "active-checkpoint"
    active.mkdir()
    (active / "model.safetensors").write_bytes(b"active weights")
    monkeypatch.setenv("OMNIVOICE_MODEL", str(active))
    _crash_after(dub, 2)
    weights.write_bytes(b"unused-folder-version-two")
    dub.run()
    assert len(dub.generated) == 2


@pytest.mark.parametrize("kind", ["inprocess", "sidecar-override", "sidecar-managed"])
@pytest.mark.parametrize("change", ["unchanged", "weights", "same-name-folder"])
def test_cosyvoice_actual_weights_change_rerenders(dub, monkeypatch, tmp_path, kind, change):
    from services.tts_backend import CosyVoiceBackend
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend, MANAGED_MODEL_SUBDIR

    cls = CosyVoiceBackend if kind == "inprocess" else CosyVoiceSubprocessBackend
    backend = cls()
    def generate(**kwargs):
        dub.generated.append(kwargs)
        if dub.state["fail_on_call"] == len(dub.generated):
            raise RuntimeError("engine crashed")
        return torch.ones(1, 24000) * 0.1

    backend.generate = generate
    backend.applies_own_mastering = True

    async def resolve():
        return "cosyvoice", backend

    monkeypatch.setattr(dub.dg, "_resolve_dub_execution", resolve)

    def select(base):
        folder = base / (MANAGED_MODEL_SUBDIR if kind == "sidecar-managed" else "same-model")
        folder.mkdir(parents=True)
        (folder / "model.pt").write_bytes(b"v1")
        if kind == "sidecar-managed":
            monkeypatch.delenv("OMNIVOICE_COSYVOICE_MODEL", raising=False)
            monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(base))
        else:
            monkeypatch.setenv("OMNIVOICE_COSYVOICE_MODEL", str(folder))
        return folder

    folder = select(tmp_path / "first")
    _crash_after(dub, 2)
    if change == "weights":
        (folder / "model.pt").write_bytes(b"replacement weights")
    elif change == "same-name-folder":
        previous = (folder / "model.pt").stat()
        replacement = select(tmp_path / "second") / "model.pt"
        replacement.write_bytes(b"v2")
        os.utime(replacement, ns=(previous.st_atime_ns, previous.st_mtime_ns))
    dub.run()
    assert len(dub.generated) == (2 if change == "unchanged" and kind == "inprocess" else 4)


@pytest.mark.parametrize("kind", ["voxcpm2", "voxcpm2-sidecar", "display-name"])
def test_unproven_model_identity_never_records_reusable_speech(dub, monkeypatch, tmp_path, kind):
    from services.tts_backend import VoxCPM2Backend, SherpaOnnxBackend
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    cls = {"voxcpm2": VoxCPM2Backend, "voxcpm2-sidecar": VoxCPM2SubprocessBackend,
           "display-name": SherpaOnnxBackend}[kind]
    identity_backend = cls()
    _, backend = asyncio.run(dub.dg._resolve_dub_execution())
    backend.model_identity = identity_backend.model_identity

    async def resolve():
        return identity_backend.id, backend

    monkeypatch.setattr(dub.dg, "_resolve_dub_execution", resolve)
    monkeypatch.setenv("OMNIVOICE_VOXCPM_MODEL", "first/checkpoint")
    _crash_after(dub, 2)
    assert not list(dub.journal.glob("*.json"))
    monkeypatch.setenv("OMNIVOICE_VOXCPM_MODEL", "second/checkpoint")
    dub.run()
    assert len(dub.generated) == 4


@pytest.mark.parametrize("change", [False, True])
def test_resident_model_fingerprint_must_match_disk_before_journaling(dub, monkeypatch, tmp_path, change):
    from services import local_model_paths

    folder = tmp_path / "resident"
    folder.mkdir()
    weights = folder / "model.pt"
    weights.write_bytes(b"loaded weights")
    _, backend = asyncio.run(dub.dg._resolve_dub_execution())
    backend._model = SimpleNamespace(
        _voicestudio_checkpoint=str(folder),
        _voicestudio_weights_fingerprint=local_model_paths._weights_fingerprint(str(folder)),
    )
    monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))
    if change:
        weights.write_bytes(b"new on-disk weights; resident still old")
    _crash_after(dub, 2)
    assert bool(list(dub.journal.glob("*.json"))) is not change
    dub.run()
    assert len(dub.generated) == (4 if change else 2)


def test_model_loaded_during_synthesis_must_match_recovery_key(dub, monkeypatch, tmp_path):
    from services import local_model_paths

    folder = tmp_path / "during-load"
    folder.mkdir()
    weights = folder / "model.pt"
    weights.write_bytes(b"old weights")
    monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))
    _, backend = asyncio.run(dub.dg._resolve_dub_execution())
    original_generate = backend.generate

    def generate(**kwargs):
        if not getattr(backend, "_model", None):
            weights.write_bytes(b"changed before native loader reads")
            backend._model = SimpleNamespace(
                _voicestudio_checkpoint=str(folder),
                _voicestudio_weights_fingerprint=local_model_paths._weights_fingerprint(str(folder)),
            )
        return original_generate(**kwargs)

    backend.generate = generate
    _crash_after(dub, 2)
    assert not list(dub.journal.glob("*.json"))


def test_model_change_during_load_never_publishes_mixed_recovered_weights(dub, monkeypatch, tmp_path):
    from services import local_model_paths

    folder = tmp_path / "recovered-before-load"
    folder.mkdir()
    weights = folder / "model.pt"
    weights.write_bytes(b"old weights")
    monkeypatch.setenv("OMNIVOICE_MODEL", str(folder))
    _crash_after(dub, 2)
    _, backend = asyncio.run(dub.dg._resolve_dub_execution())
    original_generate = backend.generate

    def generate(**kwargs):
        if not getattr(backend, "_model", None):
            weights.write_bytes(b"new weights loaded after recovery")
            backend._model = SimpleNamespace(
                _voicestudio_checkpoint=str(folder),
                _voicestudio_weights_fingerprint=local_model_paths._weights_fingerprint(str(folder)),
            )
        return original_generate(**kwargs)

    backend.generate = generate
    events = dub.run()
    assert not any(event["type"] == "done" for event in events)
    assert any(event["type"] == "error" for event in events)
    assert not list(dub.path.glob("seg_en_*.wav"))
    assert any(event["type"] == "done" for event in dub.run())
    assert len(dub.generated) == 4


def test_hub_repository_name_alone_never_records_reusable_speech(dub, monkeypatch):
    monkeypatch.setenv("OMNIVOICE_MODEL", "example/mutable-hub-checkpoint")
    _crash_after(dub, 2)
    assert not list(dub.journal.glob("*.json"))
    dub.run()
    assert len(dub.generated) == 4


def test_unhashable_later_reference_does_not_fail_after_recovery(dub, monkeypatch, tmp_path):
    from services import dub_resume

    reference = tmp_path / "reference.wav"
    reference.write_bytes(b"reference")
    dub.job["segment_clones"] = {"c": {"ref_audio": str(reference), "ref_text": "a reference", "duration": 1.0}}
    segments = [dict(segment) for segment in SEGMENTS]
    segments[2]["profile_id"] = "auto-seg:c"
    dub.state["fail_on_call"] = 3
    assert any(event["type"] == "error" for event in dub.run(segments=segments))
    dub.state["fail_on_call"] = None
    original_hash = dub_resume.hash_reference_file
    monkeypatch.setattr(dub_resume, "hash_reference_file", lambda path, memo: None if path else original_hash(path, memo))
    events = dub.run(segments=segments)
    assert any(event["type"] == "done" for event in events)
    assert [row["text"] for row in dub.generated] == ["line 2", "line 3"]
    assert dub.generated[0]["ref_audio"] == str(reference)
