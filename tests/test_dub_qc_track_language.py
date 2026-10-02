"""QC scores the track actually selected, rather than the last generated text."""
import asyncio
import copy

import pytest


@pytest.mark.parametrize("requested_lang", ["es", None, "unknown", "bn"])
def test_qc_uses_selected_track_authoritative_text(monkeypatch, requested_lang):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline, model_manager

    job = {
        "segments": [{"id": "a", "start": 0.0, "end": 2.0, "text": "ohe prithibi"}],
        "segments_i18n": {"es": {"a": "hola mundo"}, "bn": {"a": "ohe prithibi"}},
        "dubbed_tracks": {"es": {"path": "spanish.wav"}, "bn": {"path": "bengali.wav"}},
        "seg_order": ["a"],
    }
    original = copy.deepcopy(job)
    heard = []
    selected = "bn" if requested_lang == "bn" else "es"
    expected_text = "ohe prithibi" if selected == "bn" else "hola mundo"

    class Backend:
        id = "test-local-asr"
        def transcribe(self, path, **kwargs):
            heard.append(path)
            return {"segments": [{"start": 0.0, "end": 2.0, "text": expected_text}]}

    async def guarded(pool, fn, **kwargs):
        return fn()

    monkeypatch.setattr(dub_export, "_job_dir_or_400", lambda job_id: None)
    monkeypatch.setattr(dub_export, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dub_export, "_dub_artifact", lambda path, *a, **kw: path)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", Backend)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", guarded)
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)

    result = asyncio.run(dub_export.dub_qc_pass("test", lang=requested_lang, drift_threshold=0.5))
    assert heard == ["bengali.wav" if selected == "bn" else "spanish.wav"]
    assert result["flagged_count"] == 0
    assert result["segments"][0]["drift"] == 0.0
    assert job["segments"][0]["text"] == original["segments"][0]["text"]
    assert job["segments_i18n"] == original["segments_i18n"]
    assert job["segments"][0]["qc_recognized"] == expected_text


@pytest.mark.parametrize("strategy,with_plan", [
    ("smart_fit", True),
    ("stretch_video", True),
    ("strict_slot", True),
    ("smart_fit", False),
    ("stretch_video", False),
    ("legacy_source", False),
])
@pytest.mark.parametrize("requested_lang", ["es", None])
@pytest.mark.parametrize("reordered", [False, True])
def test_qc_matches_selected_track_rendered_times(monkeypatch, strategy, with_plan, requested_lang, reordered):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline, model_manager

    source = [{"id": "a", "start": 0.0, "end": 1.0},
              {"id": "b", "start": 1.0, "end": 2.0}]
    fitted = [{"id": "a", "start": 0.0, "end": 3.0},
              {"id": "b", "start": 3.0, "end": 4.0}]
    plan = [{"orig_start": 0.0, "orig_end": 1.0, "new_start": 0.0, "new_end": 3.0},
            {"orig_start": 1.0, "orig_end": 2.0, "new_start": 3.0, "new_end": 4.0}]
    if strategy == "legacy_source":
        source = [{"start": s["start"], "end": s["end"]} for s in source]
    job = {
        "segments": [{"id": "a", "start": 10.0, "end": 11.0, "text": "bengali first"},
                     {"id": "b", "start": 11.0, "end": 12.0, "text": "bengali second"}],
        "segments_i18n": {"es": {"a": "hola", "b": "adios"}},
        # The most recently generated track has a different timeline/strategy.
        "timing_strategy": "concise",
        "dubbed_tracks": {"es": {"path": "spanish.wav", "timing_strategy": "strict_slot" if strategy == "legacy_source" else strategy,
                                   "source_segments": source},
                          "bn": {"path": "bengali.wav", "timing_strategy": "concise"}},
        "seg_order": ["a", "b"],
    }
    if with_plan:
        # Retained plans must not override a selected strict-slot track.
        job["fit_plans"] = {"es": {"fitted_segments": fitted}}
        job["video_stretch_plans"] = {"es": {"plan": plan}}
    timing = (job["segments"] if strategy == "legacy_source" else
              fitted if with_plan and strategy != "strict_slot" else source)
    if strategy == "legacy_source":
        timing = source
    recognized = [dict(t, text=text) for t, text in zip(timing, ["hola", "adios"])]
    if reordered:
        job["segments"].reverse()
        job["seg_order"].reverse()
    original = copy.deepcopy(job)
    asr_calls = []

    class Backend:
        id = "test-local-asr"
        def transcribe(self, path, **kwargs):
            asr_calls.append(path)
            assert path == "spanish.wav"
            return {"segments": recognized}

    async def guarded(pool, fn, **kwargs):
        return fn()

    monkeypatch.setattr(dub_export, "_job_dir_or_400", lambda job_id: None)
    monkeypatch.setattr(dub_export, "_get_job", lambda job_id: job)
    monkeypatch.setattr(dub_export, "_dub_artifact", lambda path, *a, **kw: path)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", Backend)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", guarded)
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)

    if strategy == "legacy_source":
        from fastapi import HTTPException
        with pytest.raises(HTTPException) as error:
            asyncio.run(dub_export.dub_qc_pass("test", lang=requested_lang, drift_threshold=0.5))
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "dub_qc_timing_identity_missing"
        assert asr_calls == []
        assert job == original
        return
    result = asyncio.run(dub_export.dub_qc_pass("test", lang=requested_lang, drift_threshold=0.5))
    assert result["flagged_count"] == 0
    assert [s["drift"] for s in result["segments"]] == [0.0, 0.0]
    for current, old in zip(job["segments"], original["segments"]):
        assert (current["start"], current["end"], current["text"]) == (old["start"], old["end"], old["text"])
    assert job["dubbed_tracks"] == original["dubbed_tracks"]
    assert job["segments_i18n"] == original["segments_i18n"]


