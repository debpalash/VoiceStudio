"""Transcript-free cloning must never implicitly download a second ASR (#2116)."""
import sys
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
import torch


def _model():
    from omnivoice.models.omnivoice import OmniVoice
    model = OmniVoice.__new__(OmniVoice)
    model.sampling_rate = 24_000
    model._asr_pipe = None
    model.audio_tokenizer = SimpleNamespace(
        config=SimpleNamespace(hop_length=320), device="cpu",
        encode=lambda _: SimpleNamespace(audio_codes=torch.zeros((1, 1, 1))),
    )
    model.transcribe = lambda _: "Words from the reference."
    return model


@pytest.mark.parametrize("seconds", [1, 21])
def test_missing_implicit_asr_never_calls_network_capable_loader(monkeypatch, seconds):
    from huggingface_hub.errors import LocalEntryNotFoundError
    model = _model()
    loader = Mock(side_effect=AssertionError("implicit network-capable ASR load"))
    model.load_asr_model = loader
    lookup = Mock(side_effect=LocalEntryNotFoundError("not cached"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", lookup)
    # Over 20 s a transcript is refused, so the message names the length limit.
    expected = "reference transcript" if seconds <= 20 else "20 seconds"
    with pytest.raises(ValueError, match=expected):
        model.create_voice_clone_prompt(
            (torch.full((1, seconds * 24_000), 0.1), 24_000),
            preprocess_prompt=False,
        )
    loader.assert_not_called()
    # The default checkpoint is asked for first; the other reusable Whisper
    # checkpoints are tried before the model concludes nothing is installed.
    assert lookup.call_args_list[0].args == ("openai/whisper-large-v3-turbo",)
    assert all(call.kwargs["local_files_only"] is True for call in lookup.call_args_list)


@pytest.mark.parametrize("seconds", [1, 21])
def test_implicit_asr_loads_only_the_resolved_local_snapshot(monkeypatch, tmp_path, seconds):
    model = _model()
    snapshot = tmp_path / "cached-whisper"
    snapshot.mkdir()
    for name in ("config.json", "preprocessor_config.json", "tokenizer.json", "model.safetensors"):
        (snapshot / name).write_text("{}")
    lookup = Mock(return_value=str(snapshot))
    monkeypatch.setattr("huggingface_hub.snapshot_download", lookup)

    def load(*, model_name):
        assert model_name == str(snapshot)
        model._asr_pipe = object()

    model.load_asr_model = Mock(side_effect=load)
    prompt = model.create_voice_clone_prompt(
        (torch.full((1, seconds * 24_000), 0.1), 24_000),
        preprocess_prompt=False,
    )
    assert prompt.ref_text == "Words from the reference."
    model.load_asr_model.assert_called_once_with(model_name=str(snapshot))
    lookup.assert_called_once_with("openai/whisper-large-v3-turbo", local_files_only=True)


def test_supplied_transcript_does_not_look_for_asr(monkeypatch):
    model = _model()
    lookup = Mock(side_effect=AssertionError("ASR lookup not needed"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", lookup)
    prompt = model.create_voice_clone_prompt(
        (torch.full((1, 24_000), 0.1), 24_000),
        ref_text="Supplied words.", preprocess_prompt=False,
    )
    assert prompt.ref_text == "Supplied words."
    lookup.assert_not_called()


@pytest.mark.parametrize("ref_text", [None, "", "   "])
def test_sidecar_reuses_installed_asr_for_short_reference(monkeypatch, tmp_path, ref_text):
    """Sidecar callers must reuse catalogue ASR just like in-process cloning."""
    import soundfile as sf
    from huggingface_hub.errors import LocalEntryNotFoundError
    from engines.omnivoice_subprocess import main as sidecar
    from services import asr_backend

    reference = tmp_path / "reference.wav"
    sf.write(reference, torch.full((24_000,), 0.1).numpy(), 24_000)
    model = _model()
    lookup = Mock(side_effect=LocalEntryNotFoundError("model-specific Whisper not installed"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", lookup)
    transcribe = Mock(return_value="Installed recognizer words.")
    monkeypatch.setattr(asr_backend, "transcribe_reference", transcribe)

    prompts = []
    def synthesize(**kwargs):
        prompts.append(model.create_voice_clone_prompt(
            kwargs["ref_audio"], ref_text=kwargs.get("ref_text"), preprocess_prompt=False,
        ))
        return [torch.zeros(1, 16)]

    monkeypatch.setattr(sidecar, "_load_model", lambda _: SimpleNamespace(generate=synthesize, sampling_rate=24_000))
    monkeypatch.setattr(sidecar, "_send", lambda *_: None)
    sidecar._handle_synthesize({"text": "New words.", "ref_audio": str(reference), "ref_text": ref_text}, None)
    assert prompts[0].ref_text == "Installed recognizer words."
    transcribe.assert_called_once_with(str(reference), release_after=True)
    lookup.assert_not_called()


@pytest.mark.parametrize("supplied", [True, False])
@pytest.mark.parametrize("fails", [True, False])
def test_sidecar_preserves_supplied_text_and_local_fallback(monkeypatch, supplied, fails, caplog):
    from engines.omnivoice_subprocess import main as sidecar
    from services import asr_backend, tts_backend

    monkeypatch.setattr(tts_backend, "reference_duration_s", lambda _: 1.0)
    transcribe = Mock(side_effect=RuntimeError("private-reference.wav")) if fails else Mock(return_value=None)
    monkeypatch.setattr(asr_backend, "transcribe_reference", transcribe)
    generate = Mock(return_value=[torch.zeros(1, 16)])
    monkeypatch.setattr(sidecar, "_load_model", lambda _: SimpleNamespace(generate=generate, sampling_rate=24_000))
    monkeypatch.setattr(sidecar, "_send", lambda *_: None)
    words = "Verified words." if supplied else None
    sidecar._handle_synthesize({"text": "New words.", "ref_audio": "ref.wav", "ref_text": words}, None)
    result = generate.call_args.kwargs
    assert result["ref_text"] == words
    assert result["ref_audio"] == "ref.wav"
    if supplied:
        transcribe.assert_not_called()
    else:
        transcribe.assert_called_once_with("ref.wav", release_after=True)
    assert "private-reference.wav" not in caplog.text


def test_reference_candidate_failure_does_not_log_audio_paths(caplog):
    from services.asr_backend import _transcribe_reference_candidates
    backend = SimpleNamespace(
        id="test-recognizer",
        transcribe=Mock(side_effect=OSError("private-reference.wav")),
    )
    assert _transcribe_reference_candidates([backend], "private-reference.wav") == ""
    assert "test-recognizer" in caplog.text
    assert "private-reference.wav" not in caplog.text


@pytest.mark.parametrize("installed", [False, True])
def test_pytorch_reference_defers_pipeline_loading(monkeypatch, installed):
    from services import asr_backend as ab
    from api.routers.setup import models
    monkeypatch.setattr(ab, "active_backend_id", lambda: "pytorch-whisper")
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "_capture_whisper_repo", lambda: "openai/whisper-large-v3-turbo")
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab, "_repo_installed", lambda *args, **kw: installed)
    monkeypatch.setattr(ab, "get_capture_asr_backend", lambda: ab.PyTorchWhisperBackend())
    monkeypatch.setattr(ab, "_recommended_asr_model", lambda *args, **kw: None)
    monkeypatch.setattr(models, "get_model_catalog", lambda: {})
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [])
    loader = Mock(side_effect=RuntimeError("network-capable loader invoked"))
    monkeypatch.setattr(ab.PyTorchWhisperBackend, "_ensure_pipe", loader)
    assert ab.transcribe_reference("ref.wav", release_after=True) is None
    loader.assert_not_called()


def test_reference_preserves_explicit_remote_provider(monkeypatch):
    from services import asr_backend as ab
    backend = ab.OpenAICompatASRBackend.__new__(ab.OpenAICompatASRBackend)
    transcribe = Mock(return_value={"text": "Configured provider words."})
    monkeypatch.setattr(backend, "transcribe", transcribe)
    monkeypatch.setattr(ab, "active_backend_id", lambda: "openai-compat-asr")
    monkeypatch.setattr(ab, "get_active_asr_backend", lambda **kw: backend)
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "_capture_whisper_repo", lambda: "missing-local-model")
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab, "_repo_installed", lambda *args, **kw: False)
    monkeypatch.setattr(ab, "_recommended_asr_model", lambda *args, **kw: None)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [])
    assert ab.transcribe_reference("ref.wav", release_after=True) == "Configured provider words."
    transcribe.assert_called_once_with("ref.wav", word_timestamps=False)


