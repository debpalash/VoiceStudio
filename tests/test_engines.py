"""Phase 3 — TTS / ASR / LLM adapter registries."""
import os
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import sys
import types

import pytest
from services import tts_backend, asr_backend, llm_backend


# ── TTS ─────────────────────────────────────────────────────────────────────


def test_tts_registry_lists_all_backends():
    rows = tts_backend.list_backends()
    ids = {r["id"] for r in rows}
    # Core set must exist; optional engines (kittentts, mlx-audio) may be
    # added as platform support lands — only assert the baseline.
    assert {"omnivoice", "voxcpm2", "moss-tts-nano"}.issubset(ids)
    for r in rows:
        assert set(r) >= {"id", "display_name", "available", "reason"}
        evidence = r["execution_evidence"]
        assert evidence["implementation_variant"]
        assert evidence["declared_device_families"] == r["gpu_compat"]
        assert evidence["evidence_state"] in {
            "loaded", "not_loaded", "subprocess_loaded_provider_unreported"
        }
        assert evidence["runtime_versions"]["python"]


def test_tts_voxcpm2_unavailable_message_is_actionable():
    ok, msg = tts_backend.VoxCPM2Backend.is_available()
    # On most CI boxes voxcpm isn't installed; message must tell the user how
    # — including the >=2.0.3 version floor (Apple-Silicon audio quality).
    if not ok:
        assert 'pip install "voxcpm>=2.0.3"' in msg


def test_tts_voxcpm2_upgrade_hint_reaches_list_backends(monkeypatch):
    """The reported instance of the dropped-ok-message class: an old-but-working
    voxcpm install reports available=True, and its ">=2.0.3" upgrade advice must
    reach the UI via the additive `hint` field instead of being dropped."""
    monkeypatch.setitem(sys.modules, "voxcpm", types.ModuleType("voxcpm"))
    monkeypatch.setattr(tts_backend, "_voxcpm_installed_version", lambda: "2.0.1")
    entry = next(e for e in tts_backend.list_backends() if e["id"] == "voxcpm2")
    assert entry["available"] is True
    assert entry["reason"] is None
    assert entry["hint"] is not None
    assert "2.0.3" in entry["hint"]
    assert 'pip install --upgrade "voxcpm>=2.0.3"' in entry["hint"]


def test_tts_moss_nano_unavailable_message_points_to_install():
    ok, msg = tts_backend.MossTTSNanoBackend.is_available()
    if not ok:
        # Either transformers is missing or the moss_tts_nano package itself.
        assert "moss_tts_nano" in msg or "transformers" in msg


def test_tts_moss_nano_language_count():
    # Non-redundant niche: 20 langs including Arabic/Hebrew/Persian/Korean.
    langs = tts_backend.MossTTSNanoBackend().supported_languages
    assert len(langs) == 20
    assert {"ar", "he", "fa", "ko", "tr"}.issubset(set(langs))


def test_tts_active_backend_env_override(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "voxcpm2")
    assert tts_backend.active_backend_id() == "voxcpm2"
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    # Reset prefs in case an earlier test persisted a choice.
    from core import prefs as _prefs
    _prefs.set_("tts_backend", "omnivoice")
    assert tts_backend.active_backend_id() == "omnivoice"


# ── #919: sherpa-onnx gates on OMNIVOICE_SHERPA_MODEL ────────────────────────
# sherpa-onnx ships no bundled model, so with the package installed but no model
# dir configured it must report unavailable-with-a-reason — not "ready" and then
# a generate-time config error the OOM catch-all mislabeled. A fake module makes
# these deterministic whether or not sherpa-onnx is installed on CI.


def test_tts_sherpa_unavailable_without_model_env(monkeypatch):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    monkeypatch.delenv("OMNIVOICE_SHERPA_MODEL", raising=False)
    ok, msg = tts_backend.SherpaOnnxBackend.is_available()
    assert ok is False
    assert "OMNIVOICE_SHERPA_MODEL" in msg   # names the exact env var
    assert "model.onnx" in msg               # says what to point it at


