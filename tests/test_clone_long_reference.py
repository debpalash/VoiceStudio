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
    engine boundary (in-process prompt cache, inline fallback, sidecar);
  * the installed catalogue recognizer transcribes each 15 s window and the
    window with the most speech is what gets encoded — the model's Whisper
    snapshot is not required for that;
  * no whole-clip ASR is spent on such a clip; if the catalogue recognizer
    returns nothing, the model's own passage selection still runs;
  * a transcript typed on the request still gets the actionable error;
  * engines advertise how much of a reference they use (``list_backends``).
"""
import importlib
import logging
import os
from collections import OrderedDict
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
        self.paths = []
        self.result = result

    def __call__(self, path):
        self.calls += 1
        self.paths.append(path)
        return self.result


def test_long_reference_uses_installed_asr_windows(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """A 25 s clip is ranked by the catalogue recognizer, not model Whisper."""
    import services.asr_backend as ab
    from omnivoice.models.omnivoice import OmniVoice

    class _Windows:
        def __init__(self):
            self.paths = []

        def __call__(self, path):
            self.paths.append(path)
            if len(self.paths) == 1:
                return "hi"
            return "this window has many spoken words"

    windows = _Windows()
    monkeypatch.setattr(ab, "transcribe_reference", windows)
    monkeypatch.setattr(_tts(), "_reference_asr_identity", lambda: "fixed-recognizer")
    monkeypatch.setattr(
        OmniVoice,
        "_load_cached_reference_asr",
        lambda self: (_ for _ in ()).throw(AssertionError("model whisper")),
    )
    model = _omnivoice_stub()
    model._asr_pipe = None
    original = _wav(tmp_path / "long.wav", 25)

    prompt = _tts()._get_clone_prompt(model, original, None)

    assert prompt is not None
    assert prompt.ref_text.startswith("this window has many spoken words")
    assert model.audio_tokenizer.seen_samples <= 15 * SR
    assert original not in windows.paths
    assert len(windows.paths) == 2
    monkeypatch.setattr(
        _tts(), "_materialize_window",
        lambda *_: (_ for _ in ()).throw(AssertionError("cache hit decoded reference")),
    )

    again = _tts()._get_clone_prompt(model, original, "stored whole clip transcript")
    assert again is prompt
    assert len(windows.paths) == 2


def test_long_reference_over_75s_is_not_windowed(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """Past the engine's hard cap, no catalogue window is invented."""
    import services.asr_backend as ab

    counting = _CountingTranscribe(result="should not run")
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    model = _omnivoice_stub()

    prompt = _tts()._get_clone_prompt(model, _wav(tmp_path / "too-long.wav", 80), None)

    assert prompt is None
    assert counting.calls == 0


