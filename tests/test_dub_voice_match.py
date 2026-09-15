"""Dub voice-match toggle (owner report: "still 4 segments different in voice").

Wave 3.2 clones each dub line from a reference cut from ITS OWN source audio —
great prosody match, but the voice IDENTITY drifts line to line, and
heuristic-diarized jobs have no pooled speaker clones to anchor it.
`DubRequest.voice_match` adds user control:

  "per_line"   (DEFAULT) — unchanged behaviour: segment clip preferred,
               per-speaker clone fallback.
  "consistent" — ONE reference per speaker for the whole dub: the pooled
               speaker clone, else (heuristic diarization — no speaker_clones
               at all, the key case) a deterministic pick among that speaker's
               segment clips (longest ≥3 s, tie-break lowest segment id).

Hermetic: fake backend, no DB, no ffmpeg, WAVs under tmp_path — same harness
as tests/test_dub_multispeaker_voice_486.py.
"""
from __future__ import annotations

import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio

import pytest
import torch

from schemas.requests import DubRequest


SR = 24000


# ── Unit: the deterministic consistent pick ─────────────────────────────────


def _job(segments, seg_clones, speaker_clones=None):
    return {
        "segments": segments,
        "segment_clones": seg_clones,
        "speaker_clones": speaker_clones or {},
    }


def test_consistent_pick_prefers_speaker_clone():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job(
        [{"id": "0", "speaker_id": "Speaker 1"}],
        {"0": {"ref_audio": "/v/seg0.wav", "ref_text": "s0", "duration": 9.0}},
        speaker_clones={"Speaker 1": {"ref_audio": "/v/spk1.wav", "ref_text": "one"}},
    )
    ref = resolve_consistent_ref(job, "speaker_1")
    assert ref["ref_audio"] == "/v/spk1.wav"


def test_consistent_pick_no_speaker_clone_longest_clip_at_least_3s_wins():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job(
        [
            {"id": "0", "speaker_id": "Speaker 1"},
            {"id": "1", "speaker_id": "Speaker 1"},
            {"id": "2", "speaker_id": "Speaker 1"},
        ],
        {
            "0": {"ref_audio": "/v/seg0.wav", "ref_text": "s0", "duration": 2.4},
            "1": {"ref_audio": "/v/seg1.wav", "ref_text": "s1", "duration": 5.7},
            "2": {"ref_audio": "/v/seg2.wav", "ref_text": "s2", "duration": 3.2},
        },
    )
    ref = resolve_consistent_ref(job, "speaker_1")
    assert ref["ref_audio"] == "/v/seg1.wav"


def test_consistent_pick_tie_breaks_on_lowest_segment_id():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job(
        [
            {"id": "10", "speaker_id": "Speaker 1"},
            {"id": "2", "speaker_id": "Speaker 1"},
        ],
        {
            "10": {"ref_audio": "/v/seg10.wav", "ref_text": "sA", "duration": 4.0},
            "2": {"ref_audio": "/v/seg2.wav", "ref_text": "sB", "duration": 4.0},
        },
    )
    # Numeric-aware tie-break: 2 < 10 (a plain string sort would pick "10").
    ref = resolve_consistent_ref(job, "speaker_1")
    assert ref["ref_audio"] == "/v/seg2.wav"


def test_consistent_pick_all_clips_short_degrades_to_longest_overall():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job(
        [
            {"id": "0", "speaker_id": "Speaker 1"},
            {"id": "1", "speaker_id": "Speaker 1"},
        ],
        {
            "0": {"ref_audio": "/v/seg0.wav", "ref_text": "s0", "duration": 1.1},
            "1": {"ref_audio": "/v/seg1.wav", "ref_text": "s1", "duration": 2.0},
        },
    )
    ref = resolve_consistent_ref(job, "speaker_1")
    assert ref["ref_audio"] == "/v/seg1.wav"


def test_consistent_pick_scoped_per_speaker_and_memoized():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job(
        [
            {"id": "0", "speaker_id": "Speaker 1"},
            {"id": "1", "speaker_id": "Speaker 2"},
        ],
        {
            "0": {"ref_audio": "/v/seg0.wav", "ref_text": "s0", "duration": 4.0},
            "1": {"ref_audio": "/v/seg1.wav", "ref_text": "s1", "duration": 8.0},
        },
    )
    memo: dict = {}
    # Speaker 2's longer clip must NOT leak into Speaker 1's pick.
    assert resolve_consistent_ref(job, "speaker_1", memo)["ref_audio"] == "/v/seg0.wav"
    assert resolve_consistent_ref(job, "speaker_2", memo)["ref_audio"] == "/v/seg1.wav"
    # Memoized: same object handed back for every later segment of the speaker.
    again = resolve_consistent_ref(job, "speaker_1", memo)
    assert again is memo["speaker_1"]