def test_tts_sherpa_unavailable_when_model_dir_lacks_onnx(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    monkeypatch.setenv("OMNIVOICE_SHERPA_MODEL", str(tmp_path))  # empty dir
    ok, msg = tts_backend.SherpaOnnxBackend.is_available()
    assert ok is False
    assert "model.onnx" in msg


def test_tts_sherpa_available_when_model_env_points_at_model(monkeypatch, tmp_path):
    monkeypatch.setitem(sys.modules, "sherpa_onnx", types.ModuleType("sherpa_onnx"))
    (tmp_path / "model.onnx").write_bytes(b"")
    monkeypatch.setenv("OMNIVOICE_SHERPA_MODEL", str(tmp_path))
    ok, _msg = tts_backend.SherpaOnnxBackend.is_available()
    assert ok is True


def test_tts_sherpa_setup_snippet_registered():
    # The Compat Matrix's copy-paste setup line must exist for the now
    # path-gated engine (parity with Confucius4/dots/MOSS).
    assert "OMNIVOICE_SHERPA_MODEL" in tts_backend._SETUP_SNIPPETS["sherpa-onnx"]


def test_tts_active_backend_prefs_fallback(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    _prefs.set_("tts_backend", "moss-tts-nano")
    assert tts_backend.active_backend_id() == "moss-tts-nano"
    # Env var must beat prefs.
    monkeypatch.setenv("OMNIVOICE_TTS_BACKEND", "voxcpm2")
    assert tts_backend.active_backend_id() == "voxcpm2"


def test_tts_sample_rate_per_backend():
    assert tts_backend.OmniVoiceBackend().sample_rate == 24000
    assert tts_backend.VoxCPM2Backend().sample_rate == 48000
    assert tts_backend.MossTTSNanoBackend().sample_rate == 48000


def test_tts_unknown_backend_raises():
    with pytest.raises(ValueError):
        tts_backend.get_backend_class("not-a-real-one")


# ── #981 — MLX-Audio curated-model selection ─────────────────────────────
#
# MLXAudioBackend.__init__ used to resolve its active model ONLY from
# OMNIVOICE_MLX_AUDIO_MODEL, invisible to Settings and unchangeable without
# restarting the process with an env var set. It must now mirror
# active_backend_id()'s env > prefs > default resolution.


def test_mlx_audio_model_id_resolves_via_prefs(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    _prefs.set_("mlx_audio_model_id", "outetts")
    be = tts_backend.MLXAudioBackend()
    assert be._model_id == tts_backend.MLXAudioBackend.CURATED_MODELS["outetts"]


def test_mlx_audio_model_id_env_overrides_prefs(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    _prefs.set_("mlx_audio_model_id", "outetts")
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "csm")
    be = tts_backend.MLXAudioBackend()
    assert be._model_id == tts_backend.MLXAudioBackend.CURATED_MODELS["csm"]


def test_mlx_audio_model_id_defaults_to_kokoro(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    be = tts_backend.MLXAudioBackend()
    assert be._model_id == tts_backend.MLXAudioBackend.CURATED_MODELS["kokoro"]


def test_get_active_tts_backend_reconstructs_on_mlx_model_switch(monkeypatch, tmp_path):
    """A curated-model-only change (same backend id 'mlx-audio') must
    invalidate the cached instance too — otherwise picking a different
    curated model in Settings has no effect until an app restart."""
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    _prefs.set_("tts_backend", "mlx-audio")
    _prefs.set_("mlx_audio_model_id", "kokoro")
    tts_backend.reset_active_backend()
    try:
        be1 = tts_backend.get_active_tts_backend()
        assert be1._model_id == tts_backend.MLXAudioBackend.CURATED_MODELS["kokoro"]
        # Same instance on a repeat call with nothing changed (still cached).
        assert tts_backend.get_active_tts_backend() is be1

        _prefs.set_("mlx_audio_model_id", "outetts")
        be2 = tts_backend.get_active_tts_backend()
        assert be2 is not be1
        assert be2._model_id == tts_backend.MLXAudioBackend.CURATED_MODELS["outetts"]
    finally:
        tts_backend.reset_active_backend()
        _prefs.set_("tts_backend", "omnivoice")


# ── ASR ─────────────────────────────────────────────────────────────────────


def test_asr_registry_lists_backends():
    rows = asr_backend.list_backends()
    ids = {r["id"] for r in rows}
    assert {"mlx-whisper", "pytorch-whisper"}.issubset(ids)
    required = {
        "actual_execution_provider",
        "actual_execution_device",
        "cpu_fallback_reason",
        "cpu_fallback_stage",
        "runtime_versions",
    }
    assert all(required.issubset(row["execution_evidence"]) for row in rows)


def test_asr_auto_detects():
    bid = asr_backend.active_backend_id()
    # WhisperX is now the default cross-platform pick (better wav2vec2 word
    # alignment for lip-sync); mlx / pytorch / faster-whisper are fallbacks.
    assert bid in {"whisperx", "faster-whisper", "mlx-whisper", "pytorch-whisper"}


def test_asr_env_override(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    assert asr_backend.active_backend_id() == "pytorch-whisper"


# ── ASR selection resolution (Model Catalogue ASR picker) ────────────────
# Same env > prefs > auto-detect contract as TTS. The env var MUST keep
# winning so existing `OMNIVOICE_ASR_BACKEND` pins don't change behavior now
# that the Settings picker writes the prefs key.


def test_asr_active_backend_prefs_fallback(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    _prefs.set_("asr_backend", "moonshine")
    assert asr_backend.active_backend_id() == "moonshine"
    # Env var must beat prefs.
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "pytorch-whisper")
    assert asr_backend.active_backend_id() == "pytorch-whisper"


def test_asr_auto_detects_when_no_env_no_prefs(monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    assert asr_backend.active_backend_id() in {
        "whisperx", "faster-whisper", "mlx-whisper", "pytorch-whisper",
    }


def test_get_active_asr_backend_follows_prefs_switch_without_restart(monkeypatch, tmp_path):
    """#981 class (fixed on the TTS side): a Settings pick must take effect on
    the next transcribe, not after an app restart. get_active_asr_backend()
    re-resolves the id per call, so a prefs write switches immediately."""
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_ASR_BACKEND", raising=False)
    _prefs.set_("asr_backend", "pytorch-whisper")
    assert isinstance(
        asr_backend.get_active_asr_backend(), asr_backend.PyTorchWhisperBackend)
    _prefs.set_("asr_backend", "moonshine")
    assert isinstance(
        asr_backend.get_active_asr_backend(), asr_backend.MoonshineASRBackend)


def test_asr_unknown_backend_raises(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "not-a-real-asr")
    with pytest.raises(ValueError):
        asr_backend.get_active_asr_backend()


# ── LLM ─────────────────────────────────────────────────────────────────────


def test_llm_registry_includes_off():
    rows = llm_backend.list_backends()
    ids = {r["id"] for r in rows}
    assert ids == {"openai-compat", "off"}


def test_llm_off_chat_raises_actionable(monkeypatch):
    # Force selection to Off regardless of env.
    monkeypatch.setenv("OMNIVOICE_LLM_BACKEND", "off")
    be = llm_backend.get_active_llm_backend()
    assert isinstance(be, llm_backend.OffBackend)
    with pytest.raises(RuntimeError) as ei:
        be.chat(system="x", user="y")
    # Error message tells the user what env vars unlock Cinematic translate.
    assert "TRANSLATE_BASE_URL" in str(ei.value)


def test_llm_auto_selects_off_when_nothing_configured(clean_llm_env):
    # clean_llm_env (conftest) clears the FULL provider env surface — a
    # hand-picked 4-var list left e.g. LLM_DEFAULT_PROVIDER / GROQ_API_KEY
    # standing when an earlier test imported `main` (which dotenv-loads the
    # developer's .env into os.environ), reading as 'configured' (#878).
    assert llm_backend.active_backend_id() == "off"


def test_llm_auto_selects_openai_compat_when_configured(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_LLM_BACKEND", raising=False)
    monkeypatch.setenv("TRANSLATE_BASE_URL", "http://localhost:11434/v1")
    monkeypatch.setenv("TRANSLATE_API_KEY", "local")
    monkeypatch.setenv("TRANSLATE_MODEL", "fixture-model")
    # is_available itself also needs the openai pkg to import — that's fine;
    # translator.py already depends on it in this repo.
    try:
        import openai  # noqa: F401
    except ImportError:
        pytest.skip("openai package not available in this environment")
    assert llm_backend.active_backend_id() == "openai-compat"


# ── HF Hub closed-client recovery (#880) ────────────────────────────────────
#
# huggingface_hub ≥1.x shares one global httpx client; if it gets closed
# mid-lifecycle, an engine's first-use model download inside the generate
# path dies with "Cannot send a request, as the client has been closed".
# The load must retry exactly once with a fresh client — and must NOT retry
# unrelated failures.


def test_hf_retry_recovers_from_closed_client_once():
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            raise RuntimeError("Cannot send a request, as the client has been closed.")
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, what="test") == "model"
    assert len(calls) == 2


def test_hf_retry_matches_wrapped_closed_client_error():
    # An engine can wrap the httpx error — detection walks the chain.
    calls = []

    def loader():
        calls.append(1)
        if len(calls) == 1:
            try:
                raise RuntimeError("Cannot send a request, as the client has been closed.")
            except RuntimeError as inner:
                raise RuntimeError("KittenTTS init failed") from inner
        return "model"

    assert tts_backend._retry_once_with_fresh_hf_client(loader, what="test") == "model"
    assert len(calls) == 2


def test_hf_retry_does_not_retry_unrelated_errors():
    calls = []

    def loader():
        calls.append(1)
        raise ValueError("bad checkpoint id")

    with pytest.raises(ValueError):
        tts_backend._retry_once_with_fresh_hf_client(loader, what="test")
    assert len(calls) == 1


def test_hf_retry_is_single_shot():
    # A second closed-client failure propagates (the generation classifier
    # then labels it a network problem) — no infinite retry loop.
    calls = []

    def loader():
        calls.append(1)
        raise RuntimeError("Cannot send a request, as the client has been closed.")

    with pytest.raises(RuntimeError):
        tts_backend._retry_once_with_fresh_hf_client(loader, what="test")
    assert len(calls) == 2


# ── #977: MLX-Audio Kokoro language-code resolution ─────────────────────────
# Kokoro's own vendored pipeline (mlx_audio.tts.models.kokoro.pipeline)
# hard-asserts `lang_code` against a fixed single-letter table. The old code
# blindly truncated a full language name — "Dutch"[:2].lower() == "du" — into
# that assert, crashing with an unreadable `(lang_code, LANG_CODES)` repr
# instead of a clean error. The resolution tests need the real mlx-audio
# package (Apple-Silicon-only) since they validate against ITS installed
# table, never a hardcoded guess; they skip cleanly where mlx-audio isn't
# installed (every non-macOS-ARM CI runner).


def test_mlx_audio_kokoro_resolves_supported_language_names():
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    resolve = tts_backend.resolve_kokoro_lang_code
    assert resolve("English") == "a"
    assert resolve("Spanish") == "e"
    assert resolve("French") == "f"
    assert resolve("Hindi") == "h"
    assert resolve("Italian") == "i"
    assert resolve("Portuguese") == "p"
    assert resolve("Japanese") == "j"
    assert resolve("Chinese") == "z"
    # Some callers may already pass an ISO code — those resolve unchanged
    # through Kokoro's own ALIASES table, not just our full-name map.
    assert resolve("es") == "e"
    assert resolve("en-gb") == "b"


@pytest.mark.parametrize("language", ["Dutch", "German"])
def test_mlx_audio_kokoro_rejects_unsupported_language_cleanly(language):
    # The literal #977 report case ("Dutch") plus one more Kokoro doesn't
    # support ("German") — neither's first two letters happen to alias to a
    # valid Kokoro code, so both used to crash.
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    with pytest.raises(ValueError) as ei:
        tts_backend.resolve_kokoro_lang_code(language)
    msg = str(ei.value)
    assert language in msg
    assert "Kokoro" in msg
    assert "English" in msg  # names what Kokoro DOES support


# The label derivation takes the tables as arguments, so it runs on every
# platform — unlike the resolution tests above, which need the real
# Apple-Silicon-only package. The bug it guards is invisible on the runners
# that skip.


def test_kokoro_resolves_british_english_label(monkeypatch):
    pipeline = types.ModuleType("mlx_audio.tts.models.kokoro.pipeline")
    pipeline.ALIASES = {"en-gb": "b"}
    pipeline.LANG_CODES = {"b": "British English"}
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.models.kokoro.pipeline", pipeline)
    assert tts_backend.resolve_kokoro_lang_code("British English") == "b"


def test_kokoro_advertised_labels_round_trip_through_installed_table(monkeypatch):
    pipeline = types.ModuleType("mlx_audio.tts.models.kokoro.pipeline")
    pipeline.ALIASES = {"en": "a", "en-gb": "b", "xx": "x"}
    pipeline.LANG_CODES = {"a": "American English", "b": "British English", "x": "Newly Added Language"}
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.models.kokoro.pipeline", pipeline)
    labels = tts_backend._kokoro_supported_labels(pipeline.ALIASES, pipeline.LANG_CODES)
    assert {tts_backend.resolve_kokoro_lang_code(label) for label in labels} == {"a", "b", "x"}


def test_kokoro_supported_labels_name_a_code_only_an_alias_reaches():
    """A code reached only through an alias is still named.

    British English is `en-gb` -> "b". The supported list must include it
    alongside the other installed language codes.
    """
    aliases = {"en": "a", "en-gb": "b", "es": "e"}
    lang_codes = {"a": "American English", "b": "British English", "e": "es"}
    labels = tts_backend._kokoro_supported_labels(aliases, lang_codes)
    assert "British English" in labels
    assert "English" in labels  # the full name a caller can pass
    assert "Spanish" in labels


def test_kokoro_supported_labels_track_the_installed_table():
    """A language a later mlx-audio adds appears without editing our map.

    That is the whole point of reading the vendored table rather than a
    hardcoded one.
    """
    aliases = {"en": "a", "xx": "x"}
    lang_codes = {"a": "American English", "x": "Newly Added Language"}
    assert "Newly Added Language" in tts_backend._kokoro_supported_labels(aliases, lang_codes)


def test_kokoro_supported_labels_prefer_the_passable_full_name():
    """The label is the name a caller can actually pass.

    `LANG_CODES` describes Spanish as the ISO tag "es", but "Spanish" is what
    the frontend sends, so that is the more useful label to print.
    """
    labels = tts_backend._kokoro_supported_labels({"es": "e"}, {"e": "es"})
    assert labels == ["Spanish"]


def test_kokoro_supported_labels_skip_codes_the_installed_table_lacks():
    """An install exposing one language is not described as supporting eight.

    Our map knows eight; the installed table is what decides.
    """
    labels = tts_backend._kokoro_supported_labels({"it": "i"}, {"i": "it"})
    assert labels == ["Italian"]


def test_mlx_audio_kokoro_error_names_british_english():
    """The real message names British English, which it previously omitted."""
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    with pytest.raises(ValueError) as ei:
        tts_backend.resolve_kokoro_lang_code("Persian")
    # Reachable as "en-gb" and previously missing from the message.
    assert "British English" in str(ei.value)


def test_mlx_audio_generate_rejects_unsupported_kokoro_language_before_calling_model():
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["kokoro"]
    backend._ensure_loaded = lambda: None  # never actually load the model

    def _boom_generate(**kw):
        raise AssertionError("model.generate() must not run for a rejected language")

    backend._model = types.SimpleNamespace(generate=_boom_generate)

    with pytest.raises(ValueError, match="Dutch"):
        backend.generate("hello", language="Dutch")


def test_mlx_audio_generate_passes_ref_text_through_for_cloning():
    # #1012/#1013: MLXAudioBackend.generate() read voice/ref_audio/language/
    # speed from kwargs but silently dropped ref_text — CSM (sesame.py) only
    # builds its cloning context when BOTH ref_audio and ref_text are
    # present, so cloning on CSM always raised an opaque
    # "IndexError: list index out of range" deep inside mlx-audio instead of
    # ever attempting the clone. Community-diagnosed with the exact fix.
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    backend = tts_backend.MLXAudioBackend()
    backend._ensure_loaded = lambda: None

    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=__import__("numpy").zeros(4))])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", ref_audio="/tmp/ref.wav", ref_text="the reference line")

    assert captured.get("ref_text") == "the reference line"
    assert captured.get("ref_audio") == "/tmp/ref.wav"


def test_mlx_audio_generate_omits_ref_text_without_ref_audio():
    # ref_text alone (no ref_audio) means nothing to CSM's context builder —
    # don't pass a stray kwarg an engine that isn't cloning doesn't expect.
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    backend = tts_backend.MLXAudioBackend()
    backend._ensure_loaded = lambda: None

    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=__import__("numpy").zeros(4))])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", ref_text="orphaned text, no audio")

    assert "ref_text" not in captured


