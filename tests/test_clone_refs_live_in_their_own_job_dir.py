"""A job's clone references must live in ITS directory, not a neighbour's (#1331).

Reported from Discord (in Russian): re-dubbing a single sentence loses the
cloned voice; only a full re-dub keeps it. #1361 made the mechanism visible —
the reference path is handed to the engine but the file is gone. This is the
root cause for the identified class:

On a content-hash cache hit (same video dubbed twice), ``find_cached_job``
points the NEW job's ``vocals_path`` into the OLD job's directory — that is the
cache working as designed. But ``dub_core`` then extracted the new job's clone
references into ``os.path.dirname(vocals_path)``, i.e. **into the old job's
directory too**. Delete that older history entry — an ordinary, sanctioned
action that ``rmtree``s its dir — and every clone reference of the newer job
dangles. From then on each single-segment regen silently renders in the
default voice, and a full re-dub "fixes" it only because prep re-extracts.

The fix is one argument: clones are written into the CURRENT job's own dir.
"""
from __future__ import annotations

import ast
import os
import sys

import numpy as np
import pytest

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

sf = pytest.importorskip("soundfile")


def _source_tree():
    from pathlib import Path
    return ast.parse((Path(__file__).parent.parent / "backend/api/routers/dub_core.py").read_text(encoding="utf-8"))


def _assert_job_owned_outputs(tree):
    """Follow each output variable in its own lexical scope, not its spelling.

    The normal transcription helper is checked behaviorally below; direct
    refresh paths must resolve through new_reference_run_dir(_safe_job_dir(job_id)).
    Unknown expressions fail closed so a newly introduced path needs coverage.
    """
    parents = {child: parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}

    def scope(node):
        while node in parents:
            node = parents[node]
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                return node
        return tree

    def owned(expr, seen=()):
        if isinstance(expr, ast.Name):
            assert expr.id not in seen, "cyclic output directory assignment"
            assignments = [node for node in ast.walk(scope(expr))
                           if isinstance(node, ast.Assign) and scope(node) is scope(expr)
                           and node.lineno < expr.lineno
                           and any(isinstance(target, ast.Name) and target.id == expr.id
                                   for target in node.targets)]
            assert assignments, f"unresolved output directory: {expr.id}"
            latest = max(assignments, key=lambda node: node.lineno)
            return owned(latest.value, (*seen, expr.id))
        assert isinstance(expr, ast.Call), f"unverified output directory: {ast.unparse(expr)}"
        name = ast.unparse(expr.func)
        if name == "_safe_job_dir":
            assert len(expr.args) == 1 and ast.unparse(expr.args[0]) == "job_id"
        elif name == "dub_pipeline.new_reference_run_dir":
            assert len(expr.args) == 1
            owned(expr.args[0], seen)
        elif name == "_ref_run_dir":
            # Execute the actual helper, including its job-id lookup and
            # fallback choice, in test_reference_run_helper_prefers_current_job.
            definitions = [node for node in ast.walk(scope(expr))
                           if isinstance(node, ast.FunctionDef) and node.name == name
                           and scope(node) is scope(expr)]
            assert len(definitions) == 1
        else:
            raise AssertionError(f"unverified output directory: {ast.unparse(expr)}")

    for call in _all_extraction_calls(tree):
        owned(call.args[2])


def test_the_out_dirs_come_from_the_jobs_own_id():
    _assert_job_owned_outputs(_source_tree())


@pytest.mark.parametrize("bad_expression", [
    "os.path.dirname(vocals_for_clone)",
    "_safe_job_dir(old_job_id)",
    "dub_pipeline.new_reference_run_dir(os.path.dirname(vocals_for_clone))",
])
def test_ownership_guard_rejects_foreign_directory_at_every_call(bad_expression):
    # Each newly added extraction must be guarded, even if another call and
    # unrelated text elsewhere still mention the correct current-job path.
    tree = _source_tree()
    for call in _all_extraction_calls(tree):
        original = call.args[2]
        call.args[2] = ast.copy_location(ast.parse(bad_expression, mode="eval").body, original)
        with pytest.raises(AssertionError):
            _assert_job_owned_outputs(tree)
        call.args[2] = original