def test_consistent_pick_unknown_speaker_returns_none():
    from api.routers.dub_generate import resolve_consistent_ref

    job = _job([{"id": "0", "speaker_id": "Speaker 1"}], {})
    assert resolve_consistent_ref(job, "speaker_9") is None


def test_clone_lookup_uses_the_same_portable_speaker_slug_as_profile_ids():
    from api.routers.dub_generate import _find_speaker_clone

    clone = {"ref_audio": "/v/speaker.wav"}
    assert _find_speaker_clone({"SPEAKER_00": clone}, "speaker00") is clone
    assert _find_speaker_clone({"Guest-2!": clone}, "guest_2") is clone


# ── Schema: validation + default ────────────────────────────────────────────


def _minimal_body(**over):
    body = {
        "segments": [{"start": 0.0, "end": 1.0, "text": "hi"}],
        "language": "Auto",
        "language_code": "es",
    }
    body.update(over)
    return body


def test_voice_match_default_is_per_line():
    req = DubRequest(**_minimal_body())
    assert req.voice_match == "per_line"


def test_voice_match_accepts_both_modes_and_rejects_junk():
    assert DubRequest(**_minimal_body(voice_match="consistent")).voice_match == "consistent"
    assert DubRequest(**_minimal_body(voice_match="per_line")).voice_match == "per_line"
    with pytest.raises(Exception):
        DubRequest(**_minimal_body(voice_match="blend"))


# ── Fingerprints: flipping the toggle must read as stale (#281 class) ───────


def test_fingerprint_mixes_in_voice_match_only_when_non_default():
    from services.incremental import segment_fingerprint

    seg = {"text": "hola", "profile_id": "auto:speaker_1"}
    legacy = segment_fingerprint(seg, track_lang="es")
    per_line = segment_fingerprint(seg, track_lang="es", voice_match="per_line")
    consistent = segment_fingerprint(seg, track_lang="es", voice_match="consistent")
    # per_line == legacy: hashes stored by previous builds stay valid.
    assert per_line == legacy
    # consistent differs: flipping the toggle marks segments stale.
    assert consistent != legacy


# ── Generate-time resolution through the real dub_generate path ─────────────


class _RefCapturingModel:
    """Records (ref_audio, ref_text, cache_ref) per call so the test can
    assert which reference the resolver picked and its cache semantics."""

    sampling_rate = SR

    def __init__(self):
        self.refs: list[tuple] = []

    def generate(self, text=None, ref_audio=None, ref_text=None, cache_ref=None, **kwargs):
        self.refs.append((ref_audio, ref_text, cache_ref))
        return [torch.full((1, int(0.5 * SR)), 0.1)]


class _FakeBackend:
    applies_own_mastering = False

    def __init__(self, model):
        self._model = model

    @property
    def sample_rate(self):
        return self._model.sampling_rate

    def generate(self, *a, **kw):
        return self._model.generate(*a, **kw)[0]


# A heuristic-diarized job: NO speaker_clones (extraction is skipped for
# heuristic labels), one per-segment clip per line, single detected speaker.
# dub_core's assignment loop binds every long line to `auto-seg:{its own id}`.
_HEURISTIC_JOB = {
    "duration": 12.0,
    "dubbed_tracks": {},
    "speaker_clones": {},
    "segment_clones": {
        "0": {"ref_audio": "/v/seg0.wav", "ref_text": "seg0 ref", "duration": 3.1},
        "1": {"ref_audio": "/v/seg1.wav", "ref_text": "seg1 ref", "duration": 6.8},
        "2": {"ref_audio": "/v/seg2.wav", "ref_text": "seg2 ref", "duration": 4.0},
        "3": {"ref_audio": "/v/seg3.wav", "ref_text": "seg3 ref", "duration": 2.2},
    },
    "segments": [
        {"id": "0", "speaker_id": "Speaker 1"},
        {"id": "1", "speaker_id": "Speaker 1"},
        {"id": "2", "speaker_id": "Speaker 1"},
        {"id": "3", "speaker_id": "Speaker 1"},
    ],
}