def test_mlx_audio_generate_design_path_unaffected_without_any_ref():
    # Absorbed from community PR #1015 (MahdiHedhli) — the design/instruct
    # path (no ref_audio, no ref_text at all) must stay untouched by the
    # ref_text forwarding fix; neither kwarg may leak into the model call.
    pytest.importorskip("mlx_audio", reason="mlx-audio is Apple-Silicon-only")
    backend = tts_backend.MLXAudioBackend()
    backend._ensure_loaded = lambda: None

    captured = {}

    def _fake_generate(**kw):
        captured.update(kw)
        return iter([types.SimpleNamespace(audio=__import__("numpy").zeros(4))])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello")

    assert "ref_text" not in captured
    assert "ref_audio" not in captured


def test_mlx_audio_generate_auto_language_skips_lang_code_entirely():
    # Matches the "Auto" convention other engines in this file use
    # (OmniVoiceBackend.generate(), _run_backend_inference) — never resolved,
    # never forwarded as lang_code.
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["kokoro"]
    backend._ensure_loaded = lambda: None
    seen_kwargs = {}

    def _fake_generate(**kw):
        seen_kwargs.update(kw)
        return iter([types.SimpleNamespace(audio=[0.0, 0.0, 0.0, 0.0])])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", language="Auto")
    assert "lang_code" not in seen_kwargs