def test_render_source_snapshot_preserves_manifest_identity_after_reorder():
    from api.routers.dub_generate import _track_source_segments, _sync_job_segments
    from schemas.requests import DubRequest, DubSegment

    first = DubRequest(language_code="es", segment_ids=["a", "b"], segments=[
        DubSegment(start=0.0, end=1.0, text="hola"),
        DubSegment(start=2.0, end=3.0, text="adios"),
    ])
    job = {"seg_order": ["a", "b"]}
    _sync_job_segments(job, first)
    snapshot = _track_source_segments(job)
    later = DubRequest(language_code="bn", segment_ids=["b", "a"], segments=[
        DubSegment(start=10.0, end=11.0, text="later b"),
        DubSegment(start=12.0, end=13.0, text="later a"),
    ])
    job["seg_order"] = later.segment_ids
    _sync_job_segments(job, later)
    from api.routers.dub_export import _apply_fitted_times
    restored = _apply_fitted_times(job["segments"], snapshot)
    assert [(s["id"], s["start"], s["end"]) for s in restored] == [("b", 2.0, 3.0), ("a", 0.0, 1.0)]
    assert [s["id"] for s in _track_source_segments(job)] == ["b", "a"]


def test_render_source_snapshot_uses_preserved_ids_for_legacy_request():
    from api.routers.dub_generate import _track_source_segments, _sync_job_segments
    from schemas.requests import DubRequest, DubSegment
    job = {"segments": [{"id": "saved", "start": 0.0, "end": 1.0, "text": "original"}]}
    req = DubRequest(language_code="es", segments=[DubSegment(start=2.0, end=3.0, text="hola")])
    _sync_job_segments(job, req)
    assert _track_source_segments(job) == [{"id": "saved", "start": 2.0, "end": 3.0}]


def _legacy_qc_route(monkeypatch, job, recognized):
    from api.routers import dub_export
    from services import asr_backend, dub_pipeline, model_manager
    calls = []
    class Backend:
        id = "test-local-asr"
        def transcribe(self, path, **kwargs):
            calls.append(path)
            return {"segments": recognized}
    async def guarded(pool, fn, **kwargs):
        return fn()
    monkeypatch.setattr(dub_export, "_job_dir_or_400", lambda _: None)
    monkeypatch.setattr(dub_export, "_get_job", lambda _: job)
    monkeypatch.setattr(dub_export, "_dub_artifact", lambda path, *a, **kw: path)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(asr_backend, "load_active_asr_backend", Backend)
    monkeypatch.setattr(asr_backend, "run_transcribe_guarded", guarded)
    monkeypatch.setattr(model_manager, "_get_gpu_pool", lambda: None)
    monkeypatch.setattr(dub_pipeline, "put_job", lambda *a: None)
    monkeypatch.setattr(dub_pipeline, "save_job", lambda *a: None)
    return lambda: asyncio.run(dub_export.dub_qc_pass("test", lang="es", drift_threshold=0.5)), calls


@pytest.mark.parametrize("fit_ids", [["a", "b"], ["b", "a"], ["a", "a"], ["a"], ["a", None], ["a", "foreign"]])
def test_legacy_source_uses_only_complete_smart_fit_identity(monkeypatch, fit_ids):
    from fastapi import HTTPException
    timings = {"a": (0.0, 1.0), "b": (2.0, 3.0)}
    fitted = [dict(id=sid, start=timings.get(sid, (0.0, 1.0))[0],
                   end=timings.get(sid, (0.0, 1.0))[1]) for sid in fit_ids]
    job = {"segments": [{"id": "b", "start": 10.0, "end": 11.0, "text": "later b"},
                        {"id": "a", "start": 12.0, "end": 13.0, "text": "later a"}],
           "segments_i18n": {"es": {"a": "hola", "b": "adios"}}, "seg_order": ["b", "a"],
           "dubbed_tracks": {"es": {"path": "spanish.wav", "timing_strategy": "smart_fit",
               "source_segments": [{"start": 0.0, "end": 1.0}, {"start": 2.0, "end": 3.0}]}},
           "fit_plans": {"es": {"fitted_segments": fitted}}}
    recognized = [{"start": 0.0, "end": 1.0, "text": "hola"},
                  {"start": 2.0, "end": 3.0, "text": "adios"}]
    run, calls = _legacy_qc_route(monkeypatch, job, recognized)
    if set(fit_ids) == {"a", "b"} and len(fit_ids) == 2:
        assert run()["flagged_count"] == 0
        assert calls == ["spanish.wav"]
    else:
        original = copy.deepcopy(job)
        with pytest.raises(HTTPException) as error:
            run()
        assert error.value.status_code == 409
        assert error.value.detail["code"] == "dub_qc_timing_identity_missing"
        assert calls == []
        assert job == original