# A diarized job WITH pooled speaker clones + per-segment clips for both segs.
_DIARIZED_JOB = {
    "duration": 6.0,
    "dubbed_tracks": {},
    "speaker_clones": {
        "Speaker 1": {"ref_audio": "/v/spk1.wav", "ref_text": "spk1 ref"},
        "Speaker 2": {"ref_audio": "/v/spk2.wav", "ref_text": "spk2 ref"},
    },
    "segment_clones": {
        "0": {"ref_audio": "/v/seg0.wav", "ref_text": "seg0 ref", "duration": 4.0},
        "1": {"ref_audio": "/v/seg1.wav", "ref_text": "seg1 ref", "duration": 4.0},
    },
    "segments": [
        {"id": "0", "speaker_id": "Speaker 1"},
        {"id": "1", "speaker_id": "Speaker 2"},
    ],
}


@pytest.fixture
def patched_generate(monkeypatch, tmp_path):
    import api.routers.dub_generate as dg

    model = _RefCapturingModel()

    async def _fake_resolve_generation_backend(**kwargs):
        return _FakeBackend(model)

    job_dir = tmp_path / "jobX"
    job_dir.mkdir()
    state = {"job": None}

    monkeypatch.setattr(dg, "resolve_generation_backend", _fake_resolve_generation_backend)
    monkeypatch.setattr(dg, "_get_job", lambda job_id: state["job"])
    monkeypatch.setattr(dg, "_save_job", lambda job_id, j: None)
    monkeypatch.setattr(dg, "DUB_DIR", str(tmp_path))
    monkeypatch.setattr(
        dg, "dub_seg_path",
        lambda job_id, seg_id: str(job_dir / f"seg_{seg_id}.wav"),
    )
    monkeypatch.setattr(dg, "rvc_is_enabled", lambda: False)
    monkeypatch.setattr(dg, "mark_synthetic", lambda wav, sr, **kw: wav)
    monkeypatch.setattr(dg, "apply_mastering", lambda a, sample_rate=None: a)
    monkeypatch.setattr(dg, "get_effect_chain", lambda preset: None)
    monkeypatch.setattr(dg, "apply_effects_chain", lambda a, **k: a)
    monkeypatch.setattr(dg, "normalize_audio", lambda a, target_dBFS=None: a)

    class _StubTaskManager:
        def is_cancelled(self, task_id):
            return False

        async def add_task(self, task_id, task_type, func, *args, **kwargs):
            async for _ in func(*args):
                pass

    monkeypatch.setattr(dg, "task_manager", _StubTaskManager())

    def run(job: dict, body: dict):
        import copy

        state["job"] = copy.deepcopy(job)
        model.refs.clear()
        req = DubRequest(**body)
        asyncio.run(dg.dub_generate("jobX", req))
        return model

    return run


def _heuristic_body(**over):
    """4 lines, all server-default bound to their OWN auto-seg clip — the
    exact shape prepare produces for a heuristic-diarized job."""
    body = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "uno", "profile_id": "auto-seg:0"},
            {"start": 3.0, "end": 6.0, "text": "dos", "profile_id": "auto-seg:1"},
            {"start": 6.0, "end": 9.0, "text": "tres", "profile_id": "auto-seg:2"},
            {"start": 9.0, "end": 12.0, "text": "cuatro", "profile_id": "auto-seg:3"},
        ],
        "segment_ids": ["0", "1", "2", "3"],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        "timing_strategy": "concise",
    }
    body.update(over)
    return body


def test_per_line_default_unchanged_four_different_refs(patched_generate):
    """DEFAULT (voice_match omitted): the reported behaviour — each of the 4
    lines clones from its own clip. Guards that the default didn't change."""
    model = patched_generate(_HEURISTIC_JOB, _heuristic_body())
    assert [r[0] for r in model.refs] == [
        "/v/seg0.wav", "/v/seg1.wav", "/v/seg2.wav", "/v/seg3.wav",
    ]
    # Per-segment single-use refs bypass the prompt cache (#1132).
    assert [r[2] for r in model.refs] == [False, False, False, False]


def test_consistent_heuristic_job_all_lines_share_one_ref(patched_generate):
    """THE key case (fail-before/pass-after): heuristic diarization has no
    speaker_clones, so consistent mode must unify all 4 lines on ONE
    deterministic pick — the longest clip ≥3 s (seg1, 6.8 s)."""
    model = patched_generate(
        _HEURISTIC_JOB, _heuristic_body(voice_match="consistent")
    )
    assert [r[0] for r in model.refs] == ["/v/seg1.wav"] * 4
    assert [r[1] for r in model.refs] == ["seg1 ref"] * 4
    # The shared pick is multi-use → cache it so segments 2..4 reuse the
    # encoded prompt instead of re-encoding per line (#1132 semantics).
    assert [r[2] for r in model.refs] == [True, True, True, True]