@pytest.mark.parametrize("fails", [False, True])
def test_reference_releases_all_candidates_when_requested(fails):
    from services.asr_backend import _transcribe_reference_candidates
    first = SimpleNamespace(id="first", unload=Mock(), transcribe=Mock(
        side_effect=RuntimeError("failed") if fails else None,
        return_value={"text": "words"},
    ))
    second = SimpleNamespace(id="second", unload=Mock(), transcribe=Mock(return_value={"text": "words"}))
    assert _transcribe_reference_candidates([first, second], "ref.wav", release_after=True) == "words"
    first.unload.assert_called_once()
    second.unload.assert_called_once()


def test_mlx_unload_releases_library_model_cache(monkeypatch):
    import sys
    from services.asr_backend import MLXWhisperBackend
    holder = SimpleNamespace(model=object(), model_path="local-model")
    clear = Mock()
    monkeypatch.setitem(sys.modules, "mlx_whisper.transcribe", SimpleNamespace(ModelHolder=holder))
    monkeypatch.setitem(sys.modules, "mlx.core", SimpleNamespace(clear_cache=clear))
    MLXWhisperBackend().unload()
    assert holder.model is None
    assert holder.model_path is None
    clear.assert_called_once()


@pytest.mark.parametrize("fails", [False, True])
def test_sidecar_releases_reference_asr_before_loading_tts(monkeypatch, fails):
    from engines.omnivoice_subprocess import main as sidecar
    from services import asr_backend as ab, tts_backend
    released = []
    backend = SimpleNamespace(id="test", transcribe=Mock(
        side_effect=RuntimeError("failed") if fails else None,
        return_value={"text": "words"},
    ), unload=lambda: released.append(True))
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: "missing" if kw.get("purpose") == "dictation" else None)
    monkeypatch.setattr(ab, "load_active_asr_backend", lambda **kw: backend)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [])
    monkeypatch.setattr(tts_backend, "reference_duration_s", lambda _: 1.0)
    def load(_):
        assert released == [True]
        return SimpleNamespace(generate=lambda **kw: [torch.zeros(1, 16)], sampling_rate=24_000)
    monkeypatch.setattr(sidecar, "_load_model", load)
    monkeypatch.setattr(sidecar, "_send", lambda *_: None)
    sidecar._handle_synthesize({"text": "New words.", "ref_audio": "ref.wav"}, None)