def test_mlx_audio_generate_non_kokoro_model_ignores_kokoro_validation():
    # Qwen3-TTS (and CSM/Dia/Chatterbox/MeloTTS/OuteTTS) don't use Kokoro's
    # lang_code convention — a language Kokoro would reject must NOT be
    # rejected when a different curated model is active (#977 nuance).
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["qwen3-tts"]
    backend._ensure_loaded = lambda: None
    seen_kwargs = {}

    def _fake_generate(**kw):
        seen_kwargs.update(kw)
        return iter([types.SimpleNamespace(audio=[0.0, 0.0, 0.0, 0.0])])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    # The curated qwen3-tts model is the VoiceDesign variant, which cannot
    # generate without a description — mlx-audio raises outright. This test
    # passed without one only because `instruct` was being dropped before it
    # ever reached the library (#1405); supply one so the scenario is real.
    # German: documented for Qwen3-TTS, absent from Kokoro's table.
    backend.generate("hello", language="German", instruct="a warm narrator")  # must not raise
    assert seen_kwargs.get("lang_code") == "ge"
    assert seen_kwargs.get("instruct") == "a warm narrator"


def test_mlx_audio_generate_rejects_language_outside_curated_model_set():
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["qwen3-tts"]
    backend._ensure_loaded = lambda: None
    backend._model = types.SimpleNamespace(generate=lambda **kw: iter([]))
    with pytest.raises(ValueError, match="support language='Dutch'"):
        backend.generate("hello", language="Dutch", instruct="a warm narrator")


