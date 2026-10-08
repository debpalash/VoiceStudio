"""Source subtitles replaced while long dub work runs must win over its result."""
import ast
import asyncio
import io
import os
import struct
import wave
from pathlib import Path
from types import SimpleNamespace

import pytest

from fastapi import UploadFile

BACKEND = Path(__file__).resolve().parents[1] / "backend"

# The revision helpers are the only code allowed to assign job["segments"].
_ALLOWED_WRITERS = {("dub_pipeline.py", "_replace_segments")}


def _segment_writes(tree: ast.AST):
    """(function, line) for every replacement of a dict's ``"segments"`` entry.

    Covers ``x["segments"] = ...`` (plain, augmented, annotated) and
    ``x.update({"segments": ...})`` / ``x.update(segments=...)``.
    """
    for func in ast.walk(tree):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for node in ast.walk(func):
            targets = node.targets if isinstance(node, ast.Assign) else (
                [node.target] if isinstance(node, (ast.AugAssign, ast.AnnAssign)) else []
            )
            for target in targets:
                if (
                    isinstance(target, ast.Subscript)
                    and isinstance(target.slice, ast.Constant)
                    and target.slice.value == "segments"
                    and isinstance(target.value, ast.Name)
                ):
                    yield func.name, node.lineno
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "job"
            ):
                keys = [kw.arg for kw in node.keywords]
                for arg in node.args:
                    if isinstance(arg, ast.Dict):
                        keys += [k.value for k in arg.keys if isinstance(k, ast.Constant)]
                if "segments" in keys:
                    yield func.name, node.lineno


def _dub_modules():
    return [
        *sorted((BACKEND / "api" / "routers").glob("dub_*.py")),
        BACKEND / "services" / "dub_pipeline.py",
    ]


def test_source_subtitles_are_only_replaced_through_the_revision_helper():
    offenders = []
    for path in _dub_modules():
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for func, line in _segment_writes(tree):
            if (path.name, func) not in _ALLOWED_WRITERS:
                offenders.append(f"{path.name}:{line} in {func}")
    assert not offenders, (
        "Replace job subtitles with services.dub_pipeline.replace_source_segments "
        "(or publish_rendered_segments for a finished render) so running renders "
        f"and transcriptions see the change: {offenders}"
    )


def test_render_publication_is_seen_by_transcription_but_not_by_other_renders():
    from api.routers.dub_generate import _sync_job_segments
    from schemas.requests import DubRequest
    from services.dub_pipeline import segments_revision, source_segments_revision

    job = {"segments": [{"id": "a", "start": 0.0, "end": 1.0, "text": "hello"}]}
    _sync_job_segments(job, DubRequest(
        segments=[dict(start=0, end=1, text="hola")], segment_ids=["a"], language_code="es",
    ))
    assert job["segments"][0]["text"] == "hola"
    assert segments_revision(job) == 1
    assert source_segments_revision(job) == 0