def test_catalogue_ct2_reference_is_reused_without_transformers_asr(monkeypatch, tmp_path):
    from collections import OrderedDict
    from services import asr_backend as ab, sherpa_dictation
    from api.routers.setup import models as catalogue

    repo = "deepdml/faster-whisper-large-v3-turbo-ct2"
    assert any(item["repo_id"] == repo for item in catalogue.KNOWN_MODELS)
    snapshot = tmp_path / "ct2-snapshot"
    snapshot.mkdir()
    # Sparse stand-in satisfies the real catalogue's completeness check.
    with (snapshot / "model.bin").open("wb") as weights:
        weights.truncate(catalogue._MIN_WEIGHT_BYTES)
    monkeypatch.setattr(catalogue, "_snapshot_dirs", lambda rid: [str(snapshot)] if rid == repo else [])
    monkeypatch.setattr(catalogue, "_model_supported", lambda _: True)
    monkeypatch.setattr(ab, "asr_model_missing_error", lambda **kw: {"error": "selected model missing"})
    monkeypatch.setattr(ab, "_ref_transcript_cache", OrderedDict())
    monkeypatch.setattr(ab.FasterWhisperBackend, "is_available", classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(sherpa_dictation, "list_specs", lambda: [])
    calls = []
    def transcribe(backend, audio_path, *, word_timestamps):
        calls.append(backend._model_name)
        assert word_timestamps is False
        return {"text": "Installed CT2 transcript."}
    monkeypatch.setattr(ab.FasterWhisperBackend, "transcribe", transcribe)
    unloaded = Mock()
    monkeypatch.setattr(ab.FasterWhisperBackend, "unload", unloaded)
    lookup = Mock(side_effect=AssertionError("must not look for a second ASR copy"))
    monkeypatch.setattr("huggingface_hub.snapshot_download", lookup)
    clip = tmp_path / "reference.wav"
    clip.write_bytes(b"reference audio handled by the ASR test double")
    transcript = ab.transcribe_reference(str(clip))
    assert transcript == "Installed CT2 transcript."
    model = _model()
    model.load_asr_model = Mock(side_effect=AssertionError("second ASR pipeline"))
    prompt = model.create_voice_clone_prompt(
        (torch.full((1, 24_000), 0.1), 24_000),
        ref_text=transcript, preprocess_prompt=False,
    )
    assert prompt.ref_text == transcript
    assert calls == [str(snapshot)]
    unloaded.assert_called_once()
    lookup.assert_not_called()
    model.load_asr_model.assert_not_called()


def _asr_snapshot(tmp_path):
    """A Whisper snapshot that passes ``_has_asr_weights`` (empty dirs do not)."""
    snapshot = tmp_path / "cached-whisper"
    snapshot.mkdir()
    for name in ("config.json", "preprocessor_config.json", "tokenizer.json", "model.safetensors"):
        (snapshot / name).write_text("{}")
    return str(snapshot)


@pytest.mark.parametrize("seconds", [1, 21])
def test_implicit_asr_is_released_before_synthesis(monkeypatch, tmp_path, seconds):
    """An implicitly loaded Whisper must not stay resident next to the TTS weights."""
    model = _model()
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.load_asr_model = Mock(side_effect=lambda **_: setattr(model, "_asr_pipe", object()))
    model.create_voice_clone_prompt(
        (torch.full((1, seconds * 24_000), 0.1), 24_000),
        preprocess_prompt=False,
    )
    model.load_asr_model.assert_called_once()
    assert model._asr_pipe is None


def test_implicit_asr_is_released_when_cloning_fails(monkeypatch, tmp_path):
    model = _model()
    model.transcribe = lambda _: ""  # no speech in any window
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.load_asr_model = Mock(side_effect=lambda **_: setattr(model, "_asr_pipe", object()))
    with pytest.raises(ValueError):
        model.create_voice_clone_prompt(
            (torch.full((1, 21 * 24_000), 0.1), 24_000),
            preprocess_prompt=False,
        )
    model.load_asr_model.assert_called_once()  # it really was loaded, then released
    assert model._asr_pipe is None


def test_explicitly_loaded_asr_stays_resident():
    model = _model()
    explicit = object()
    model._asr_pipe = explicit
    model.create_voice_clone_prompt(
        (torch.full((1, 24_000), 0.1), 24_000),
        preprocess_prompt=False,
    )
    assert model._asr_pipe is explicit


# MPS stays float32: a float16 Whisper on MPS transcribes reference windows as
# "!" / "В!", and that mismatched transcript makes the clone babble.
@pytest.mark.parametrize(("device", "dtype"), [
    ("mps", torch.float32), ("cuda:0", torch.float16), ("cpu", torch.float32),
])
def test_reference_asr_precision_per_device(monkeypatch, device, dtype):
    from omnivoice.models.omnivoice import OmniVoice
    model = OmniVoice.__new__(OmniVoice)
    monkeypatch.setattr(OmniVoice, "device", property(lambda _self: device))
    seen = {}

    def fake_pipeline(task, **kwargs):
        seen.update(kwargs)
        return object()

    # transformers' lazy module may serve `from transformers import pipeline`
    # from the submodule or from a value it cached on itself on first access;
    # patch both so the result does not depend on test order.
    import transformers

    monkeypatch.setattr("transformers.pipelines.pipeline", fake_pipeline)
    monkeypatch.setattr(transformers, "pipeline", fake_pipeline, raising=False)
    model.load_asr_model(model_name="local-whisper")
    assert seen["dtype"] is dtype


def test_release_is_reported_before_synthesis(monkeypatch, tmp_path, capsys):
    """With diagnostics on, the `[diag]` trail shows the recognizer leaving memory."""
    monkeypatch.setenv("OMNIVOICE_DIAG", "1")
    model = _model()
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.load_asr_model = Mock(side_effect=lambda **_: setattr(model, "_asr_pipe", object()))
    model.create_voice_clone_prompt(
        (torch.full((1, 21 * 24_000), 0.1), 24_000),
        preprocess_prompt=False,
    )
    assert model._asr_pipe is None  # the release itself, not just its log line
    assert "[diag] ASR released before synthesis" in capsys.readouterr().err


def test_diagnostics_are_silent_unless_enabled(monkeypatch, tmp_path, capsys):
    monkeypatch.delenv("OMNIVOICE_DIAG", raising=False)
    model = _model()
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.load_asr_model = Mock(side_effect=lambda **_: setattr(model, "_asr_pipe", object()))
    model.create_voice_clone_prompt(
        (torch.full((1, 21 * 24_000), 0.1), 24_000),
        preprocess_prompt=False,
    )
    assert "[diag]" not in capsys.readouterr().err


def test_diagnostics_never_log_the_spoken_reference_text(monkeypatch, tmp_path, capsys):
    """The sidecar's stderr reaches backend logs and crash dialogs, so the
    reference transcript (the user's speech) must never appear in it."""
    monkeypatch.setenv("OMNIVOICE_DIAG", "1")
    model = _model()
    spoken = "private words nobody should read"
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.create_voice_clone_prompt(
        (torch.full((1, 5 * 24_000), 0.1), 24_000),
        ref_text=spoken,
        preprocess_prompt=False,
    )
    err = capsys.readouterr().err
    assert "reference passage" in err
    assert spoken not in err
    assert f"ref_text_chars={len(spoken)}" in err


def test_release_reference_asr_after_unloads_once_at_block_exit():
    """Ranking a long reference transcribes several windows: the recognizer is
    unloaded once when the pass ends, not once per window and not never."""
    from services import asr_backend

    class Backend:
        id = "fake"

        def __init__(self):
            self.calls = 0
            self.unloads = 0

        def transcribe(self, _path, word_timestamps=False):
            self.calls += 1
            return {"text": "some words"}

        def unload(self):
            self.unloads += 1

    backend = Backend()
    with asr_backend.release_reference_asr_after():
        for _ in range(5):
            assert asr_backend._transcribe_reference_candidates([backend], "w.wav") == "some words"
        assert backend.unloads == 0  # still resident between windows
    assert (backend.calls, backend.unloads) == (5, 1)


def test_reference_pass_reuses_one_recognizer_across_windows(monkeypatch):
    """get_active_asr_backend builds a fresh recognizer per call; a deferred
    pass must not hold one per window (several large models at once)."""
    from services import asr_backend as ab

    built = []

    def make_backend(**_kw):
        backend = SimpleNamespace(
            id="fresh", unload=Mock(), transcribe=Mock(return_value={"text": "words"}),
        )
        built.append(backend)
        return backend

    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(
        ab, "asr_model_missing_error",
        lambda **kw: "missing" if kw.get("purpose") == "dictation" else None,
    )
    monkeypatch.setattr(ab, "load_active_asr_backend", make_backend)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [])
    with ab.release_reference_asr_after():
        for window in range(5):
            assert ab.transcribe_reference(f"window-{window}.wav") == "words"
        assert not built[0].unload.called  # still resident between windows
    assert len(built) == 1
    built[0].unload.assert_called_once()