def test_reference_run_helper_prefers_current_job(tmp_path):
    from services import dub_pipeline

    tree = _source_tree()
    helpers = [node for node in ast.walk(tree)
               if isinstance(node, ast.FunctionDef) and node.name == "_ref_run_dir"]
    assert len(helpers) == 1
    own = tmp_path / "current-job"
    foreign = tmp_path / "cached-source-job"
    calls = []

    def job_dir(job_id):
        calls.append(job_id)
        assert job_id == "current-job"
        return str(own)

    namespace = {"_ref_run": {"dir": None}, "_safe_job_dir": job_dir,
                 "job_id": "current-job", "dub_pipeline": dub_pipeline}
    exec(compile(ast.Module(body=helpers, type_ignores=[]), "dub_core_path_helper", "exec"), namespace)
    result = namespace["_ref_run_dir"](str(foreign))
    assert os.path.commonpath([str(own), result]) == str(own)
    assert os.path.isdir(result)
    assert calls == ["current-job"]
    assert namespace["_ref_run_dir"](str(foreign)) == result


def test_extraction_honours_an_out_dir_away_from_the_vocals(tmp_path):
    """Functional half: vocals in one job's dir, clones requested in another's
    — the written reference files must land in the requested dir, so deleting
    the vocals-owning job cannot orphan them."""
    from services.speaker_clone import extract_speaker_clones

    old_job = tmp_path / "job-old"
    new_job = tmp_path / "job-new"
    old_job.mkdir()
    new_job.mkdir()

    sr = 16000
    rng = np.random.default_rng(0)
    audio = (rng.standard_normal(sr * 12) * 0.1).astype(np.float32)
    vocals = old_job / "vocals.wav"
    sf.write(vocals, audio, sr)

    segments = [
        {"start": 0.0, "end": 5.5, "text": "first line", "speaker_id": "Speaker 1"},
        {"start": 6.0, "end": 11.5, "text": "second line", "speaker_id": "Speaker 1"},
    ]
    clones = extract_speaker_clones(str(vocals), segments, str(new_job))
    assert clones, "no clone extracted from 11s of speech"
    for info in clones.values():
        ref = info["ref_audio"]
        assert os.path.dirname(ref) == str(new_job), (
            f"clone reference written next to the vocals ({ref}) instead of "
            f"into the requesting job's dir"
        )
        assert os.path.isfile(ref)

    # The point of the whole exercise: the old job's dir can now die without
    # taking the new job's voice with it.
    import shutil
    shutil.rmtree(old_job)
    for info in clones.values():
        assert os.path.isfile(info["ref_audio"])


def _all_extraction_calls(tree=None):
    """Every pooled-speaker and per-segment extraction, including refreshes."""
    calls = [
        node for node in ast.walk(tree if tree is not None else _source_tree())
        if isinstance(node, ast.Call)
        and getattr(node.func, "id", getattr(node.func, "attr", None))
        in ("extract_speaker_clones", "extract_segment_refs")
    ]
    assert len(calls) >= 3, "expected normal and refresh extraction call sites"
    return calls


def test_no_extraction_call_derives_its_out_dir_from_the_vocals():
    for call in _all_extraction_calls():
        out_dir_arg = ast.unparse(call.args[2])
        assert "dirname" not in out_dir_arg, (
            f"an extraction call writes next to the vocals again "
            f"({ast.unparse(call.func)}: {out_dir_arg!r}) — on a cache hit "
            f"that is an older job's directory (#1331)"
        )


def test_segment_refs_honour_an_out_dir_away_from_the_vocals(tmp_path):
    """Functional half for the DEFAULT path: per-segment clips land in the
    requesting job's dir and survive the vocals-owning job's deletion."""
    from services.speaker_clone import extract_segment_refs

    old_job = tmp_path / "job-old"
    new_job = tmp_path / "job-new"
    old_job.mkdir()
    new_job.mkdir()

    sr = 16000
    rng = np.random.default_rng(1)
    audio = (rng.standard_normal(sr * 12) * 0.1).astype(np.float32)
    vocals = old_job / "vocals.wav"
    sf.write(vocals, audio, sr)

    segments = [
        {"start": 0.0, "end": 5.5, "text": "first line", "speaker_id": "Speaker 1"},
        {"start": 6.0, "end": 11.5, "text": "second line", "speaker_id": "Speaker 1"},
    ]
    refs = extract_segment_refs(str(vocals), segments, str(new_job), seg_ids=[0, 1])
    assert refs, "no per-segment refs extracted from two 5.5s lines"

    import shutil
    shutil.rmtree(old_job)
    for info in refs.values():
        assert os.path.dirname(info["ref_audio"]) == str(new_job)
        assert os.path.isfile(info["ref_audio"])