def _make_wav(path: Path, seconds: float = 1.0, sr: int = 16000) -> None:
    n = int(seconds * sr)
    with wave.open(str(path), "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(sr)
        wf.writeframes(struct.pack(f"<{n}h", *([0] * n)))


_SRT = b"1\n00:00:00,000 --> 00:00:00,900\nImported correction\n"


@pytest.fixture
def transcription(tmp_path, monkeypatch, asr_model_installed):
    """Run one streamed transcription with ``during_refine`` called while it
    refines the voice references it extracted (the long tail of the pass).

    ``asr_model_installed`` matters: without it the hermetic CI environment,
    which has no ASR weights, ends the stream at the model-missing preflight
    and the pass never reaches reference extraction.
    """
    from api.routers import dub_core as dc
    from services import speaker_clone as sc

    job_id = "t_import_during_asr"
    job_dir = tmp_path / "job"
    job_dir.mkdir()
    audio = tmp_path / "a.wav"
    _make_wav(audio)
    job = {"audio_path": str(audio), "vocals_path": None, "scene_cuts": []}
    dc._dub_jobs[job_id] = job

    class _FakeASR:
        id = "fake"

        def ensure_loaded(self):
            pass

        def transcribe(self, *a, **k):
            return {"chunks": [{"text": "asr words", "timestamp": (0.0, 0.5)}],
                    "segments": [], "language": "en"}

        def unload(self):
            pass

    monkeypatch.setattr("services.asr_backend.get_active_asr_backend", lambda *a, **k: _FakeASR())
    monkeypatch.setattr(dc, "offload_tts_for_asr", lambda *a, **k: None)
    monkeypatch.setattr(dc, "_safe_job_dir", lambda _jid: str(job_dir))
    monkeypatch.setattr(dc, "_save_job", lambda *_: None)

    def _extract(_vocals, segments, out_dir, seg_ids=None):
        # Same file name every pass, as the real extractor writes.
        ref = Path(out_dir) / "seg_ref_0.wav"
        ref.write_bytes(b"clip of this pass")
        key = str((seg_ids or [0])[0])
        return {key: {"ref_audio": str(ref), "ref_text": "asr words"}}

    monkeypatch.setattr(sc, "extract_segment_refs", _extract)
    hooks = []
    hook_errors = []

    def _refine(refs, _backend):
        for hook in hooks:
            try:
                hook()
            except Exception as exc:  # the pass swallows it; the test must not
                hook_errors.append(exc)
                raise
        return refs

    monkeypatch.setattr(sc, "refine_ref_texts", _refine)

    def run():
        async def _collect():
            resp = await dc.dub_transcribe_stream(job_id)
            parts = []
            async for chunk in resp.body_iterator:
                parts.append(chunk.decode() if isinstance(chunk, (bytes, bytearray)) else str(chunk))
            return "".join(parts)

        body = asyncio.run(_collect())
        assert not hook_errors, hook_errors
        assert "event: error" not in body, body
        return body

    def import_srt():
        upload = UploadFile(file=io.BytesIO(_SRT), filename="fixed.srt")
        return asyncio.run(dc.dub_import_srt(job_id, upload))

    try:
        yield SimpleNamespace(job=job, job_dir=job_dir, hooks=hooks, run=run, import_srt=import_srt)
    finally:
        dc._dub_jobs.pop(job_id, None)


def test_srt_import_during_transcription_survives_its_commit(transcription):
    imported = {}
    transcription.hooks.append(lambda: imported.update(transcription.import_srt()))
    body = transcription.run()
    job = transcription.job

    assert imported["segments"][0]["text"] == "Imported correction"
    texts = [segment["text"] for segment in job["segments"]]
    assert texts == ["Imported correction"]
    # Nothing from the discarded transcript is attached to the imported cues.
    assert "clip of this pass" not in str(job.get("segment_clones"))
    assert not job.get("transcription_complete")
    final = body[body.rfind("event: final"):]
    assert "Imported correction" in final and "asr words" not in final, final
    assert body.rfind("event: done") > body.rfind("event: final")


def test_dub_published_during_transcription_survives_its_commit(transcription):
    from api.routers.dub_generate import _sync_job_segments
    from schemas.requests import DubRequest

    transcription.job["segments"] = [{"id": 0, "start": 0.0, "end": 0.9, "text": "hello"}]

    def publish_edited_dub():
        _sync_job_segments(transcription.job, DubRequest(
            segments=[dict(start=0, end=0.9, text="edited line")],
            segment_ids=["0"], language_code="es",
        ))

    transcription.hooks.append(publish_edited_dub)
    body = transcription.run()
    assert [s["text"] for s in transcription.job["segments"]] == ["edited line"]
    assert not transcription.job.get("transcription_complete")
    final = body[body.rfind("event: final"):]
    assert "edited line" in final and "asr words" not in final, final


def test_transcription_never_overwrites_references_of_imported_cues(transcription):
    """Cues imported mid-pass keep the clips they were matched to.

    The import carries the previous transcript's references over to the new
    cues. A pass writing its own clips under the same names in the same
    folder replaced that audio, so the imported cues cloned this discarded
    pass's speech instead.
    """
    job, job_dir = transcription.job, transcription.job_dir
    # A job transcribed before per-pass folders: references sit in the job dir.
    legacy = job_dir / "seg_ref_0.wav"
    legacy.write_bytes(b"clip the user's cues use")
    job["segments"] = [{"id": 0, "start": 0.0, "end": 0.9, "text": "earlier", "speaker_id": "Speaker 1"}]
    job["segment_clones"] = {"0": {"ref_audio": str(legacy), "ref_text": "earlier"}}

    imported = {}
    transcription.hooks.append(lambda: imported.update(transcription.import_srt()))
    transcription.run()

    assert job["segments"][0]["text"] == "Imported correction"
    assert job["segment_clones"]["0"]["ref_audio"] == str(legacy)
    assert legacy.read_bytes() == b"clip the user's cues use"
    # The discarded pass leaves no clips behind.
    assert not list((job_dir / "refs").glob("*/*"))


def test_each_committed_pass_owns_its_references(transcription):
    job, job_dir = transcription.job, transcription.job_dir
    legacy = job_dir / "voice_Speaker_1.wav"
    legacy.write_bytes(b"pre-upgrade clip")

    transcription.run()
    (first,) = (c["ref_audio"] for c in job["segment_clones"].values())
    assert Path(first).parent.parent == job_dir / "refs"
    assert Path(first).read_bytes() == b"clip of this pass"
    assert job["ref_run"] == Path(first).parent.name

    job["transcription_complete"] = False  # the user asks for a fresh pass
    transcription.run()
    (second,) = (c["ref_audio"] for c in job["segment_clones"].values())
    assert Path(second).parent != Path(first).parent
    assert Path(second).read_bytes() == b"clip of this pass"
    # The previous pass's folder goes; files outside per-pass folders stay.
    assert not Path(first).parent.exists()
    assert legacy.read_bytes() == b"pre-upgrade clip"


def test_transcription_tests_neutralize_the_asr_model_preflight():
    """A dev machine with ASR weights hides a missing preflight stub; CI has
    none, so such a test ends at ``asr_model_missing`` there and fails far
    from the cause."""
    import re

    # A direct router call, or an HTTP request to the route (not a bare path in a route list).
    calls = re.compile(r"dub_transcribe(_stream)?\(|\.(get|post|stream)\(\s*f?[\"']/dub/transcribe")
    offenders = []
    for root in (Path(__file__).parent, BACKEND / "tests"):
        for path in sorted(root.rglob("test_*.py")):
            text = path.read_text(encoding="utf-8")
            if calls.search(text) and not re.search(r"asr_model_installed|asr_model_missing_error", text):
                offenders.append(str(path.relative_to(BACKEND.parent)))
    assert not offenders, (
        "Use the asr_model_installed fixture (tests/conftest.py) or stub "
        f"asr_model_missing_error in: {offenders}"
    )


def test_blocking_transcription_drops_the_previous_transcripts_references(transcription):
    """Its segments reuse the previous transcript's ids for other lines."""
    from api.routers import dub_core as dc

    job = transcription.job
    job["segments"] = [{"id": 0, "start": 0.0, "end": 0.9, "text": "earlier"}]
    job["segment_clones"] = {"0": {"ref_audio": "earlier.wav", "ref_text": "earlier"}}
    job["speaker_clones"] = {"Speaker 1": {"ref_audio": "earlier_speaker.wav"}}
    job["cast_sources"] = {"Speaker 1": {"duration": 4.0}}

    result = asyncio.run(dc.dub_transcribe("t_import_during_asr"))

    assert [s["text"] for s in result["segments"]] == ["asr words"]
    assert job["segment_clones"] == {}
    assert job["speaker_clones"] == {}
    assert job["cast_sources"] == {}


def test_import_racing_a_finishing_transcription_keeps_the_clips_it_saves(
    transcription, monkeypatch,
):
    """An import that selects the current transcript's clips must find them on
    disk after a re-transcription that finished meanwhile cleans up.

    The import is held right after selecting its references. Before the fix
    it held them outside the job lock, so the transcription committed, deleted
    the previous pass's folder, and the import then saved references to the
    deleted clips. Now selection and save are one locked step, which the
    transcription's commit and cleanup wait for.
    """
    import threading

    from api.routers import dub_core as dc
    from services import dub_pipeline

    job = transcription.job
    transcription.run()  # first pass: the clips the import will select
    (selected_clip,) = (Path(c["ref_audio"]) for c in job["segment_clones"].values())
    assert selected_clip.exists()
    job["transcription_complete"] = False

    selected, cleaned_up = threading.Event(), threading.Event()
    real_carry = dc._carry_srt_voice_metadata
    real_discard = dub_pipeline.discard_reference_run

    def carry_then_wait(*args, **kwargs):
        result = real_carry(*args, **kwargs)
        selected.set()
        # Resume once the transcription has cleaned up, or, when it cannot
        # (it waits for this import's lock), after a grace period.
        cleaned_up.wait(timeout=2)
        return result

    def discard_and_signal(*args, **kwargs):
        real_discard(*args, **kwargs)
        cleaned_up.set()

    monkeypatch.setattr(dc, "_carry_srt_voice_metadata", carry_then_wait)
    monkeypatch.setattr(dub_pipeline, "discard_reference_run", discard_and_signal)

    importer_errors = []

    def import_in_background():
        def _run():
            try:
                transcription.import_srt()
            except Exception as exc:
                importer_errors.append(exc)
        importer = threading.Thread(target=_run)
        importer.start()
        transcription.importer = importer
        assert selected.wait(timeout=10)

    transcription.hooks.append(import_in_background)
    transcription.run()
    transcription.importer.join(timeout=10)

    assert not importer_errors, importer_errors
    assert [s["text"] for s in job["segments"]] == ["Imported correction"]
    saved = Path(job["segment_clones"]["0"]["ref_audio"])
    assert saved == selected_clip
    assert saved.read_bytes() == b"clip of this pass"


def test_reference_cleanup_keeps_a_folder_the_job_still_points_into(tmp_path):
    from services.dub_pipeline import (
        REFERENCE_RUNS_DIRNAME,
        discard_reference_run,
        new_reference_run_dir,
    )

    run_dir = Path(new_reference_run_dir(str(tmp_path)))
    clip = run_dir / "seg_ref_0.wav"
    clip.write_bytes(b"clip")
    job = {"segment_clones": {"0": {"ref_audio": str(clip)}}, "speaker_clones": {}}

    discard_reference_run(str(run_dir), job)
    assert clip.read_bytes() == b"clip"

    job["segment_clones"] = {}
    discard_reference_run(str(run_dir), job)
    assert not run_dir.exists()
    assert (tmp_path / REFERENCE_RUNS_DIRNAME).is_dir()


@pytest.mark.skipif(not hasattr(os, "symlink"), reason="needs symlinks")
def test_reference_cleanup_never_follows_a_linked_run_folder(tmp_path):
    from services.dub_pipeline import REFERENCE_RUNS_DIRNAME, discard_reference_run

    outside = tmp_path / "outside"
    outside.mkdir()
    keep = outside / "keep.wav"
    keep.write_bytes(b"not ours")
    refs = tmp_path / "job" / REFERENCE_RUNS_DIRNAME
    refs.mkdir(parents=True)
    try:
        os.symlink(outside, refs / "run", target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks unavailable")

    discard_reference_run(str(refs / "run"), {})
    assert keep.read_bytes() == b"not ours"

    linked_refs = tmp_path / "job2" / REFERENCE_RUNS_DIRNAME
    linked_refs.parent.mkdir()
    real_refs = tmp_path / "elsewhere"
    (real_refs / "run").mkdir(parents=True)
    victim = real_refs / "run" / "victim.wav"
    victim.write_bytes(b"not ours")
    os.symlink(real_refs, linked_refs, target_is_directory=True)

    discard_reference_run(str(linked_refs / "run"), {})
    assert victim.read_bytes() == b"not ours"