def test_overlapping_clones_do_not_release_each_others_recognizer(monkeypatch, tmp_path):
    """Request B must never find the recognizer gone mid-transcription because
    request A finished first and released the one it had loaded."""
    import threading

    model = _model()
    monkeypatch.setattr("huggingface_hub.snapshot_download", Mock(return_value=_asr_snapshot(tmp_path)))
    model.load_asr_model = Mock(side_effect=lambda **_: setattr(model, "_asr_pipe", object()))
    in_a, go_a, go_b = threading.Event(), threading.Event(), threading.Event()
    seen = []

    def transcribe(_audio):
        seen.append(model._asr_pipe is not None)
        if len(seen) == 1:  # request A, mid-transcription
            in_a.set()
            go_a.wait(5)
        else:  # request B must read after A has finished and released
            go_b.wait(5)
        return "Words from the reference."

    model.transcribe = transcribe
    audio = (torch.full((1, 5 * 24_000), 0.1), 24_000)
    a = threading.Thread(target=lambda: model.create_voice_clone_prompt(audio, preprocess_prompt=False))
    b = threading.Thread(target=lambda: model.create_voice_clone_prompt(audio, preprocess_prompt=False))
    a.start()
    assert in_a.wait(5)
    b.start()
    go_a.set()
    a.join(5)
    go_b.set()
    b.join(5)
    assert not a.is_alive() and not b.is_alive()
    assert seen == [True, True]
    assert model._asr_pipe is None


