"""POST /convert — Studio's speech-to-speech voice changer.

Contract under test (all engine/ASR layers stubbed — no GPU, no weights):
  * happy path: source clip → active-ASR transcript (word_timestamps=False)
    → TTS conditioned on the chosen profile's reference audio → saved take
    returned as ``{audio_url, text, duration_s}`` with a 'convert' history row;
  * a missing profile is a strict 404 (unlike /generate's silent skip);
  * an empty transcript is a 422 — nothing to convert;
  * a clone-less active engine is refused with the actionable 400 from
    ``resolve_generation_backend(require_cloning=True)``;
  * a TTS-only install (no ASR weights) answers the typed 409 download CTA;
  * duration match: the atempo ratio is clamped to one ffmpeg stage
    ([0.5, 2.0]) and lands on the ffmpeg argv; the toggle is honored;
  * the router is registered on the app (a real request routes, and the
    committed route snapshot carries POST /convert).
"""
import asyncio
import contextlib
import io
import importlib
import os
import re
import tempfile
import uuid

import pytest
import torch
from fastapi import HTTPException

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def _tts_mod():
    return importlib.import_module("services.tts_backend")


def _vc_mod():
    return importlib.import_module("api.routers.voice_convert")


def _make_fake_engine(engine_id, *, cloning=True):
    class _FakeEngine(_tts_mod().TTSBackend):
        id = engine_id
        display_name = f"Fake Convert Engine ({engine_id})"
        applies_own_mastering = False
        gpu_compat = ("cpu",)
        supports_cloning = cloning
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 24000)  # 1 s of silence

    return _FakeEngine


class _FakeASR:
    id = "fake-asr"

    def __init__(self, result):
        self.result = result
        self.calls: list = []

    def transcribe(self, audio_path, *, word_timestamps=True):
        self.calls.append({"audio_path": audio_path, "word_timestamps": word_timestamps})
        return self.result


class _LangASR(_FakeASR):
    """ASR whose ``transcribe`` accepts a language pin (like MLX/faster-whisper)."""

    def transcribe(self, audio_path, *, word_timestamps=True, language=None):
        self.calls.append({"audio_path": audio_path, "word_timestamps": word_timestamps,
                           "language": language})
        return self.result


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def _init_db():
    from core.db import init_db

    init_db()


def _delete_profile_and_takes(pid):
    """Fixture teardown: remove the profile, its history rows AND their WAVs —
    a leaked take in OUTPUTS_DIR would bleed into later tests."""
    from core.config import OUTPUTS_DIR
    from core.db import db_conn

    with db_conn() as conn:
        takes = [
            row["audio_path"]
            for row in conn.execute(
                "SELECT audio_path FROM generation_history WHERE profile_id=?", (pid,)
            ).fetchall()
        ]
        conn.execute("DELETE FROM generation_history WHERE profile_id=?", (pid,))
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))
    for name in takes:
        if name:
            with contextlib.suppress(OSError):
                os.remove(os.path.join(OUTPUTS_DIR, os.path.basename(name)))


@pytest.fixture()
def clone_profile(_init_db):
    from core.db import db_conn

    pid = f"vp-conv-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, ref_audio_path, ref_text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "Convert Target", "clone", "convert-ref.wav", "reference words", 0.0),
        )
    yield pid
    _delete_profile_and_takes(pid)


@pytest.fixture()
def transcriptless_profile(_init_db):
    """Clone profile with a reference clip but NO stored transcript — the shape
    POST /profiles produces when the user doesn't type one (#1032)."""
    from core.db import db_conn

    pid = f"vp-convt-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, ref_audio_path, ref_text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "Blank Transcript", "clone", "convert-ref.wav", "", 0.0),
        )
    yield pid
    _delete_profile_and_takes(pid)


def _wire_stubs(monkeypatch, *, engine_cls, asr):
    """Point the active engine + ASR seams at the fakes (no weights load)."""
    import services.asr_backend as ab

    monkeypatch.setitem(_tts_mod()._REGISTRY, engine_cls.id, engine_cls)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", engine_cls.id)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: None)
    monkeypatch.setattr(ab, "load_active_asr_backend", lambda **kw: asr)


