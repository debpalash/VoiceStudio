"""#2281: a reference longer than OmniVoice's transcript limit must still clone.

OmniVoice aligns a transcript against the whole clip and rejects the pair above
``CLONE_REF_TEXT_MAX_SECONDS`` (20 s). Every automatic path used to produce
exactly that pair: the backend transcribed the whole clip whenever no
transcript was stored (``_get_clone_prompt``, ``/generate``), and saved
profiles kept the whole-clip transcript made at save time. A 25 s saved voice
was therefore permanently unusable on the default engine, although the model's
own transcript-free path picks the best 15 s passage of clips up to 75 s.

Rules pinned here:
  * an automatic or stored transcript on an over-long clip is dropped at the
    engine boundary (in-process prompt cache, inline fallback, sidecar), so the
    model's best-passage selection runs;
  * no whole-clip ASR is spent on such a clip;
  * a transcript typed on the request still gets the actionable error;
  * engines advertise how much of a reference they use (``list_backends``).
"""
import importlib
import os
from types import SimpleNamespace

import pytest
import soundfile as sf
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SR = 24_000


def _tts():
    return importlib.import_module("services.tts_backend")


def _wav(path, seconds, value=0.1):
    sf.write(path, torch.full((int(seconds * SR),), value).numpy(), SR)
    return str(path)


class _Tokenizer:
    config = SimpleNamespace(hop_length=320)
    device = "cpu"
    seen_samples = None

    def encode(self, audio):
        self.seen_samples = audio.shape[-1]
        return SimpleNamespace(audio_codes=torch.zeros((1, 1, 1), dtype=torch.long))


def _omnivoice_stub():
    from omnivoice.models.omnivoice import OmniVoice

    model = OmniVoice.__new__(OmniVoice)
    model.sampling_rate = SR
    model.audio_tokenizer = _Tokenizer()
    model._asr_pipe = object()
    model.transcribe = lambda _audio: "Selected passage words."
    return model


@pytest.fixture()
def no_prompt_disk_cache(monkeypatch):
    tts = _tts()
    monkeypatch.setattr(tts, "_prompt_disk_dir", lambda: None)
    tts.clear_clone_prompt_cache()
    yield
    tts.clear_clone_prompt_cache()


class _CountingTranscribe:
    def __init__(self, result="whole clip transcript"):
        self.calls = 0
        self.result = result

    def __call__(self, _path):
        self.calls += 1
        return self.result


