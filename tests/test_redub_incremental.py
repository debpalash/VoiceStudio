"""#281 — re-dub must honor transcript edits.

Root cause: the per-segment fingerprint stored after a generate run was
computed from the pydantic-parsed request (defaults filled in: `instruct=""`,
`profile_id=""`, `effect_preset="broadcast"`, `direction` silently dropped),
while the frontend recomputed it from raw editor state (unset keys omitted,
`preset:` voices unexpanded). The two representations never hashed the same,
so after every run EVERY segment was reported "changed" — a 1-line edit
re-dubbed all N lines, and the incremental plan was useless.

Covers:
  - fingerprint parity between the server-side (pydantic) view and the
    client-side (raw dict) view of the same logical segment;
  - back-compat: hashes stored by previous builds still match;
  - `DubSegment.direction` is a real schema field (was silently dropped);
  - end-to-end regen with a mocked TTS engine: an edited line produces a
    DIFFERENT cached seg WAV, an untouched line's cached WAV is reused
    byte-for-byte.
"""
from __future__ import annotations

import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import asyncio
import hashlib
import json

import pytest
import torch

from services import incremental
from schemas.requests import DubRequest, DubSegment


fp = incremental.segment_fingerprint


# ── Fingerprint parity (server-side vs client-side payload shapes) ─────────


def _server_view(seg: DubSegment) -> dict:
    """What dub_generate hashes: pydantic-parsed segment, defaults filled."""
    return {
        "text": seg.text,
        "target_lang": seg.target_lang,
        "profile_id": seg.profile_id,
        "instruct": seg.instruct,
        "speed": seg.speed,
        "direction": seg.direction,
        "effect_preset": seg.effect_preset,
    }


def test_parity_minimal_segment():
    """A segment with only text set must hash identically whether it went
    through pydantic (defaults filled in) or came raw from the editor."""
    server = _server_view(DubSegment(start=0.0, end=1.0, text="Hola"))
    client = {"text": "Hola"}  # frontend omits unset keys
    assert fp(server) == fp(client)


def test_parity_with_null_and_empty_string_defaults():
    server = _server_view(DubSegment(start=0.0, end=1.0, text="Hola"))
    client = {
        "text": "Hola",
        "target_lang": None,
        "profile_id": "",
        "instruct": "",
        "speed": None,
        "direction": None,
    }
    assert fp(server) == fp(client)


def test_parity_int_vs_float_speed():
    """JS sends `speed: 1`, pydantic parses `1.0` — same fingerprint."""
    assert fp({"text": "x", "speed": 1}) == fp({"text": "x", "speed": 1.0})


def test_effect_preset_change_is_still_detected():
    """Canonicalisation must not erase real preset changes."""
    assert fp({"text": "x", "effect_preset": "cinematic"}) != fp({"text": "x"})
    assert fp({"text": "x", "effect_preset": "broadcast"}) == fp({"text": "x"})