def _post_convert(client, pid, **extra):
    data = {"profile_id": pid}
    data.update(extra)
    return client.post(
        "/convert",
        data=data,
        files={"audio": ("source.wav", b"RIFF-fake-wav-bytes", "audio/wav")},
    )


# ── The route ───────────────────────────────────────────────────────────────


def test_convert_native_voice_uses_full_description(client, monkeypatch, _init_db):
    """Converting with VoxCPM2 preserves a saved profile's free-form prose."""
    import json
    from core.db import db_conn

    fake = _make_fake_engine(f"native-conv-{uuid.uuid4().hex[:6]}")
    fake.instruct_vocabulary = "freeform"
    asr = _FakeASR({"text": "Hello from a designed voice", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)
    pid = f"native-conv-{uuid.uuid4().hex[:8]}"
    desc = "A warm, breathy adult voice speaking with a soft Japanese accent."
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles "
            "(id, name, kind, instruct, vd_states, design_recipe, created_at) "
            "VALUES (?, 'Native design', 'design', 'female', ?, ?, 0)",
            (pid, json.dumps({"Gender": "female"}), json.dumps({
                "description": desc, "picks": {"Pitch": "low pitch"},
                "engine": fake.id,
            })),
        )
    try:
        res = _post_convert(client, pid, match_duration="0")
        assert res.status_code == 200, res.text
        assert fake.calls[0][1]["instruct"] == desc + ", low pitch"
    finally:
        _delete_profile_and_takes(pid)


def test_convert_happy_path(client, monkeypatch, clone_profile):
    """ASR text → TTS with the profile's reference clip → saved take with a
    'convert' history row, answered as {audio_url, text, duration_s}."""
    from core.config import VOICES_DIR
    from core.db import db_conn

    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "hello there world", "segments": [
        {"start": 0.0, "end": 1.5, "text": "hello there world"},
    ]})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0")
    assert res.status_code == 200, res.text
    body = res.json()

    assert re.fullmatch(r"/audio/[0-9a-f-]{8}\.wav", body["audio_url"])
    assert "hello there world" in body["text"].lower()
    assert body["duration_s"] == pytest.approx(1.0, abs=0.05)

    # ASR ran on the upload without word timestamps (the text is all we need).
    assert asr.calls and asr.calls[0]["word_timestamps"] is False

    # TTS was conditioned on the PROFILE's reference clip + stored transcript.
    assert len(fake.calls) == 1
    gen_text, gen_kwargs = fake.calls[0]
    assert "hello there world" in gen_text.lower()
    assert gen_kwargs["ref_audio"] == os.path.join(VOICES_DIR, "convert-ref.wav")
    assert gen_kwargs["ref_text"] == "reference words"

    # The take exists on disk and its history row is a 'convert'.
    from core.config import OUTPUTS_DIR
    take = body["audio_url"].rsplit("/", 1)[-1]
    assert os.path.isfile(os.path.join(OUTPUTS_DIR, take))
    with db_conn() as conn:
        row = conn.execute(
            "SELECT mode, profile_id FROM generation_history WHERE id=?",
            (body["id"],),
        ).fetchone()
    assert row["mode"] == "convert"
    assert row["profile_id"] == clone_profile


def test_convert_rejects_an_unavailable_runtime_profile(
    client, monkeypatch, clone_profile,
):
    base = _make_fake_engine(f"fake-unavailable-{uuid.uuid4().hex[:6]}")

    class UnavailableRuntime(base):
        @classmethod
        def runtime_compute_profile(cls, caps):
            return {
                "gpu_compat": ("cpu",),
                "min_vram_gb": 0.0,
                "effective_device": "cpu",
                "routing_status": "unavailable",
                "routing_reason": "configured native device is unavailable",
                "runtime_hardware_family": None,
                "runtime_vram_gb": None,
            }

    asr = _FakeASR({"text": "hello there"})
    _wire_stubs(monkeypatch, engine_cls=UnavailableRuntime, asr=asr)
    backend = UnavailableRuntime()

    async def resolve_backend(**_kwargs):
        return backend

    monkeypatch.setattr(
        _tts_mod(), "resolve_generation_backend", resolve_backend,
    )

    async def transcribe_source(*_args, **_kwargs):
        return {"text": "hello there", "segments": []}

    monkeypatch.setattr(_vc_mod(), "_transcribe_source", transcribe_source)

    res = _post_convert(client, clone_profile, match_duration="0")

    assert res.status_code == 400
    assert res.json()["detail"] == "configured native device is unavailable"
    assert UnavailableRuntime.calls == []