def test_auto_transcribed_long_reference_uses_best_passage(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """Before the fix: whole-clip ASR → [clone_ref_too_long] → prompt None, and
    the inline fallback raised the same error. Now no ASR and a 15 s passage."""
    import services.asr_backend as ab

    counting = _CountingTranscribe()
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    model = _omnivoice_stub()

    prompt = _tts()._get_clone_prompt(model, _wav(tmp_path / "long.wav", 25), None)

    assert prompt is not None
    assert prompt.ref_text.startswith("Selected passage words")
    assert model.audio_tokenizer.seen_samples <= 15 * SR
    assert counting.calls == 0


def test_stored_whole_clip_transcript_on_long_reference_still_clones(
    tmp_path, no_prompt_disk_cache
):
    """Existing saved profiles carry the save-time whole-clip transcript."""
    model = _omnivoice_stub()

    prompt = _tts()._get_clone_prompt(
        model, _wav(tmp_path / "saved.wav", 25), "stored whole clip transcript"
    )

    assert prompt is not None
    assert model.audio_tokenizer.seen_samples <= 15 * SR


def test_short_reference_keeps_its_transcript(tmp_path, monkeypatch, no_prompt_disk_cache):
    import services.asr_backend as ab

    counting = _CountingTranscribe("short clip transcript")
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    model = _omnivoice_stub()

    prompt = _tts()._get_clone_prompt(model, _wav(tmp_path / "short.wav", 8), None)

    assert prompt is not None
    assert prompt.ref_text.startswith("short clip transcript")
    assert counting.calls == 1


def test_inline_fallback_drops_whole_clip_transcript(tmp_path, monkeypatch):
    seen = {}

    class _Model:
        def generate(self, **kw):
            seen.update(kw)
            return [torch.zeros(1, 10)]

    monkeypatch.setattr(_tts(), "_get_clone_prompt", lambda *a, **k: None)
    path = _wav(tmp_path / "long.wav", 25)

    _tts().generate_with_cached_ref(_Model(), ref_audio=path, ref_text="whole clip", text="hi")

    assert seen["ref_audio"] == path
    assert seen["ref_text"] is None


def test_sidecar_request_drops_whole_clip_transcript(tmp_path, monkeypatch):
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend

    seen = {}
    # The class's own base, not a fresh import: other suites purge
    # sys.modules["services"], leaving a second SubprocessBackend object.
    base = OmniVoiceSubprocessBackend.__mro__[1]
    monkeypatch.setattr(base, "generate", lambda self, text, **kw: seen.update(kw))
    backend = OmniVoiceSubprocessBackend.__new__(OmniVoiceSubprocessBackend)
    long_path = _wav(tmp_path / "long.wav", 25)
    short_path = _wav(tmp_path / "short.wav", 8)

    backend.generate("hi", ref_audio=long_path, ref_text="whole clip")
    assert seen["ref_text"] is None
    backend.generate("hi", ref_audio=short_path, ref_text="short clip")
    assert seen["ref_text"] == "short clip"


def test_model_limit_matches_advertised_engine_limit():
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend
    from omnivoice.utils.audio import CLONE_REF_TEXT_MAX_SECONDS

    tts = _tts()
    for cls in (tts.OmniVoiceBackend, OmniVoiceSubprocessBackend):
        assert cls.max_ref_seconds == CLONE_REF_TEXT_MAX_SECONDS
        assert cls.ref_strategy == "best_window"
    assert tts.VoxCPM2Backend.max_ref_seconds == tts._VOXCPM_REF_MAX_S
    assert tts.VoxCPM2Backend.ref_strategy == "head"
    assert tts.TTSBackend.max_ref_seconds is None
    assert tts.TTSBackend.ref_strategy is None


def test_list_backends_exposes_reference_limits():
    by_id = {entry["id"]: entry for entry in _tts().list_backends()}
    assert by_id["omnivoice"]["max_ref_seconds"] == 20.0
    assert by_id["omnivoice"]["ref_strategy"] == "best_window"
    assert by_id["voxcpm2"]["max_ref_seconds"] == 30.0
    assert by_id["voxcpm2"]["ref_strategy"] == "head"
    for entry in by_id.values():
        assert entry["ref_strategy"] in {None, "best_window", "head", "full"}


# ── /generate route ──────────────────────────────────────────────────────────


def _fake_best_window_engine():
    class _FakeEngine(_tts().TTSBackend):
        id = "fake-best-window-engine"
        display_name = "Fake best-window engine (test)"
        applies_own_mastering = False
        gpu_compat = ("cpu",)
        max_ref_seconds = 20.0
        ref_strategy = "best_window"
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return SR

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append(kw)
            return torch.zeros(1, SR)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def fake_engine(monkeypatch):
    import services.asr_backend as ab

    fake = _fake_best_window_engine()
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)
    fake.calls.clear()
    counting = _CountingTranscribe()
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    return fake, counting


def _post(client, engine, path, **extra):
    with open(path, "rb") as fh:
        return client.post(
            "/generate",
            data={"text": "Hello world", "engine": engine.id, **extra},
            files={"ref_audio": ("ref.wav", fh, "audio/wav")},
        )


def test_generate_long_upload_skips_whole_clip_asr(client, fake_engine, tmp_path):
    fake, counting = fake_engine

    res = _post(client, fake, _wav(tmp_path / "long.wav", 25))

    assert res.status_code == 200, res.text
    assert counting.calls == 0
    assert not fake.calls[0].get("ref_text")


def test_generate_long_upload_with_typed_transcript_is_actionable(
    client, fake_engine, tmp_path
):
    fake, counting = fake_engine

    res = _post(client, fake, _wav(tmp_path / "long.wav", 25), ref_text="typed words")

    assert res.status_code == 400
    detail = res.json()["detail"]
    assert "[clone_ref_too_long]" in detail and "at most 20 seconds" in detail
    assert fake.calls == [] and counting.calls == 0


def test_generate_probes_reference_length_off_the_event_loop(
    client, fake_engine, tmp_path, monkeypatch
):
    """Non-WAV clips decode through ffmpeg to measure length; doing that on the
    request loop would stall every other request."""
    import asyncio

    tts = _tts()
    real = tts.reference_duration_s
    on_loop: list[bool] = []

    def probe(path):
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return real(path)

    monkeypatch.setattr(tts, "reference_duration_s", probe)
    fake, _counting = fake_engine

    res = _post(client, fake, _wav(tmp_path / "long.wav", 25))

    assert res.status_code == 200, res.text
    assert on_loop == [False]


def test_generate_short_upload_is_still_transcribed(client, fake_engine, tmp_path):
    fake, counting = fake_engine

    res = _post(client, fake, _wav(tmp_path / "short.wav", 8))

    assert res.status_code == 200, res.text
    assert counting.calls == 1
    assert fake.calls[0]["ref_text"] == "whole clip transcript"