def test_mlx_audio_outetts_receives_reference_as_array(monkeypatch):
    # mlx-audio's OuteTTS crashes on a file path (UnboundLocalError: resampled_audio).
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["outetts"]
    backend._ensure_loaded = lambda: None
    loaded = object()
    monkeypatch.setattr(tts_backend, "_outetts_reference_array", lambda path: (path, loaded))
    seen = {}

    def _fake_generate(**kw):
        seen.update(kw)
        return iter([types.SimpleNamespace(audio=[0.0, 0.0])])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("hello", ref_audio="/tmp/ref.wav")
    assert seen["ref_audio"] == ("/tmp/ref.wav", loaded)


def test_mlx_audio_eos_ids_accept_single_int_from_transformers_5(monkeypatch):
    import sys

    module = types.ModuleType("mlx_audio.lm.generate")
    module._eos_ids = lambda tokenizer: set(tokenizer.eos_token_ids)
    package = types.ModuleType("mlx_audio.lm")
    package.generate = module
    monkeypatch.setitem(sys.modules, "mlx_audio.lm", package)
    monkeypatch.setitem(sys.modules, "mlx_audio.lm.generate", module)
    tts_backend._harden_mlx_audio_eos_ids()
    tts_backend._harden_mlx_audio_eos_ids()  # idempotent
    assert module._eos_ids(types.SimpleNamespace(eos_token_ids=7)) == {7}
    assert module._eos_ids(types.SimpleNamespace(eos_token_ids=[1, 2])) == {1, 2}