def test_convert_transcribes_blank_profile_reference_and_persists(
    client, monkeypatch, transcriptless_profile,
):
    """/generate parity (#308/#1032): a clone profile with a blank stored
    transcript gets its reference clip transcribed (best-effort) before TTS,
    the engine sees that transcript instead of None, and it's cached onto the
    profile row so it runs once, not per convert."""
    import services.asr_backend as ab
    from core.db import db_conn

    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "hello there"})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    ref_calls = []

    def _counting_ref_transcribe(audio_path):
        ref_calls.append(audio_path)
        return "auto transcript words"

    monkeypatch.setattr(ab, "transcribe_reference", _counting_ref_transcribe)

    res = _post_convert(client, transcriptless_profile, match_duration="0")
    assert res.status_code == 200, res.text

    from core.config import VOICES_DIR
    assert ref_calls == [os.path.join(VOICES_DIR, "convert-ref.wav")]
    _, gen_kwargs = fake.calls[0]
    assert gen_kwargs["ref_text"] == "auto transcript words"
    with db_conn() as conn:
        row = conn.execute(
            "SELECT ref_text FROM voice_profiles WHERE id=?", (transcriptless_profile,)
        ).fetchone()
    assert row["ref_text"] == "auto transcript words"


def test_convert_stored_transcript_skips_reference_transcribe(
    client, monkeypatch, clone_profile,
):
    """A profile that already carries a transcript never re-runs reference ASR
    (the #1032 perf-regression class)."""
    import services.asr_backend as ab

    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    _wire_stubs(monkeypatch, engine_cls=fake, asr=_FakeASR({"text": "hello"}))

    def _boom(audio_path):
        raise AssertionError("stored transcript must short-circuit reference ASR")

    monkeypatch.setattr(ab, "transcribe_reference", _boom)

    res = _post_convert(client, clone_profile, match_duration="0")
    assert res.status_code == 200, res.text
    assert fake.calls[0][1]["ref_text"] == "reference words"


def test_convert_missing_profile_is_404(client, monkeypatch):
    """No target voice, no convert — strict 404, never a profileless render."""
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    _wire_stubs(monkeypatch, engine_cls=fake, asr=_FakeASR({"text": "hi"}))

    res = _post_convert(client, "vp-does-not-exist")
    assert res.status_code == 404
    assert "doesn't exist" in res.json()["detail"]
    assert fake.calls == []


def test_convert_empty_transcript_is_422(client, monkeypatch, clone_profile):
    """Silence/music in, nothing recognized → 422 with guidance, no TTS run."""
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile)
    assert res.status_code == 422
    assert "No speech was recognized" in res.json()["detail"]
    assert fake.calls == []


def test_convert_refuses_clone_less_engine(client, monkeypatch, clone_profile):
    """The shared require_cloning gate answers 400 with the switch-engine CTA
    before any ASR/TTS work happens."""
    fake = _make_fake_engine(f"fake-noclone-{uuid.uuid4().hex[:6]}", cloning=False)
    asr = _FakeASR({"text": "hello"})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile)
    assert res.status_code == 400
    assert "doesn't support voice cloning" in res.json()["detail"]
    assert asr.calls == []
    assert fake.calls == []


def test_convert_asr_missing_is_typed_409(client, monkeypatch, clone_profile):
    """TTS-only install: the same typed asr_model_missing 409 (+ download CTA
    payload) every other ASR consumer answers — never a silent download."""
    import services.asr_backend as ab

    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    _wire_stubs(monkeypatch, engine_cls=fake, asr=_FakeASR({"text": "hi"}))
    payload = {
        "error": "asr_model_missing",
        "missing_repo_id": "org/some-whisper",
        "recommended": {"repo_id": "org/some-whisper", "label": "Whisper", "size_gb": 1.5},
    }
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: payload)

    res = _post_convert(client, clone_profile)
    assert res.status_code == 409
    detail = res.json()["detail"]
    assert detail["error"] == "asr_model_missing"
    assert detail["recommended"]["repo_id"] == "org/some-whisper"
    assert fake.calls == []