def test_recognizers_are_released_before_the_models_prompt_builder_runs(monkeypatch):
    """When installed recognizers find no words the model falls back to its own
    Whisper: the installed ones are unloaded as soon as they fail (before any
    fallback loads), and are not unloaded a second time later in the pass."""
    from services import asr_backend as ab

    events = []
    backend = SimpleNamespace(
        id="fresh", transcribe=Mock(return_value={"text": ""}), unload=Mock(side_effect=lambda: events.append("unload")),
    )
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(
        ab, "asr_model_missing_error",
        lambda **kw: "missing" if kw.get("purpose") == "dictation" else None,
    )
    monkeypatch.setattr(ab, "load_active_asr_backend", lambda **_kw: backend)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [])
    with ab.release_reference_asr_after():
        assert ab.transcribe_reference("window.wav") is None  # no words found
        assert events == ["unload"]  # released right away, not kept while fallbacks are tried
        ab.release_reference_asr_now()
        assert events == ["unload"]  # nothing left to release: no second unload
        events.append("model prompt builder")  # the model's fallback Whisper loads here
    assert events == ["unload", "model prompt builder"]  # nothing unloaded again at exit


def test_unhelpful_recognizer_is_released_before_the_next_one_works():
    """The selected recognizer finds no words, an installed fallback does: the
    first must be gone before the second starts, the second stays resident for
    the next window, and each is unloaded exactly once overall."""
    from services import asr_backend as ab

    events = []

    def backend(name, text):
        return SimpleNamespace(
            id=name,
            transcribe=Mock(side_effect=lambda *_a, **_k: events.append(f"{name} transcribes") or {"text": text}),
            unload=Mock(side_effect=lambda: events.append(f"{name} unloaded")),
        )

    first, second = backend("selected", ""), backend("fallback", "Words.")
    with ab.release_reference_asr_after():
        assert ab._transcribe_reference_candidates([first, second], "w1.wav") == "Words."
        assert events == ["selected transcribes", "selected unloaded", "fallback transcribes"]
        assert ab._transcribe_reference_candidates([first, second], "w2.wav") == "Words."  # next window
        assert "fallback unloaded" not in events  # the helpful one is reused, not reloaded per window
    assert events.count("fallback unloaded") == 1
    assert events.count("selected unloaded") == 2  # once per window it failed, never twice in a row