@pytest.mark.parametrize("map_id", ["a", "foreign"])
def test_legacy_single_line_requires_matching_selected_track_identity(monkeypatch, map_id):
    from fastapi import HTTPException
    job = {"segments": [{"id": "a", "start": 10.0, "end": 11.0, "text": "later"}],
           "segments_i18n": {"es": {map_id: "hola"}}, "seg_order": ["a"],
           "dubbed_tracks": {"es": {"path": "spanish.wav", "timing_strategy": "strict_slot",
                                     "source_segments": [{"start": 0.0, "end": 1.0}]}}}
    run, calls = _legacy_qc_route(monkeypatch, job, [{"start": 0.0, "end": 1.0, "text": "hola"}])
    if map_id == "a":
        assert run()["flagged_count"] == 0
        assert calls == ["spanish.wav"]
        assert job["segments"][0]["start"] == 10.0
    else:
        with pytest.raises(HTTPException) as error:
            run()
        assert error.value.status_code == 409
        assert calls == []


def test_legacy_source_does_not_infer_strategy_from_another_track():
    from api.routers.dub_export import _qc_source_segments
    from fastapi import HTTPException
    job = {"timing_strategy": "smart_fit",
           "dubbed_tracks": {"es": {"source_segments": [{"start": 0.0, "end": 1.0}]}},
           "fit_plans": {"es": {"fitted_segments": [{"id": "a", "start": 0.0, "end": 1.0}]}}}
    with pytest.raises(HTTPException) as error:
        _qc_source_segments(job, "es", [{"id": "a", "start": 10.0, "end": 11.0}])
    assert error.value.status_code == 409


@pytest.mark.parametrize("existing", [[], [{"start": 0.0, "end": 1.0, "text": "old a"},
                                          {"start": 2.0, "end": 3.0, "text": "old b"}]])
def test_regenerate_without_request_ids_persists_current_manifest_identity(monkeypatch, existing):
    from api.routers.dub_generate import _sync_job_segments, _track_source_segments
    from schemas.requests import DubRequest, DubSegment
    req = DubRequest(language_code="es", segments=[DubSegment(start=2.0, end=3.0, text="hola"),
                                                  DubSegment(start=4.0, end=5.0, text="adios")])
    # Actual current-render contract: dub_generate seeds these same positional
    # fallback names before rendering and synchronizing an ID-less request.
    job = {"segments": existing, "seg_order": ["seg_0", "seg_1"]}
    _sync_job_segments(job, req)
    job["dubbed_tracks"] = {"es": {"path": "es.wav", "timing_strategy": "strict_slot",
                                     "source_segments": _track_source_segments(job)}}
    run, calls = _legacy_qc_route(monkeypatch, job, [{"start": 2.0, "end": 3.0, "text": "hola"},
                                                  {"start": 4.0, "end": 5.0, "text": "adios"}])
    assert run()["flagged_count"] == 0
    assert calls == ["es.wav"]
    assert [s["id"] for s in job["segments"]] == ["seg_0", "seg_1"]
    assert job["segments_i18n"]["es"] == {"seg_0": "hola", "seg_1": "adios"}


@pytest.mark.parametrize("order", [None, [], ["seg_0"], ["seg_0", "seg_0"],
                                  ["old_a", "old_b"], [0, 1], ["seg_0", "seg_1", "seg_2"],
                                  ("seg_0", "seg_1")])
def test_sync_does_not_invent_ids_from_invalid_render_order(order):
    from api.routers.dub_generate import _sync_job_segments, _track_source_segments
    from schemas.requests import DubRequest, DubSegment
    req = DubRequest(language_code="es", segments=[DubSegment(start=0.0, end=1.0, text="hola"),
                                                  DubSegment(start=2.0, end=3.0, text="adios")])
    job = {"segments": [{"text": "old a"}, {"text": "old b"}], "seg_order": order}
    _sync_job_segments(job, req)
    assert all(s.get("id") is None for s in job["segments"])
    assert all(s["id"] is None for s in _track_source_segments(job))


def test_sync_request_and_existing_identity_precede_current_manifest_fallback():
    from api.routers.dub_generate import _sync_job_segments
    from schemas.requests import DubRequest, DubSegment
    req = DubRequest(language_code="es", segment_ids=["explicit"], segments=[
        DubSegment(start=0.0, end=1.0, text="hola"), DubSegment(start=2.0, end=3.0, text="adios")])
    job = {"segments": [{"id": "old", "text": "old a"}, {"id": "preserved", "text": "old b"}],
           "seg_order": ["explicit", "seg_1"]}
    _sync_job_segments(job, req)
    assert [s["id"] for s in job["segments"]] == ["explicit", "preserved"]