def test_convert_rejects_oversized_upload_before_engine_work(
    client, monkeypatch, clone_profile,
):
    """The bounded copy returns 413 before loading ASR or TTS."""
    vc = _vc_mod()
    monkeypatch.setattr(vc, "_MAX_SOURCE_AUDIO_BYTES", 8)

    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "should not run"})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile)
    assert res.status_code == 413
    assert "maximum 64 MB" in res.json()["detail"]
    assert asr.calls == []
    assert fake.calls == []


def test_source_upload_is_read_in_bounded_chunks(monkeypatch):
    """The upload reader never requests or retains the complete body."""
    vc = _vc_mod()
    monkeypatch.setattr(vc, "_MAX_SOURCE_AUDIO_BYTES", 8)
    monkeypatch.setattr(vc, "_UPLOAD_CHUNK_BYTES", 4)

    class _Upload:
        def __init__(self):
            self.source = io.BytesIO(b"123456789")
            self.read_sizes = []

        async def read(self, size):
            self.read_sizes.append(size)
            return self.source.read(size)

    upload = _Upload()
    destination = io.BytesIO()
    with pytest.raises(HTTPException) as exc:
        asyncio.run(vc._copy_source_upload(upload, destination))

    assert exc.value.status_code == 413
    assert upload.read_sizes == [4, 4, 4]
    assert destination.getvalue() == b"12345678"


def test_convert_timeout_defers_source_cleanup_until_asr_worker_drains(monkeypatch):
    """The request may finish while native ASR still reads its source file."""
    import services.asr_backend as ab
    from api.routers.generation import _TempReferenceLease

    vc = _vc_mod()
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: None)
    release_worker = None

    async def _timeout(_pool, _fn, **kwargs):
        nonlocal release_worker
        release_worker = kwargs["on_abandon"]
        raise ab.ASRTimeoutError("timed out")

    monkeypatch.setattr(ab, "run_transcribe_guarded", _timeout)
    source = tempfile.NamedTemporaryFile(delete=False, suffix=".wav")
    source.close()
    lease = _TempReferenceLease(source.name)

    try:
        with pytest.raises(HTTPException) as exc:
            asyncio.run(vc._transcribe_source(source.name, source_lease=lease))
        assert exc.value.status_code == 504
        lease.finish_request()
        assert os.path.exists(source.name)

        assert release_worker is not None
        release_worker()
        assert not os.path.exists(source.name)
    finally:
        with contextlib.suppress(OSError):
            os.remove(source.name)


def test_convert_match_duration_toggle(client, monkeypatch, clone_profile):
    """match_duration defaults ON (stretch invoked with the source duration);
    '0' skips the stretch entirely."""
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "hello", "segments": [{"start": 0.0, "end": 2.5, "text": "hello"}]})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    import services.ffmpeg_utils as ff
    async def _no_probe(path, *, allowed_root):
        return None  # force the ASR-segment fallback for the source duration
    monkeypatch.setattr(ff, "probe_duration", _no_probe)

    vc = _vc_mod()
    stretch_calls = []

    async def _fake_match(audio_tensor, sample_rate, source_duration_s):
        stretch_calls.append(source_duration_s)
        return audio_tensor

    monkeypatch.setattr(vc, "_match_source_duration", _fake_match)

    assert _post_convert(client, clone_profile).status_code == 200  # default on
    assert stretch_calls == [2.5]

    assert _post_convert(client, clone_profile, match_duration="0").status_code == 200
    assert stretch_calls == [2.5]  # unchanged — no second stretch


# ── Duration match internals (the atempo clamp + argv) ─────────────────────


def test_clamped_tempo_ratio():
    vc = _vc_mod()
    assert vc._clamped_tempo_ratio(6.0, 5.0) == pytest.approx(1.2)   # speed up
    assert vc._clamped_tempo_ratio(4.0, 5.0) == pytest.approx(0.8)   # slow down
    assert vc._clamped_tempo_ratio(10.0, 2.0) == 2.0                 # clamp high
    assert vc._clamped_tempo_ratio(1.0, 10.0) == 0.5                 # clamp low
    assert vc._clamped_tempo_ratio(5.0, 5.0) is None                 # already matched
    assert vc._clamped_tempo_ratio(5.03, 5.0) is None                # within tolerance
    assert vc._clamped_tempo_ratio(5.0, 0.0) is None                 # unusable source
    assert vc._clamped_tempo_ratio(0.0, 5.0) is None                 # unusable take