def test_cached_engine_follows_a_model_switch(monkeypatch):
    # Selecting another curated mlx-audio model must not keep synthesizing
    # with the model the cached instance was built for.
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "kokoro")
    first = tts_backend.get_engine_instance(tts_backend.MLXAudioBackend)
    unloaded = []
    first.unload = lambda: unloaded.append(True)
    assert tts_backend.get_engine_instance(tts_backend.MLXAudioBackend) is first
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "outetts")
    second = tts_backend.get_engine_instance(tts_backend.MLXAudioBackend)
    assert second is not first
    assert second.model_identity() == tts_backend.MLXAudioBackend.CURATED_MODELS["outetts"]
    assert unloaded == [True]


def test_model_switch_never_unloads_an_engine_mid_job(monkeypatch):
    monkeypatch.setattr(tts_backend, "_ENGINE_INSTANCES", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_LAST_USED", {})
    monkeypatch.setattr(tts_backend, "_ENGINE_IN_USE", {})
    monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "kokoro")
    first = tts_backend.get_engine_instance(tts_backend.MLXAudioBackend)
    unloaded = []
    first.unload = lambda: unloaded.append(True)
    with tts_backend.engine_in_use(first):
        monkeypatch.setenv("OMNIVOICE_MLX_AUDIO_MODEL", "csm")
        second = tts_backend.get_engine_instance(tts_backend.MLXAudioBackend)
        assert not unloaded
    assert unloaded == [True]
    assert second is not first