# ── VoxCPM2: head strategy ───────────────────────────────────────────────────


def test_voxcpm_capped_reference_drops_whole_clip_transcript(tmp_path):
    """A clip cut to its first 30 s no longer matches a whole-clip transcript;
    continuing from it would speak text the prompt audio does not contain."""
    kw = {"ref_audio": _wav(tmp_path / "long.wav", 40), "ref_text": "whole clip"}

    _tts().prepare_voxcpm_reference(kw)

    assert kw["ref_audio"] != str(tmp_path / "long.wav")
    assert kw["ref_text"] is None


def test_voxcpm_uncapped_reference_keeps_transcript(tmp_path):
    kw = {"ref_audio": _wav(tmp_path / "short.wav", 12), "ref_text": "short clip"}

    _tts().prepare_voxcpm_reference(kw)

    assert kw["ref_text"] == "short clip"


# ── Review follow-ups ────────────────────────────────────────────────────────


@pytest.mark.parametrize("blank", ["", "   "])
def test_blank_transcript_is_no_transcript(tmp_path, blank):
    """The model checks ``ref_text is not None``; "" must not reach it."""
    tts = _tts()
    assert tts.omnivoice_ref_text(_wav(tmp_path / "long.wav", 25), blank) is None
    assert tts.omnivoice_ref_text(_wav(tmp_path / "short.wav", 8), blank) is None


def test_inline_fallback_drops_blank_transcript_on_long_reference(tmp_path, monkeypatch):
    seen = {}

    class _Model:
        def generate(self, **kw):
            seen.update(kw)
            return [torch.zeros(1, 10)]

    monkeypatch.setattr(_tts(), "_get_clone_prompt", lambda *a, **k: None)
    path = _wav(tmp_path / "long.wav", 25)
    _tts().generate_with_cached_ref(_Model(), ref_audio=path, ref_text="", text="hi")
    assert seen["ref_text"] is None


def test_every_sidecar_advertises_its_in_process_reference_limits():
    """An own-venv install resolves to the sidecar class, so /engines reports
    the sidecar's metadata; it must match the in-process engine's."""
    tts = _tts()
    for engine_id, (module_name, class_name) in tts._OWN_VENV_SIDECARS.items():
        sidecar = getattr(importlib.import_module(module_name), class_name)
        in_process = tts._REGISTRY[engine_id]
        assert (sidecar.max_ref_seconds, sidecar.ref_strategy) == (
            in_process.max_ref_seconds, in_process.ref_strategy,
        ), engine_id
    from engines.omnivoice_subprocess import OmniVoiceMPSSubprocessBackend

    assert OmniVoiceMPSSubprocessBackend.max_ref_seconds == tts.OmniVoiceBackend.max_ref_seconds
    assert OmniVoiceMPSSubprocessBackend.ref_strategy == tts.OmniVoiceBackend.ref_strategy


@pytest.fixture()
def long_profile():
    import uuid

    from api.routers import generation
    from core.db import db_conn, init_db

    init_db()
    pid = f"vp-long-{uuid.uuid4().hex[:8]}"
    os.makedirs(generation.VOICES_DIR, exist_ok=True)
    clip = os.path.join(generation.VOICES_DIR, f"{pid}.wav")
    _wav(clip, 25)
    with db_conn() as conn:
        conn.execute(
            "INSERT INTO voice_profiles (id, name, kind, created_at, ref_text, ref_audio_path) "
            "VALUES (?, 'Long', 'clone', 0.0, 'stored whole clip words', ?)",
            (pid, f"{pid}.wav"),
        )
    yield pid
    with db_conn() as conn:
        conn.execute("DELETE FROM generation_history WHERE profile_id=?", (pid,))
        conn.execute("DELETE FROM voice_profiles WHERE id=?", (pid,))
    os.remove(clip)


def test_generate_profile_with_typed_transcript_is_actionable(client, fake_engine, long_profile):
    fake, _counting = fake_engine
    res = client.post(
        "/generate",
        data={"text": "Hello world", "engine": fake.id, "profile_id": long_profile,
              "ref_text": "typed override"},
    )
    assert res.status_code == 400
    assert "[clone_ref_too_long]" in res.json()["detail"]
    assert fake.calls == []


def test_generate_profile_stored_transcript_still_clones(client, fake_engine, long_profile):
    fake, counting = fake_engine
    res = client.post(
        "/generate",
        data={"text": "Hello world", "engine": fake.id, "profile_id": long_profile},
    )
    assert res.status_code == 200, res.text
    assert counting.calls == 0