def test_backcompat_with_hashes_stored_by_previous_builds():
    """Old builds hashed `{field: value or ""}` with pydantic defaults
    (effect_preset="broadcast"). Stored seg_hashes in existing
    omnivoice_data/ projects must stay valid for unchanged segments."""
    legacy_payload = {
        "text": "Hola", "target_lang": "", "profile_id": "", "instruct": "",
        "speed": "", "direction": "", "effect_preset": "broadcast",
    }
    legacy_hash = hashlib.sha1(
        json.dumps(legacy_payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    assert fp({"text": "Hola"}) == legacy_hash


def test_one_edit_marks_exactly_one_segment_stale():
    """The #281 scenario: generate stored server-side hashes; the editor
    recomputes with client-side payloads; ONE text edit → ONE stale line."""
    server_segs = [
        DubSegment(start=0.0, end=1.0, text="Line one"),
        DubSegment(start=1.0, end=2.0, text="Line two"),
        DubSegment(start=2.0, end=3.0, text="Line three"),
    ]
    stored = {str(i): fp(_server_view(s)) for i, s in enumerate(server_segs)}

    client_segs = [
        {"id": "0", "text": "Line one"},
        {"id": "1", "text": "Line two EDITED"},
        {"id": "2", "text": "Line three"},
    ]
    plan = incremental.plan_incremental(client_segs, stored_hashes=stored)
    assert plan["stale"] == ["1"]
    assert plan["fresh"] == ["0", "2"]


# ── DubSegment.direction (was silently dropped by pydantic) ────────────────


def test_dubsegment_accepts_direction():
    seg = DubSegment(start=0.0, end=1.0, text="hi", direction="urgent, whispered")
    assert seg.direction == "urgent, whispered"
    # default stays None so old payloads parse unchanged
    assert DubSegment(start=0.0, end=1.0, text="hi").direction is None


def test_direction_change_flips_fingerprint():
    base = _server_view(DubSegment(start=0.0, end=1.0, text="hi"))
    directed = _server_view(DubSegment(start=0.0, end=1.0, text="hi", direction="urgent"))
    assert fp(base) != fp(directed)


# ── End-to-end regen with a mocked TTS engine ──────────────────────────────


class _FakeModel:
    """Deterministic 'TTS engine': output amplitude depends on the text, so
    a text edit provably changes the rendered audio bytes."""

    sampling_rate = 24000

    def __init__(self):
        self.calls: list[str] = []

    def generate(self, text=None, **kwargs):
        self.calls.append(text)
        h = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
        val = 0.1 + (h % 1000) / 2000.0
        n = int(0.5 * self.sampling_rate)
        return [torch.full((1, n), val)]


class _FakeBackend:
    """Adapts the list-returning _FakeModel above to the TTSBackend.generate()
    contract (a single tensor, not a list) that resolve_generation_backend()
    now hands dub_generate.py (issue #312 class)."""

    applies_own_mastering = False

    def __init__(self, model):
        self._model = model

    @property
    def sample_rate(self):
        return self._model.sampling_rate

    def generate(self, *a, **kw):
        return self._model.generate(*a, **kw)[0]


@pytest.fixture
def patched_generate(monkeypatch, tmp_path):
    """Patch api.routers.dub_generate so `_stream` runs hermetically:
    fake model, no DB, no watermark/DSP, WAVs under tmp_path."""
    import api.routers.dub_generate as dg

    model = _FakeModel()

    async def _fake_resolve_generation_backend(**kwargs):
        return _FakeBackend(model)

    job = {
        "duration": 2.0,
        "dubbed_tracks": {},
        "speaker_clones": {},
    }
    job_dir = tmp_path / "jobX"
    job_dir.mkdir()

    monkeypatch.setattr(dg, "resolve_generation_backend", _fake_resolve_generation_backend)
    monkeypatch.setattr(dg, "_get_job", lambda job_id: job)
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

    events: list[str] = []

    class _StubTaskManager:
        def is_cancelled(self, task_id):
            return False

        async def add_task(self, task_id, task_type, func, *args, **kwargs):
            async for evt in func(*args):
                events.append(evt)

    monkeypatch.setattr(dg, "task_manager", _StubTaskManager())

    def run(body: dict) -> list[dict]:
        events.clear()
        req = DubRequest(**body)
        asyncio.run(dg.dub_generate("jobX", req))
        parsed = []
        for e in events:
            line = e.strip()
            if line.startswith("data: "):
                parsed.append(json.loads(line[len("data: "):]))
        return parsed

    return run, model, job, job_dir


def _body(segments, **extra):
    return {
        "segments": segments,
        "segment_ids": [str(i) for i in range(len(segments))],
        "language": "Auto",
        "language_code": "es",
        "num_step": 4,
        **extra,
    }


def test_edited_line_produces_different_cached_output(patched_generate):
    run, model, job, job_dir = patched_generate

    segs = [
        {"start": 0.0, "end": 1.0, "text": "Buenos dias"},
        {"start": 1.0, "end": 2.0, "text": "Hasta luego"},
    ]

    # ── First full run: both lines rendered, hashes stored ──
    parsed = run(_body(segs))
    done = [p for p in parsed if p.get("type") == "done"]
    assert done, f"no done event in {parsed}"
    seg_hashes = done[0]["seg_hashes"]
    assert set(seg_hashes) == {"0", "1"}
    assert model.calls == ["Buenos dias", "Hasta luego"]

    # P1.3 — per-segment WAVs are keyed by the track language now.
    wav0_v1 = (job_dir / "seg_es_0.wav").read_bytes()
    wav1_v1 = (job_dir / "seg_es_1.wav").read_bytes()

    # ── User edits line 0; client-side recompute marks ONLY it stale ──
    edited = [
        {"start": 0.0, "end": 1.0, "text": "Buenas noches"},
        {"start": 1.0, "end": 2.0, "text": "Hasta luego"},
    ]
    plan = incremental.plan_incremental(
        [{"id": "0", "text": "Buenas noches"}, {"id": "1", "text": "Hasta luego"}],
        stored_hashes=seg_hashes,
        track_lang="es",  # the recompute names the track it's judging (P1.3)
    )
    assert plan["stale"] == ["0"]
    assert plan["fresh"] == ["1"]

    # ── Regen only the stale line ──
    model.calls.clear()
    parsed = run(_body(edited, regen_only=plan["stale"]))
    done = [p for p in parsed if p.get("type") == "done"]
    assert done, f"no done event in {parsed}"

    # TTS ran exactly once, with the edited text
    assert model.calls == ["Buenas noches"]

    wav0_v2 = (job_dir / "seg_es_0.wav").read_bytes()
    wav1_v2 = (job_dir / "seg_es_1.wav").read_bytes()
    # the edited line's cached audio changed…
    assert wav0_v2 != wav0_v1
    # …and the untouched line's cached audio was reused as-is
    assert wav1_v2 == wav1_v1

    # stored hash for the edited line was refreshed to the new content
    new_hashes = done[0]["seg_hashes"]
    assert new_hashes["0"] != seg_hashes["0"]
    assert new_hashes["1"] == seg_hashes["1"]

    # the final dubbed track was rebuilt
    assert (job_dir / "dubbed_es.wav").exists()


def test_full_rerun_rerenders_edited_text(patched_generate):
    """Plain 'Generate Dub' (no regen_only) must always use the new text."""
    run, model, job, job_dir = patched_generate

    run(_body([{"start": 0.0, "end": 1.0, "text": "primero"}]))
    first = (job_dir / "seg_es_0.wav").read_bytes()

    run(_body([{"start": 0.0, "end": 1.0, "text": "segundo"}]))
    second = (job_dir / "seg_es_0.wav").read_bytes()

    assert model.calls == ["primero", "segundo"]
    assert first != second


# ── P1.3 — per-track WAV cache + per-language fingerprints ──────────────────
#
# The per-segment cache used to be keyed by job+segment only, and seg_hashes
# was one flat map — so on a multi-language job, "Regen N changed" spliced
# cached WAVs FROM THE LAST-GENERATED LANGUAGE into the current track, and
# staleness was judged against whatever language ran last.


def _amp_for(text: str) -> float:
    """The deterministic amplitude _FakeModel renders for `text`."""
    h = int(hashlib.sha1(text.encode("utf-8")).hexdigest()[:8], 16)
    return 0.1 + (h % 1000) / 2000.0


def _track_sample(job_dir, lang: str, t_seconds: float, sr: int = 24000) -> float:
    """One sample (as float in [-1, 1]) of the final dubbed_{lang}.wav."""
    import wave
    with wave.open(str(job_dir / f"dubbed_{lang}.wav"), "rb") as wf:
        assert wf.getframerate() == sr
        wf.setpos(int(t_seconds * sr))
        frame = wf.readframes(1)
    return int.from_bytes(frame, "little", signed=True) / 32767.0


_ES_SEGS = [
    {"start": 0.0, "end": 1.0, "text": "Buenos dias"},
    {"start": 1.0, "end": 2.0, "text": "Hasta luego"},
]
_BN_SEGS = [
    {"start": 0.0, "end": 1.0, "text": "shubho sokal"},
    {"start": 1.0, "end": 2.0, "text": "abar dekha hobe"},
]


def test_two_track_regen_never_splices_other_language(patched_generate):
    """The P1.3 headline bug: regen on track A after generating track B used
    to mix B's cached WAVs into A (the cache wasn't language-keyed)."""
    run, model, job, job_dir = patched_generate

    run(_body(_ES_SEGS))                      # track 1: es
    run(_body(_BN_SEGS, language_code="bn"))  # track 2: bn
    # Each track owns its cache files now.
    assert (job_dir / "seg_es_1.wav").exists()
    assert (job_dir / "seg_bn_1.wav").exists()

    # Regen only line 0 of the es track. Line 1 must be reused from the ES
    # cache — before the fix the un-keyed seg_1.wav held bn's audio.
    model.calls.clear()
    run(_body(_ES_SEGS, regen_only=["0"]))
    assert model.calls == ["Buenos dias"]

    # Mid-audio sample of line 1 in the rebuilt es track (0.5 s of natural
    # audio at slot start 1.0 s → sample at 1.25 s, clear of the 15 ms fades).
    got = _track_sample(job_dir, "es", 1.25)
    assert abs(got - _amp_for("Hasta luego")) < 0.01, "es track must carry es audio"
    assert abs(got - _amp_for("abar dekha hobe")) > 0.01, "bn audio spliced into es track"


def test_legacy_single_track_job_still_hits_its_old_cache(patched_generate):
    """On-disk back-compat: a job rendered by previous builds only has
    un-keyed seg_{id}.wav files. With a single language on the job they are
    unambiguous and must keep being reused (no forced re-render)."""
    run, model, job, job_dir = patched_generate

    run(_body(_ES_SEGS))
    # Simulate the pre-upgrade cache: only legacy names on disk.
    for i in range(2):
        (job_dir / f"seg_es_{i}.wav").rename(job_dir / f"seg_{i}.wav")

    model.calls.clear()
    run(_body(_ES_SEGS, regen_only=[]))  # pure re-mix, reuse everything
    assert model.calls == []             # no TTS — cache hit
    got = _track_sample(job_dir, "es", 1.25)
    assert abs(got - _amp_for("Hasta luego")) < 0.01, "legacy cache not reused"


def test_legacy_cache_ignored_once_job_has_another_language(patched_generate):
    """A multi-track job's un-keyed files hold whichever language wrote them
    last — ambiguous, so they must never be spliced into a track again."""
    run, model, job, job_dir = patched_generate

    run(_body(_ES_SEGS))
    for i in range(2):
        (job_dir / f"seg_es_{i}.wav").rename(job_dir / f"seg_{i}.wav")
    # Another language exists on the job → the legacy files are ambiguous.
    job["dubbed_tracks"]["bn"] = {"path": str(job_dir / "dubbed_bn.wav"),
                                  "language": "Bengali", "language_code": "bn"}

    model.calls.clear()
    run(_body(_ES_SEGS, regen_only=[]))
    # Missing unambiguous speech must be regenerated, never padded with silence.
    assert model.calls == ["Buenos dias", "Hasta luego"]
    assert abs(_track_sample(job_dir, "es", 1.25) - _amp_for("Hasta luego")) < 0.01


def test_seg_hashes_stored_per_language_with_flat_mirror(patched_generate):
    """seg_hashes_by_lang keeps every track's fingerprints; the flat
    seg_hashes stays = the CURRENT track's map (what every legacy consumer
    already assumed it meant)."""
    run, model, job, job_dir = patched_generate

    run(_body(_ES_SEGS))
    es_hashes = dict(job["seg_hashes_by_lang"]["es"])
    run(_body(_BN_SEGS, language_code="bn"))

    assert set(job["seg_hashes_by_lang"]) == {"es", "bn"}
    # es hashes survived the bn generate (single-slot loss was the bug)…
    assert job["seg_hashes_by_lang"]["es"] == es_hashes
    # …and differ from bn's (language is part of the fingerprint).
    assert job["seg_hashes_by_lang"]["bn"] != es_hashes
    # Flat mirror = last-generated track.
    assert job["seg_hashes"] == job["seg_hashes_by_lang"]["bn"]


# ── Language-scoped fingerprints + migration semantics (unit) ──────────────


def test_track_lang_scopes_fingerprint():
    assert fp({"text": "x"}, track_lang="es") != fp({"text": "x"}, track_lang="bn")
    # No lang → the legacy payload, byte-for-byte (old stored hashes keep
    # their values; see test_backcompat_with_hashes_stored_by_previous_builds).
    assert fp({"text": "x"}, track_lang=None) == fp({"text": "x"})
    # A legacy (lang-less) hash never vouches for a lang-scoped track.
    assert fp({"text": "x"}) != fp({"text": "x"}, track_lang="es")


def test_plan_incremental_track_lang_threads_through():
    stored = {"0": fp({"text": "hola"}, track_lang="es")}
    plan = incremental.plan_incremental(
        [{"id": "0", "text": "hola"}], stored_hashes=stored, track_lang="es",
    )
    assert plan["fresh"] == ["0"]
    # Same hashes judged for another track → stale (never reuse cross-lang).
    plan = incremental.plan_incremental(
        [{"id": "0", "text": "hola"}], stored_hashes=stored, track_lang="bn",
    )
    assert plan["stale"] == ["0"]


def test_legacy_flat_seg_hashes_attributed_to_last_generated_language():
    from api.routers.dub_generate import _seg_hashes_by_lang
    # The flat map could only describe the job's last-generated track.
    job = {"seg_hashes": {"0": "abc"}, "language_code": "es"}
    assert _seg_hashes_by_lang(job) == {"es": {"0": "abc"}}
    assert job["seg_hashes_by_lang"] == {"es": {"0": "abc"}}
    # Unknown language → dropped (stale reads → clean regen; never a guess).
    job = {"seg_hashes": {"0": "abc"}}
    assert _seg_hashes_by_lang(job) == {}
    # Already migrated → returned as-is, no double migration.
    job = {"seg_hashes_by_lang": {"bn": {"1": "def"}}, "seg_hashes": {"0": "abc"},
           "language_code": "es"}
    assert _seg_hashes_by_lang(job) == {"bn": {"1": "def"}}


def test_legacy_seg_cache_gate():
    from api.routers.dub_generate import _legacy_seg_cache_ok
    # No tracks yet / only this language → legacy files are unambiguous.
    assert _legacy_seg_cache_ok({}, "es")
    assert _legacy_seg_cache_ok({"dubbed_tracks": {"es": {}}}, "es")
    # Any OTHER language on the job → ambiguous, never reuse.
    assert not _legacy_seg_cache_ok({"dubbed_tracks": {"bn": {}}}, "es")
    assert not _legacy_seg_cache_ok({"dubbed_tracks": {"es": {}, "bn": {}}}, "es")