def test_consistent_pick_is_deterministic_across_runs(patched_generate):
    """Same job, two runs → byte-identical reference choice."""
    first = list(
        patched_generate(_HEURISTIC_JOB, _heuristic_body(voice_match="consistent")).refs
    )
    second = list(
        patched_generate(_HEURISTIC_JOB, _heuristic_body(voice_match="consistent")).refs
    )
    assert first == second


def test_consistent_auto_binding_uses_speaker_clone_not_segment_clip(patched_generate):
    """With pooled speaker clones, consistent mode ignores the per-segment
    clips (per_line would have preferred them) and multi-uses the clone."""
    body = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "hola", "profile_id": "auto:speaker_1"},
            {"start": 3.0, "end": 6.0, "text": "buenas", "profile_id": "auto:speaker_2"},
        ],
        "segment_ids": ["0", "1"],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        "timing_strategy": "concise",
        "voice_match": "consistent",
    }
    model = patched_generate(_DIARIZED_JOB, body)
    assert model.refs[0] == ("/v/spk1.wav", "spk1 ref", True)
    assert model.refs[1] == ("/v/spk2.wav", "spk2 ref", True)


def test_per_line_auto_binding_still_prefers_segment_clip(patched_generate):
    """The #486 contract is untouched in the default mode."""
    body = {
        "segments": [
            {"start": 0.0, "end": 3.0, "text": "hola", "profile_id": "auto:speaker_1"},
        ],
        "segment_ids": ["0"],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        "timing_strategy": "concise",
        "voice_match": "per_line",
    }
    model = patched_generate(_DIARIZED_JOB, body)
    assert model.refs[0] == ("/v/seg0.wav", "seg0 ref", False)


def test_per_line_cross_speaker_cast_uses_the_selected_speaker(patched_generate):
    """Choosing Speaker 2 on Speaker 1's row must not silently keep that
    row's Speaker 1 segment reference."""
    body = {
        "segments": [
            {
                "start": 0.0,
                "end": 3.0,
                "text": "hola",
                "profile_id": "auto:speaker_2",
            },
        ],
        "segment_ids": ["0"],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        "timing_strategy": "concise",
        "voice_match": "per_line",
    }
    model = patched_generate(_DIARIZED_JOB, body)
    assert model.refs[0] == ("/v/spk2.wav", "spk2 ref", True)


def test_per_line_auto_binding_short_line_uses_speakers_best_video_clip(patched_generate):
    """A cast choice applies to short lines too; they must not fall back to
    the engine default merely because that line has no segment reference."""
    job = {
        **_HEURISTIC_JOB,
        "segment_clones": {
            "0": _HEURISTIC_JOB["segment_clones"]["0"],
            "1": _HEURISTIC_JOB["segment_clones"]["1"],
        },
    }
    body = _heuristic_body()
    body["segments"][2]["profile_id"] = "auto:speaker_1"
    model = patched_generate(job, body)
    assert model.refs[2][0:2] == ("/v/seg1.wav", "seg1 ref")


def test_consistent_explicit_cross_auto_seg_binding_is_honoured(patched_generate):
    """An auto-seg binding to ANOTHER segment's clip can only come from an
    explicit request — consistent mode must not override it. Only the
    self-binding (the server default) joins the speaker-consistent pick."""
    body = _heuristic_body(voice_match="consistent")
    # Segment 0 explicitly cross-bound to segment 3's clip.
    body["segments"][0]["profile_id"] = "auto-seg:3"
    model = patched_generate(_HEURISTIC_JOB, body)
    assert model.refs[0] == ("/v/seg3.wav", "seg3 ref", False)  # explicit wins
    assert [r[0] for r in model.refs[1:]] == ["/v/seg1.wav"] * 3  # rest unified


def test_consistent_pick_rejects_oversized_fallback_reference():
    from api.routers.dub_generate import resolve_consistent_ref
    job = _job(
        [{'id': 'too-long', 'speaker_id': 'Speaker 1'}, {'id': 'usable', 'speaker_id': 'Speaker 1'}],
        {'too-long': {'ref_audio': '/v/long.wav', 'ref_text': 'long', 'duration': 20.92},
         'usable': {'ref_audio': '/v/usable.wav', 'ref_text': 'usable', 'duration': 9.0}},
    )
    assert resolve_consistent_ref(job, 'speaker_1')['ref_audio'] == '/v/usable.wav'