def test_melotts_names_its_missing_text_package(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "g2p_en", None)  # import raises ImportError
    with pytest.raises(RuntimeError, match="g2p_en"):
        tts_backend._ensure_melotts_text_frontend()


def test_melotts_fetches_missing_nltk_data_into_the_app_folder(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "g2p_en", types.ModuleType("g2p_en"))
    downloads = []

    def _find(lookup):
        if not any(lookup.endswith(name) for name, _ in downloads):
            raise LookupError(lookup)

    fake_nltk = types.SimpleNamespace(
        data=types.SimpleNamespace(path=[], find=_find),
        download=lambda package, download_dir, quiet: downloads.append((package, download_dir)) or True,
    )
    monkeypatch.setitem(sys.modules, "nltk", fake_nltk)
    monkeypatch.setattr("core.config.DATA_DIR", str(tmp_path))
    tts_backend._ensure_melotts_text_frontend()
    assert [name for name, _ in downloads] == ["averaged_perceptron_tagger_eng", "cmudict"]
    assert {path for _, path in downloads} == {str(tmp_path / "nltk_data")}
    assert fake_nltk.data.path[0] == str(tmp_path / "nltk_data")


def test_mlx_audio_dia_receives_speaker_tagged_text():
    backend = tts_backend.MLXAudioBackend()
    backend._model_id = backend.CURATED_MODELS["dia"]
    backend._ensure_loaded = lambda: None
    seen = {}

    def _fake_generate(**kw):
        seen.update(kw)
        return iter([types.SimpleNamespace(audio=[0.0, 0.0])])

    backend._model = types.SimpleNamespace(generate=_fake_generate)
    backend.generate("Hello there.", ref_audio="/tmp/ref.wav", ref_text="Earlier words.")
    assert seen["text"] == "[S1] Hello there."
    assert seen["ref_text"] == "[S1] Earlier words."
    backend.generate("[S1] Hi. [S2] Hey.")
    assert seen["text"] == "[S1] Hi. [S2] Hey."