def test_next_window_tries_the_resident_recognizer_first(monkeypatch):
    """Window 1: the selected recognizer finds nothing, the installed fallback
    succeeds. Window 2 must go straight to the resident fallback instead of
    reloading the selected recognizer beside it."""
    from services import asr_backend as ab

    events = []

    def backend(name, text):
        return SimpleNamespace(
            id=name,
            transcribe=Mock(side_effect=lambda *_a, **_k: events.append(f"{name} transcribes") or {"text": text}),
            unload=Mock(side_effect=lambda: events.append(f"{name} unloaded")),
        )

    selected, fallback = backend("selected", ""), backend("fallback", "Words.")
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(
        ab, "asr_model_missing_error",
        lambda **kw: "missing" if kw.get("purpose") == "dictation" else None,
    )
    monkeypatch.setattr(ab, "load_active_asr_backend", lambda **_kw: selected)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [fallback])
    with ab.release_reference_asr_after():
        assert ab.transcribe_reference("w1.wav") == "Words."
        assert events == ["selected transcribes", "selected unloaded", "fallback transcribes"]
        events.clear()
        assert ab.transcribe_reference("w2.wav") == "Words."
        assert events == ["fallback transcribes"]  # selected is never touched again: no overlap
    assert events == ["fallback transcribes", "fallback unloaded"]  # unloaded once, at the end