def test_changed_recognizer_does_not_reuse_the_cached_passage(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """The prompt cache is keyed by the recognizer that picked the window."""
    import services.asr_backend as ab

    class _Windows:
        def __init__(self):
            self.calls = 0

        def __call__(self, _path):
            self.calls += 1
            if self.calls <= 2:
                return "hi" if self.calls == 1 else "this window has many spoken words"
            return "a completely different spoken passage"

    windows = _Windows()
    monkeypatch.setattr(ab, "transcribe_reference", windows)
    identity = {"value": "recognizer-a"}
    monkeypatch.setattr(_tts(), "_reference_asr_identity", lambda: identity["value"])
    model = _omnivoice_stub()
    original = _wav(tmp_path / "long.wav", 25)

    first = _tts()._get_clone_prompt(model, original, None)
    identity["value"] = "recognizer-b"
    second = _tts()._get_clone_prompt(model, original, None)

    assert first.ref_text.startswith("this window has many spoken words")
    assert second.ref_text.startswith("a completely different spoken passage")
    assert windows.calls == 4


def test_equal_transcripts_from_different_windows_do_not_share_prompt(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """Conditioning must follow the window, not just the recognized words."""
    import services.asr_backend as ab

    calls = {"value": 0}
    monkeypatch.setattr(_tts(), "_reference_asr_identity", lambda: "same-recognizer")

    def transcribe(_path):
        calls["value"] += 1
        return "same words" if calls["value"] in (1, 4) else ""

    monkeypatch.setattr(ab, "transcribe_reference", transcribe)
    original = _wav(tmp_path / "long.wav", 25)
    model = _omnivoice_stub()
    first = _tts()._get_clone_prompt(model, original, None)
    _tts()._passage_choices.clear()
    second = _tts()._get_clone_prompt(model, original, None)

    assert first is not None and second is not None
    assert first is not second
    assert first.ref_text == second.ref_text
    assert calls["value"] == 4


def test_long_reference_without_installed_asr_uses_model_passage(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """No catalogue transcript: the model's own best-passage path still runs."""
    import services.asr_backend as ab

    counting = _CountingTranscribe(result=None)
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    model = _omnivoice_stub()
    original = _wav(tmp_path / "long.wav", 25)

    prompt = _tts()._get_clone_prompt(model, original, None)

    assert prompt is not None
    assert prompt.ref_text.startswith("Selected passage words")
    assert model.audio_tokenizer.seen_samples <= 15 * SR
    assert original not in counting.paths
    assert counting.calls == 2


def test_stored_whole_clip_transcript_on_long_reference_still_clones(
    tmp_path, monkeypatch, no_prompt_disk_cache
):
    """Existing saved profiles carry the save-time whole-clip transcript."""
    import services.asr_backend as ab

    monkeypatch.setattr(ab, "transcribe_reference", lambda _path: None)
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

    monkeypatch.setattr(_tts(), "_omnivoice_installed_passage", lambda _path: None)
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


def test_sidecar_forwards_installed_passage(tmp_path, monkeypatch):
    import shutil

    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend

    window = _wav(tmp_path / "window.wav", 10)

    def _selected(_path):
        owned = tmp_path / "owned.wav"
        shutil.copy(window, owned)
        return str(owned), "best passage words"

    monkeypatch.setattr(_tts(), "_omnivoice_installed_passage", _selected)
    seen = {}
    base = OmniVoiceSubprocessBackend.__mro__[1]
    monkeypatch.setattr(base, "generate", lambda self, text, **kw: seen.update(kw))
    backend = OmniVoiceSubprocessBackend.__new__(OmniVoiceSubprocessBackend)
    long_path = _wav(tmp_path / "long.wav", 25)

    backend.generate("hi", ref_audio=long_path, ref_text="whole clip")

    assert seen["ref_text"] == "best passage words"
    assert seen["ref_audio"] != long_path
    assert not os.path.exists(seen["ref_audio"])


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
    filename = extra.pop("filename", "ref.wav")
    mime = extra.pop("mime", "audio/wav")
    with open(path, "rb") as fh:
        return client.post(
            "/generate",
            data={"text": "Hello world", "engine": engine.id, **extra},
            files={"ref_audio": (filename, fh, mime)},
        )


def _flac(path, seconds, value=0.1):
    sf.write(path, torch.full((int(seconds * SR),), value).numpy(), SR, format="FLAC")
    return str(path)


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


def test_passage_choices_share_the_prompt_cache_lock(
    tmp_path, monkeypatch, no_prompt_disk_cache,
):
    """Two GPU workers can rank long references at once; the window LRU must
    take the same lock as the prompt cache."""
    tts = _tts()
    monkeypatch.setattr(tts, "_reference_asr_identity", lambda: "lock-test")
    held = []

    class _Guarded(OrderedDict):
        def _check(self):
            held.append(tts._prompt_cache_lock.locked())

        def get(self, key, default=None):
            self._check()
            return super().get(key, default)

        def __setitem__(self, key, value):
            self._check()
            super().__setitem__(key, value)

        def move_to_end(self, key, last=True):
            self._check()
            return super().move_to_end(key, last)

        def popitem(self, last=True):
            self._check()
            return super().popitem(last)

        def clear(self):
            self._check()
            super().clear()

    original = tts._passage_choices
    guarded = _Guarded()
    tts._passage_choices = guarded
    try:
        first = _wav(tmp_path / "first.wav", 1)
        tts._remember_passage(first, 1, "first window")
        assert tts._recall_passage(first) == (1, "first window")
        for index in range(tts._PASSAGE_CHOICE_MAX):
            tts._remember_passage(_wav(tmp_path / f"w{index}.wav", 1), 0, "x")
        tts.clear_clone_prompt_cache()
    finally:
        tts._passage_choices = original
    assert held and all(held)


def test_unnamed_recognizer_is_not_cached(tmp_path, monkeypatch, no_prompt_disk_cache):
    """An identity we cannot name must not become a shared cache key."""
    tts = _tts()
    monkeypatch.setattr(tts, "_reference_asr_identity", lambda: "")

    tts._remember_passage(_wav(tmp_path / "clip.wav", 1), 1, "words")

    assert list(tts._passage_choices) == []


def test_passage_identity_tracks_each_selected_model(monkeypatch):
    """WhisperX, and every other backend, changes the key when its model changes."""
    import services.asr_backend as ab

    tts = _tts()
    monkeypatch.setattr(ab, "active_backend_id", lambda: "whisperx")
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **_kwargs: None)
    monkeypatch.setattr(tts, "_capture_recognizer_label", lambda _ab: "faster-whisper:fixed")
    monkeypatch.setattr(tts, "_fallback_recognizer_labels", lambda _ab, _parts: [])
    monkeypatch.setenv("ASR_MODEL_WHISPERX", "small")

    small = tts._reference_asr_identity()
    monkeypatch.setenv("ASR_MODEL_WHISPERX", "large-v3")
    large = tts._reference_asr_identity()

    assert small == "whisperx:small|faster-whisper:fixed"
    assert large == "whisperx:large-v3|faster-whisper:fixed"


def test_silent_long_reference_is_not_ranked_again(
    tmp_path, monkeypatch, no_prompt_disk_cache,
):
    """No spoken words is a stable result: the next chunk must not re-run ASR."""
    import services.asr_backend as ab

    counting = _CountingTranscribe(result=None)
    monkeypatch.setattr(ab, "transcribe_reference", counting)
    monkeypatch.setattr(_tts(), "_reference_asr_identity", lambda: "fixed-recognizer")
    model = _omnivoice_stub()
    original = _wav(tmp_path / "silent.wav", 25)

    _tts()._get_clone_prompt(model, original, None)
    _tts()._get_clone_prompt(model, original, None)

    assert counting.calls == 2


def test_long_reference_decodes_when_soundfile_cannot(
    tmp_path, monkeypatch, no_prompt_disk_cache,
):
    """AAC/M4A fall through libsndfile to ffmpeg, then still rank 15 s windows."""
    import soundfile as sf
    import services.asr_backend as ab
    from pydub import AudioSegment

    class _Segment:
        frame_rate = SR
        channels = 1
        sample_width = 2

        def get_array_of_samples(self):
            return [1000] * (25 * SR)

    real_read = sf.read

    def _read(path, *args, **kwargs):
        if os.path.basename(str(path)) == "long.wav":
            raise RuntimeError("unsupported")
        return real_read(path, *args, **kwargs)

    monkeypatch.setattr(sf, "read", _read)
    monkeypatch.setattr(AudioSegment, "from_file", lambda _path: _Segment())

    class _Windows:
        def __init__(self):
            self.calls = 0

        def __call__(self, _path):
            self.calls += 1
            if self.calls == 1:
                return "hi"
            return "this window has many spoken words"

    windows = _Windows()
    monkeypatch.setattr(ab, "transcribe_reference", windows)
    model = _omnivoice_stub()

    prompt = _tts()._get_clone_prompt(model, _wav(tmp_path / "long.wav", 25), None)

    assert prompt is not None
    assert prompt.ref_text.startswith("this window has many spoken words")
    assert model.audio_tokenizer.seen_samples <= 15 * SR
    assert windows.calls == 2


def test_window_cleanup_log_omits_the_absolute_path(
    tmp_path, monkeypatch, no_prompt_disk_cache,
):
    import services.asr_backend as ab

    tts = _tts()
    monkeypatch.setattr(ab, "transcribe_reference", lambda _path: "spoken words here")
    real_remove = os.remove

    def _remove(path):
        if not str(path).startswith(str(tmp_path)):
            raise OSError("busy")
        real_remove(path)

    monkeypatch.setattr(os, "remove", _remove)
    logged = []

    class _Capture(logging.Handler):
        def emit(self, record):
            logged.append(record)

    handler = _Capture()
    tts.logger.addHandler(handler)
    previous = tts.logger.level
    tts.logger.setLevel(logging.DEBUG)
    try:
        tts._get_clone_prompt(_omnivoice_stub(), _wav(tmp_path / "long.wav", 25), None)
    finally:
        tts.logger.setLevel(previous)
        tts.logger.removeHandler(handler)

    names = [
        rec.args[0] for rec in logged
        if rec.getMessage().startswith("failed to remove reference window")
    ]
    assert names
    assert all(os.sep not in name and name == os.path.basename(name) for name in names)


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


def test_generate_keeps_uploaded_flac_extension(client, fake_engine, tmp_path):
    """A one-shot clone upload must keep its container, not be rewritten as .wav.

    /profiles already stores the original extension. /generate wrote every
    upload with suffix=.wav, so an MP3/M4A/WebM recording failed to decode
    (pydub passes -f wav to ffmpeg). Saved voices were fine; Use once was not.
    """
    fake, counting = fake_engine
    path = _flac(tmp_path / "voice.flac", 25)

    res = _post(
        client, fake, path, filename="voice.flac", mime="audio/flac",
    )

    assert res.status_code == 200, res.text
    saved = fake.calls[0]["ref_audio"]
    assert saved.endswith(".flac"), saved
    assert counting.calls == 0


def test_generate_keeps_webm_recording_extension(client, fake_engine, tmp_path):
    """MediaRecorder WebM is the fallback when /clean-audio is missing."""
    fake, _counting = fake_engine
    path = _wav(tmp_path / "recording.wav", 8)

    res = _post(
        client, fake, path, filename="recording.webm", mime="audio/webm",
    )

    assert res.status_code == 200, res.text
    assert fake.calls[0]["ref_audio"].endswith(".webm")


def test_generate_unknown_reference_extension_stays_wav(client, fake_engine, tmp_path):
    """A crafted filename must not choose the on-disk suffix."""
    fake, _counting = fake_engine
    path = _wav(tmp_path / "voice.wav", 8)

    res = _post(
        client, fake, path, filename="voice.exe", mime="application/octet-stream",
    )

    assert res.status_code == 200, res.text
    assert fake.calls[0]["ref_audio"].endswith(".wav")


@pytest.mark.parametrize(
    "filename, suffix",
    [
        ("voice.flac", ".flac"),
        ("take.MP3", ".mp3"),
        ("clip.m4a", ".m4a"),
        ("recording.webm", ".webm"),
        ("note.opus", ".opus"),
        ("note.oga", ".oga"),
        ("clip.aac", ".aac"),
        ("clip.ogg", ".ogg"),
        ("voice.exe", ".wav"),
        ("voice", ".wav"),
        (None, ".wav"),
        (r"C:\Users\a\clip.mp3", ".mp3"),
    ],
)
def test_ref_upload_suffix_allowlist(filename, suffix):
    from api.routers.generation import _ref_upload_suffix

    assert _ref_upload_suffix(filename) == suffix