def test_match_duration_atempo_argv(monkeypatch):
    """The stretch pipes through `ffmpeg -af atempo=<clamped ratio>` — a 10 s
    take against a 2 s source clamps at 2.0, never a chained 5× chipmunk."""
    import numpy as np
    import services.ffmpeg_utils as ff

    vc = _vc_mod()
    argv_seen = []

    class _FakeProc:
        returncode = 0

        async def communicate(self, input=None):
            # 240000 samples in / clamped 2.0 → 120000 float32 samples out.
            return np.zeros(120000, dtype=np.float32).tobytes(), b""

    async def _fake_spawn(*args, **kwargs):
        argv_seen.append(list(args))
        return _FakeProc()

    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ff, "spawn_subprocess", _fake_spawn)

    out = asyncio.run(
        vc._match_source_duration(torch.zeros(1, 240000), 24000, 2.0)
    )
    assert out.shape[-1] == 120000

    (argv,) = argv_seen
    af = argv[argv.index("-af") + 1]
    assert af == "atempo=2.000000"  # ONE clamped stage — no chained atempo
    assert argv[0] == "ffmpeg"


def test_match_duration_survives_ffmpeg_failure(monkeypatch):
    """A broken ffmpeg degrades to the unstretched take, never a 500."""
    import services.ffmpeg_utils as ff

    vc = _vc_mod()

    async def _boom(*args, **kwargs):
        raise RuntimeError("no ffmpeg here")

    monkeypatch.setattr(ff, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(ff, "spawn_subprocess", _boom)

    wav = torch.zeros(1, 240000)
    out = asyncio.run(vc._match_source_duration(wav, 24000, 2.0))
    assert out is wav


# ── Registration ────────────────────────────────────────────────────────────


def test_convert_router_is_registered():
    """POST /convert is on the app and pinned in the committed route snapshot
    (tests/test_api_route_inventory.py diffs the live app against it)."""
    from main import app

    routes = {
        (m, r.path)
        for r in app.routes
        if hasattr(r, "methods") and r.methods
        for m in r.methods
    }
    assert ("POST", "/convert") in routes

    snapshot = os.path.join(os.path.dirname(__file__), "fixtures", "api_routes.txt")
    with open(snapshot, encoding="utf-8") as f:
        assert "POST /convert\n" in f.read()


# ── Source language pin ─────────────────────────────────────────────────────


def test_whisper_language_maps_names_and_keeps_auto():
    from api.routers.voice_convert import _whisper_language

    assert _whisper_language("Armenian") == "hy"
    assert _whisper_language("Russian") == "ru"
    assert _whisper_language("Hindi") == "hi"
    assert _whisper_language("Bengali") == "bn"
    assert _whisper_language("Thai") == "th"
    assert _whisper_language("Burmese") == "my"
    assert _whisper_language("Klingon") is None
    assert _whisper_language("Auto") is None
    assert _whisper_language("") is None
    assert _whisper_language(None) is None
    assert _whisper_language("Standard Arabic") == "ar"


def test_convert_picker_offers_exactly_the_pinnable_languages():
    """The Convert picker enables only names the backend pins; anything else
    would silently auto-detect after an explicit choice."""
    import json
    from pathlib import Path
    from api.routers.voice_convert import _whisper_language

    root = Path(__file__).resolve().parents[1]
    picker = json.loads((root / "electron/src/shared/languages.json").read_text("utf-8"))
    pinnable = {name for name in picker if _whisper_language(name)}
    source = (
        root / "electron/src/renderer/src/features/tools/convert-languages.ts"
    ).read_text("utf-8")
    offered = set(re.findall(r"^  '([^']+)',$", source, re.M))
    assert offered == pinnable

    # LanguagePicker also enables any name sharing an ISO code with an offered
    # one; each of those must pin too.
    codes = json.loads((root / "electron/src/shared/language-codes.json").read_text("utf-8"))
    offered_codes = {codes.get(name.lower()) for name in offered} - {None}
    leaked = [n for n in picker if codes.get(n.lower()) in offered_codes and n not in pinnable]
    assert not leaked


def test_convert_pins_explicit_source_language(client, monkeypatch, clone_profile):
    """An explicit ``language`` reaches the ASR so short non-Latin clips aren't
    auto-detected as the wrong language (Armenian read as Russian)."""
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _LangASR({"text": "hello", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0", language="Armenian")
    assert res.status_code == 200, res.text
    assert asr.calls[0]["language"] == "hy"


def test_convert_auto_language_falls_back_to_profile_language(
    client, monkeypatch, clone_profile,
):
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET language=? WHERE id=?", ("Armenian", clone_profile))
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _LangASR({"text": "hello", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0")
    assert res.status_code == 200, res.text
    assert asr.calls[0]["language"] == "hy"


def test_convert_language_is_not_sent_to_asr_without_support(
    client, monkeypatch, clone_profile,
):
    """Backends whose ``transcribe`` has no ``language`` option keep working."""
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _FakeASR({"text": "hello", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0", language="Armenian")
    assert res.status_code == 200, res.text
    assert "language" not in asr.calls[0]


def test_convert_explicit_language_beats_profile_language(client, monkeypatch, clone_profile):
    """Picking Hindi on an English profile pins Hindi, not English."""
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET language=? WHERE id=?", ("English", clone_profile))
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _LangASR({"text": "hello", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0", language="Hindi")
    assert res.status_code == 200, res.text
    assert asr.calls[0]["language"] == "hi"


def test_convert_unsupported_pick_keeps_auto_detect(client, monkeypatch, clone_profile):
    """A language Whisper lacks is not silently swapped for the profile's."""
    from core.db import db_conn

    with db_conn() as conn:
        conn.execute("UPDATE voice_profiles SET language=? WHERE id=?", ("English", clone_profile))
    fake = _make_fake_engine(f"fake-conv-{uuid.uuid4().hex[:6]}")
    asr = _LangASR({"text": "hello", "segments": []})
    _wire_stubs(monkeypatch, engine_cls=fake, asr=asr)

    res = _post_convert(client, clone_profile, match_duration="0", language="Klingon")
    assert res.status_code == 200, res.text
    assert asr.calls[0]["language"] is None


# ── Keep my delivery (#3421): speech-to-speech, no transcript ───────────────
#
# The recording goes to an engine that declares supports_voice_conversion;
# ASR never runs. ffmpeg decode is stubbed.

import numpy as np  # noqa: E402


def _vc_engine(engine_id):
    cls = _make_fake_engine(engine_id)
    cls.supports_voice_conversion = True
    cls.max_vc_source_seconds = 10.0
    cls.vc_calls = []

    def convert_voice(self, source_audio, *, ref_audio, seed=None):
        import soundfile as sf

        audio, sr = sf.read(source_audio, dtype="float32")
        type(self).vc_calls.append((source_audio, ref_audio, len(audio) / sr))
        return torch.full((1, len(audio)), 0.1)

    cls.convert_voice = convert_voice
    return cls


@pytest.fixture()
def profile():
    from core.db import db_conn, init_db

    init_db()
    pid = f"vp-keep-{uuid.uuid4().hex[:8]}"
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, ref_audio_path, ref_text, created_at) "
            "VALUES (?,?,?,?,?,?)",
            (pid, "Keep Target", "clone", "convert-ref.wav", "reference words", 0.0),
        )
    yield pid
    _delete_profile_and_takes(pid)


def _stub_decode(monkeypatch, seconds):
    import services.voice_conversion as vc

    async def decode(path, sr, *, max_s=None):
        n = int((min(seconds, max_s) if max_s else seconds) * sr)
        t = np.arange(n) / sr
        return (0.3 * np.sin(2 * np.pi * 180 * t)).astype(np.float32)

    monkeypatch.setattr(vc, "decode_mono", decode)


def _no_asr(monkeypatch):
    import services.asr_backend as ab

    def boom(**kw):
        raise AssertionError("Keep my delivery must not transcribe")

    monkeypatch.setattr(ab, "load_active_asr_backend", boom)
    monkeypatch.setattr(ab, "asr_model_missing_error", boom)


def _post(client, pid, **extra):
    return client.post(
        "/convert",
        data={"profile_id": pid, "keep_delivery": "1", **extra},
        files={"audio": ("source.wav", b"RIFF-fake-wav-bytes", "audio/wav")},
    )


def test_keep_delivery_converts_the_recording_without_asr(client, monkeypatch, profile):
    vc_cls = _vc_engine(f"vc-{uuid.uuid4().hex[:6]}")
    plain = _make_fake_engine(f"plain-{uuid.uuid4().hex[:6]}")
    monkeypatch.setitem(_tts_mod()._REGISTRY, vc_cls.id, vc_cls)
    monkeypatch.setitem(_tts_mod()._REGISTRY, plain.id, plain)
    # The active engine cannot convert; the installed one that can is used.
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", plain.id)
    _no_asr(monkeypatch)
    _stub_decode(monkeypatch, 25.0)

    r = _post(client, profile)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["engine"] == vc_cls.id and body["text"] == ""
    # 25 s split under the 10 s cap (minus margin): every piece fits, none lost.
    pieces = [c[2] for c in vc_cls.vc_calls]
    assert len(pieces) >= 3 and max(pieces) <= 8.0 + 1e-6
    assert abs(sum(pieces) - 25.0) < 0.01
    assert body["duration_s"] == pytest.approx(25.0, abs=0.05)
    assert plain.calls == []  # never re-spoken

    from core.db import db_conn
    with db_conn() as conn:
        row = conn.execute(
            "SELECT mode FROM generation_history WHERE id=?", (body["id"],)
        ).fetchone()
    assert row["mode"] == "convert"


def test_keep_delivery_prefers_the_active_engine(client, monkeypatch, profile):
    first = _vc_engine(f"vca-{uuid.uuid4().hex[:6]}")
    active = _vc_engine(f"vcb-{uuid.uuid4().hex[:6]}")
    monkeypatch.setitem(_tts_mod()._REGISTRY, first.id, first)
    monkeypatch.setitem(_tts_mod()._REGISTRY, active.id, active)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", active.id)
    _no_asr(monkeypatch)
    _stub_decode(monkeypatch, 2.0)

    r = _post(client, profile)
    assert r.status_code == 200, r.text
    assert r.json()["engine"] == active.id
    assert first.vc_calls == []


def test_keep_delivery_without_a_conversion_engine_says_what_to_install(client, monkeypatch, profile):
    monkeypatch.setattr(_tts_mod(), "voice_conversion_engine_ids", lambda: [])
    _no_asr(monkeypatch)
    r = _post(client, profile)
    assert r.status_code == 409
    detail = r.json()["detail"]
    assert detail["error"] == "voice_conversion_engine_missing"
    assert "CosyVoice 3" in detail["message"]


def test_split_lands_in_the_pause_and_respects_the_cap():
    from services.voice_conversion import split_audio

    sr = 16000
    speech = lambda s: (0.3 * np.sin(2 * np.pi * 200 * np.arange(int(s * sr)) / sr)).astype(np.float32)
    # 7 s speech, 0.5 s pause, 7 s speech: a 10 s cap must cut inside the pause.
    audio = np.concatenate([speech(7.0), np.zeros(int(0.5 * sr), np.float32), speech(7.0)])
    bounds = split_audio(audio, sr, 10.0)
    assert bounds[0][0] == 0 and bounds[-1][1] == len(audio)
    assert all(b - a <= 10 * sr for a, b in bounds)
    assert all(bounds[i][1] == bounds[i + 1][0] for i in range(len(bounds) - 1))
    assert 7.0 * sr <= bounds[0][1] <= 7.5 * sr
    assert split_audio(audio[: 3 * sr], sr, 10.0) == [(0, 3 * sr)]
    assert split_audio(np.zeros(0, np.float32), sr, 10.0) == []


def test_keep_delivery_refuses_an_overlong_source(client, monkeypatch, profile):
    import services.voice_conversion as vc

    vc_cls = _vc_engine(f"vcl-{uuid.uuid4().hex[:6]}")
    monkeypatch.setitem(_tts_mod()._REGISTRY, vc_cls.id, vc_cls)
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", vc_cls.id)
    monkeypatch.setattr(vc, "MAX_SOURCE_S", 5.0)
    _no_asr(monkeypatch)
    _stub_decode(monkeypatch, 30.0)
    r = _post(client, profile)
    assert r.status_code == 422
    assert "longer than" in r.json()["detail"]
    assert vc_cls.vc_calls == []