def test_winner_that_fails_a_window_is_not_reloaded_for_the_same_window(monkeypatch):
    """The resident recognizer is tried first; when it finds nothing on this
    window it is unloaded and must not be retried through the other lists."""
    from services import asr_backend as ab

    events = []
    fallback_answers = iter(["Words.", ""])  # window 1 words, window 2 nothing

    selected = SimpleNamespace(
        id="selected",
        transcribe=Mock(side_effect=lambda *_a, **_k: events.append("selected transcribes") or {"text": ""}),
        unload=Mock(side_effect=lambda: events.append("selected unloaded")),
    )
    fallback = SimpleNamespace(
        id="fallback",
        transcribe=Mock(side_effect=lambda *_a, **_k: events.append("fallback transcribes") or {"text": next(fallback_answers)}),
        unload=Mock(side_effect=lambda: events.append("fallback unloaded")),
    )
    monkeypatch.setattr(ab, "_ref_audio_fingerprint", lambda _: None)
    monkeypatch.setattr(ab, "active_backend_id", lambda: "faster-whisper")
    monkeypatch.setattr(
        ab, "asr_model_missing_error",
        lambda **kw: "missing" if kw.get("purpose") == "dictation" else None,
    )
    monkeypatch.setattr(ab, "load_active_asr_backend", lambda **_kw: selected)
    monkeypatch.setattr(ab, "_installed_reference_fallbacks", lambda _: [fallback])
    with ab.release_reference_asr_after():
        assert ab.transcribe_reference("w1.wav") == "Words."
        events.clear()
        assert ab.transcribe_reference("w2.wav") is None  # nobody finds words on window 2
    # fallback ran once (winner-first) and was unloaded; neither list retried it
    assert events.count("fallback transcribes") == 1
    assert events.count("fallback unloaded") == 1


def test_release_now_outside_a_pass_is_a_noop():
    from services import asr_backend as ab

    ab.release_reference_asr_now()  # API process / tests: must not raise or touch anything


def test_diag_never_raises(monkeypatch):
    from omnivoice.models import omnivoice as ov

    monkeypatch.setitem(sys.modules, "psutil", None)  # import fails inside _diag
    ov._diag("anything")
