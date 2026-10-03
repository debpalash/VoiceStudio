"""ASR auto-detect must respect the hardware it's running on (#1127).

The picker used to probe WhisperX first, unconditionally. WhisperX (and
faster-whisper) are CTranslate2, which has **no Metal backend** — so on Apple
Silicon they transcribe on the *CPU* while the GPU sits idle. Because WhisperX is
always installed, the MPS branch further down was unreachable in practice and
every Mac dub ran on the CPU.

Measured on an M2, one 30 s dub chunk of whisper-large-v3:

    WhisperX (CPU)              90.4 s   <- 3x SLOWER than realtime
    MLX (GPU)                   20.5 s
    MLX (GPU) + forced align    20.3 s   <- same word timings, ~4x faster

That is how a 16-minute video became a ~48-minute transcribe and looked like a
hang. These tests pin the pick, and — just as importantly — pin that we did not
buy the speed by throwing away lip-sync accuracy: the MLX path keeps WhisperX's
wav2vec2 forced alignment (±10-30 ms) rather than settling for Whisper's own
native word timestamps (±100-300 ms).
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest

from services import asr_backend as ab


@pytest.fixture(autouse=True)
def _clear_align_cache(monkeypatch):
    ab._ALIGN_CACHE.clear()
    monkeypatch.delenv(ab._ALIGN_DEVICE_ENV, raising=False)
    yield
    ab._ALIGN_CACHE.clear()


def _probe(available: set[str]):
    """Stub _probe_available: only the named backend ids report available."""
    return lambda cls: cls.id in available


# ── the pick ────────────────────────────────────────────────────────────────


def test_apple_silicon_picks_mlx_not_the_cpu_bound_whisperx(monkeypatch):
    """The regression. Before the fix this returned "whisperx" — i.e. the CPU."""
    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", _probe({"mlx-whisper", "whisperx", "faster-whisper"}))
    assert ab._auto_detect() == "mlx-whisper"


def test_cuda_and_linux_still_get_whisperx(monkeypatch):
    """No MPS => CTranslate2 can use the GPU (CUDA) or is simply the best
    available. WhisperX must remain the default everywhere else."""
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_probe_available", _probe({"whisperx", "faster-whisper"}))
    assert ab._auto_detect() == "whisperx"


def test_a_mac_without_mlx_installed_falls_back_to_whisperx(monkeypatch):
    """MLX is the preference, not a requirement — never strand a user."""
    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", _probe({"whisperx", "faster-whisper"}))
    assert ab._auto_detect() == "whisperx"


def test_fallback_chain_still_degrades_to_faster_whisper_then_pytorch(monkeypatch):
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_probe_available", _probe({"faster-whisper"}))
    assert ab._auto_detect() == "faster-whisper"
    monkeypatch.setattr(ab, "_probe_available", _probe(set()))
    assert ab._auto_detect() == "pytorch-whisper"


def test_an_explicitly_pinned_backend_still_wins(monkeypatch):
    """Anyone who pinned an engine keeps it — auto-detect must not override."""
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "faster-whisper")
    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", _probe({"mlx-whisper"}))
    assert ab.active_backend_id() == "faster-whisper"


def test_explicit_omnivoice_alias_routes_shared_asr_to_pytorch(monkeypatch):
    """The public env spelling requested by #1582 reaches the shared backend.

    Dubbing, cloning, batch transcription, and the transcription API all use
    this selector/load seam; accepting the alias here keeps those consumers
    on the PyTorch-native Whisper path that can use ROCm/HIP.
    """
    attached_pipeline = object()
    monkeypatch.setenv("OMNIVOICE_ASR_BACKEND", "omnivoice")

    selected = ab.get_active_asr_backend(asr_pipe=attached_pipeline)

    assert isinstance(selected, ab.PyTorchWhisperBackend)
    assert selected._pipe is attached_pipeline


# ── we did not buy speed with lip-sync accuracy ─────────────────────────────


def test_forced_align_is_engine_agnostic_so_mlx_keeps_whisperx_timing(monkeypatch):
    """The whole reason the swap is safe: alignment takes plain segments, so
    MLX's GPU transcript gets the *same* wav2vec2 boundaries WhisperX would give."""
    segments = [{"text": "hello world", "start": 0.0, "end": 1.0}]
    aligned = [{"text": "hello world", "start": 0.0, "end": 1.0,
                "words": [{"word": "hello", "start": 0.01, "end": 0.4}]}]

    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "load_align_model", lambda lang, dev: ("model", "meta"))
    fake = type("W", (), {"align": staticmethod(lambda *a, **k: {"segments": aligned})})
    monkeypatch.setitem(__import__("sys").modules, "whisperx", fake)

    assert ab.forced_align(segments, object(), "en") == aligned


def test_alignment_retries_on_cpu_before_giving_up_on_timing(monkeypatch):
    """An aligner that hits an unimplemented MPS op must NOT silently cost us the
    word timing — it must fall back to the CPU, which always works."""
    tried: list[str] = []
    aligned = [{"text": "hi", "start": 0.0, "end": 1.0, "words": [{"word": "hi", "start": 0.0}]}]

    def align(segs, model, meta, audio, device, **kw):
        tried.append(device)
        if device == "mps":
            raise NotImplementedError("aten::_ctc_loss not implemented for MPS")
        return {"segments": aligned}

    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setattr(ab, "load_align_model", lambda lang, dev: ("model", "meta"))
    fake = type("W", (), {"align": staticmethod(align)})
    monkeypatch.setitem(__import__("sys").modules, "whisperx", fake)

    out = ab.forced_align([{"text": "hi", "start": 0.0, "end": 1.0}], object(), "en")
    assert tried == ["mps", "cpu"], "must retry on CPU, not abandon alignment"
    assert out == aligned  # timing preserved


def test_alignment_load_failure_on_mps_retries_the_cpu_model(monkeypatch):
    """A failed device transfer during loading must not skip the CPU retry."""
    import sys
    import types
    loaded = []
    segments = [{"text": "hi", "start": 0.0, "end": 1.0}]
    aligned = [dict(segments[0], words=[{"word": "hi", "start": 0.1, "end": 0.8}])]

    def load_align_model(language_code, device):
        loaded.append(device)
        if device == "mps":
            raise RuntimeError("MPS device transfer failed")
        return "cpu model", "metadata"

    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setitem(sys.modules, "whisperx", types.SimpleNamespace(
        load_align_model=load_align_model,
        align=lambda *a, **kw: {"segments": aligned},
    ))
    assert ab.forced_align(segments, object(), "en") == aligned
    assert loaded == ["mps", "cpu"]
    # The failed MPS load is cached; another call still reaches cached CPU.
    assert ab.forced_align(segments, object(), "en") == aligned
    assert loaded == ["mps", "cpu"]


def test_a_language_with_no_aligner_keeps_its_native_timestamps(monkeypatch):
    """~20 languages have wav2vec2 aligners. The other 626 must still transcribe."""
    segments = [{"text": "x", "start": 0.0, "end": 1.0, "words": [{"word": "x", "start": 0.0}]}]
    monkeypatch.setattr(ab, "load_align_model", lambda lang, dev: None)
    assert ab.forced_align(segments, object(), "yue") == segments


def test_alignment_failure_never_loses_the_transcript(monkeypatch):
    """Worse timing beats no transcript. Never raise out of alignment."""
    segments = [{"text": "x", "start": 0.0, "end": 1.0}]
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "load_align_model", lambda lang, dev: ("m", "meta"))

    def boom(*a, **k):
        raise RuntimeError("aligner exploded")

    fake = type("W", (), {"align": staticmethod(boom)})
    monkeypatch.setitem(__import__("sys").modules, "whisperx", fake)

    assert ab.forced_align(segments, object(), "en") == segments


def test_empty_segments_short_circuit(monkeypatch):
    assert ab.forced_align([], object(), "en") == []


# ── parakeet-mlx (Apple Silicon Parakeet TDT v3) ────────────────────────────


@pytest.fixture()
def _fresh_capture_singleton(monkeypatch):
    """Isolate the capture-backend singleton and pin a deterministic
    environment: no sherpa pref, MLX whisper + faster-whisper available."""
    monkeypatch.setattr(ab, "_capture_backend", None)
    monkeypatch.setattr(ab, "_capture_backend_key", None)
    monkeypatch.setattr(ab, "dictation_model_id", lambda: None)
    monkeypatch.setattr(ab.MLXWhisperBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab.FasterWhisperBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    yield


def test_parakeet_mlx_is_registered_with_hint_and_mps_compat():
    assert "parakeet-mlx" in ab._REGISTRY
    assert ab._REGISTRY["parakeet-mlx"] is ab.ParakeetMLXBackend
    assert ab.ParakeetMLXBackend.gpu_compat == ("mps",)
    assert ab._INSTALL_HINTS.get("parakeet-mlx")


def test_capture_prefers_installed_parakeet_mlx(_fresh_capture_singleton, monkeypatch):
    """On Apple Silicon with the Parakeet weights ON DISK and a covered
    (European) locale language, dictation/capture picks parakeet-mlx over
    mlx-whisper turbo."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_parakeet_mlx_installed", lambda: True)
    monkeypatch.setattr(ab, "_locale_language", lambda: "en")
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.ParakeetMLXBackend)
    # The dictation preflight mirror agrees: the repo it would check is the
    # installed parakeet model, so no asr_model_missing 409 can fire.
    assert ab._capture_whisper_repo() == ab._PARAKEET_MLX_DEFAULT


def test_capture_keeps_whisper_for_uncovered_language(_fresh_capture_singleton, monkeypatch):
    """Language parity: parakeet-mlx knows exactly 25 European languages —
    a CJK/etc locale must keep the ~100-language whisper tier even with the
    parakeet weights installed. Installing a model must never silently break
    dictation that worked yesterday."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_parakeet_mlx_installed", lambda: True)
    monkeypatch.setattr(ab, "_locale_language", lambda: "ja")
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.MLXWhisperBackend)
    # Preflight mirror stays in lock-step with the picker.
    assert ab._capture_whisper_repo() == ab._MLX_MODEL_TURBO


def test_capture_keeps_whisper_without_locale_signal(_fresh_capture_singleton, monkeypatch):
    """No usable locale (C/POSIX, empty launchd GUI env) → no evidence the
    user's language is covered → keep whisper (never regress on a guess)."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_parakeet_mlx_installed", lambda: True)
    monkeypatch.setattr(ab, "_locale_language", lambda: None)
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.MLXWhisperBackend)


def test_env_pinned_parakeet_model_bypasses_language_gate(_fresh_capture_singleton, monkeypatch):
    """ASR_MODEL_PARAKEET_MLX is an explicit engine choice — trust it over
    the locale heuristic."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_parakeet_mlx_installed", lambda: True)
    monkeypatch.setattr(ab, "_locale_language", lambda: "ja")
    monkeypatch.setenv("ASR_MODEL_PARAKEET_MLX", ab._PARAKEET_MLX_DEFAULT)
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.ParakeetMLXBackend)


def test_installing_parakeet_mid_session_rebuilds_capture_singleton(
        _fresh_capture_singleton, monkeypatch):
    """The warm-singleton cache key includes the parakeet gate, so installing
    the model from the engine's Weights list in Model Catalogue takes effect on the next utterance —
    not after an app restart (the stale-singleton regression)."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_locale_language", lambda: "en")
    installed = {"v": False}
    monkeypatch.setattr(ab, "_parakeet_mlx_installed", lambda: installed["v"])
    b1 = ab.get_capture_asr_backend()
    assert isinstance(b1, ab.MLXWhisperBackend)
    # Warm reuse while nothing changed.
    assert ab.get_capture_asr_backend() is b1
    installed["v"] = True  # user installs parakeet from the engine's Weights list in Model Catalogue
    b2 = ab.get_capture_asr_backend()
    assert isinstance(b2, ab.ParakeetMLXBackend)


def test_locale_language_parses_env_and_skips_posix(monkeypatch):
    import locale as _locale
    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(_locale, "getlocale", lambda: (None, None))
    assert ab._locale_language() is None
    monkeypatch.setenv("LANG", "C.UTF-8")
    assert ab._locale_language() is None  # POSIX default is not a language
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    assert ab._locale_language() == "de"
    monkeypatch.setenv("LC_ALL", "uk_UA.UTF-8")  # LC_ALL outranks LANG
    assert ab._locale_language() == "uk"


def test_parakeet_transcribe_never_claims_english(monkeypatch):
    """The backend serves 25 languages and cannot see the model's detected
    pick — it must report the caller's requested language (or None), never a
    hardcoded 'en' that downstream consumers would treat as detected truth."""
    class _Sent:
        text, start, end, tokens = "hallo welt", 0.0, 1.0, []

    class _Res:
        text, sentences = "hallo welt", [_Sent()]

    class _Model:
        def transcribe(self, path, **kw):
            return _Res()

    backend = ab.ParakeetMLXBackend()
    monkeypatch.setattr(backend, "_ensure_model", lambda: None)
    backend._model = _Model()
    assert backend.transcribe("a.wav")["language"] is None
    assert backend.transcribe("a.wav", language="de")["language"] == "de"


def test_capture_never_auto_downloads_parakeet_mlx(_fresh_capture_singleton, monkeypatch):
    """Package installed but weights NOT cached → the picker must skip
    parakeet-mlx (no surprise ~1.2 GB download) and keep MLX turbo. Exercises
    the REAL _parakeet_mlx_installed via the model store's is_cached."""
    from api.routers.setup import models as setup_models
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (True, "ready")))
    monkeypatch.setattr(ab, "_locale_language", lambda: "en")  # pass the language gate
    monkeypatch.setattr(setup_models, "is_cached", lambda repo_id: False)
    monkeypatch.setattr(setup_models, "cache_is_complete", lambda meta: True)
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.MLXWhisperBackend)
    assert not isinstance(backend, ab.ParakeetMLXBackend)


def test_capture_skips_parakeet_mlx_when_package_unavailable(_fresh_capture_singleton, monkeypatch):
    """Not Apple Silicon / package missing → unchanged pre-existing behavior."""
    monkeypatch.setattr(ab.ParakeetMLXBackend, "is_available",
                        classmethod(lambda cls: (False, "requires Apple Silicon")))
    monkeypatch.setattr(
        ab, "_parakeet_mlx_installed",
        lambda: pytest.fail("installed-check must not run when unavailable"),
    )
    backend = ab.get_capture_asr_backend()
    assert isinstance(backend, ab.MLXWhisperBackend)


def test_offline_auto_detect_never_picks_parakeet_mlx(monkeypatch):
    """Language-coverage parity: the whisper family stays the universal
    offline (dub/batch) default — 25 EU languages is not 99."""
    monkeypatch.setattr(ab, "_mps_available", lambda: True)
    monkeypatch.setattr(
        ab, "_probe_available",
        _probe({"parakeet-mlx", "mlx-whisper", "whisperx", "faster-whisper"}),
    )
    assert ab._auto_detect() == "mlx-whisper"


def test_pinned_parakeet_mlx_is_preflighted_not_silently_downloaded(monkeypatch):
    """A user who PINS parakeet-mlx as the offline engine with no weights on
    disk gets the typed asr_model_missing payload (download CTA), never a
    silent first-load download."""
    from api.routers.setup import models as setup_models
    monkeypatch.setattr(ab, "active_backend_id", lambda: "parakeet-mlx")
    monkeypatch.setattr(setup_models, "is_cached", lambda repo_id: False)
    monkeypatch.setattr(setup_models, "cache_is_complete", lambda meta: True)
    # The catalog entry is darwin-arm64-only; make the host check deterministic
    # so the recommended-pick assertion holds on Linux CI too.
    monkeypatch.setattr(setup_models, "_model_supported", lambda m: True)
    payload = ab.asr_model_missing_error()
    assert payload is not None
    assert payload["error"] == "asr_model_missing"
    assert payload["missing_repo_id"] == ab._PARAKEET_MLX_DEFAULT
    # The catalog carries the model, so the CTA recommends exactly it.
    assert payload["recommended"]["repo_id"] == ab._PARAKEET_MLX_DEFAULT


def test_parakeet_mlx_token_to_word_mapping():
    """Subword tokens (leading space = word start) merge into whisper-shaped
    word dicts with the span's start/end."""
    class Tok:
        def __init__(self, text, start, end):
            self.text, self.start, self.end = text, start, end

    words = ab.ParakeetMLXBackend._tokens_to_words([
        Tok(" Hel", 0.10, 0.20), Tok("lo", 0.20, 0.30),
        Tok(" wor", 0.40, 0.55), Tok("ld", 0.55, 0.70), Tok(".", 0.70, 0.75),
    ])
    assert words == [
        {"word": "Hello", "start": 0.10, "end": 0.30},
        {"word": "world.", "start": 0.40, "end": 0.75},
    ]


# Real-weights smoke — opt-in like the supertonic smoke tests: downloads
# ~460 MB (the tiny 110M TDT-CTC model, NOT the 1.2 GB v3), so CI never runs
# it. Locally: OMNIVOICE_SMOKE=1 pytest tests/test_asr_device_aware_autodetect.py
_PARAKEET_MLX_TINY = "mlx-community/parakeet-tdt_ctc-110m"


@pytest.mark.skipif(
    os.environ.get("OMNIVOICE_SMOKE") != "1",
    reason="network test (~460 MB weights); set OMNIVOICE_SMOKE=1 to run",
)
def test_parakeet_mlx_real_transcribe_smoke(tmp_path):
    """End-to-end on real weights (Apple Silicon only): load, transcribe a
    generated WAV, and verify the standard return shape survives the trip."""
    ok, why = ab.ParakeetMLXBackend.is_available()
    if not ok:
        pytest.skip(f"parakeet-mlx unavailable on this host: {why}")
    import numpy as np
    import soundfile as sf
    sr = 16000
    t = np.linspace(0, 1.0, sr, endpoint=False)
    wav = (0.1 * np.sin(2 * np.pi * 220 * t)).astype("float32")
    path = tmp_path / "tone.wav"
    sf.write(path, wav, sr)

    backend = ab.ParakeetMLXBackend(model_name=_PARAKEET_MLX_TINY)
    backend.ensure_loaded()
    try:
        out = backend.transcribe(str(path), word_timestamps=True)
    finally:
        backend.unload()
    assert set(out) >= {"text", "chunks", "segments", "language"}
    assert isinstance(out["text"], str)
    for seg in out["segments"]:
        assert isinstance(seg["text"], str) and seg["text"].strip()
        assert seg["start"] <= seg["end"]
        for w in seg["words"]:
            assert w["word"] and w["start"] <= w["end"]
    for chunk in out["chunks"]:
        assert chunk["text"] and len(chunk["timestamp"]) == 2


# ── ROCm (#1529): the Apple lesson repeated on the AMD axis ─────────────────
# ROCm torch masquerades as CUDA (torch.cuda.is_available() is True, devices
# are "cuda"), but CTranslate2 has no HIP backend — whisperx handed "cuda" to
# NVIDIA's runtime on an RX 7900 XTX and died with "CUDA driver version is
# insufficient for CUDA runtime version". pytorch-whisper rides torch itself,
# so it is the engine that actually uses the HIP GPU.


def test_rocm_host_picks_pytorch_whisper_not_ctranslate2(monkeypatch):
    """The #1529 regression: before the fix this returned "whisperx"."""
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(
        ab, "_probe_available", _probe({"whisperx", "faster-whisper", "pytorch-whisper"})
    )
    assert ab._auto_detect() == "pytorch-whisper"


def test_rocm_host_without_transformers_still_degrades_to_whisperx_on_cpu(monkeypatch):
    """pytorch-whisper unavailable → whisperx is still correct, on the CPU."""
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_probe_available", _probe({"whisperx", "faster-whisper"}))
    assert ab._auto_detect() == "whisperx"


def test_real_cuda_is_untouched_by_the_rocm_branch(monkeypatch):
    monkeypatch.setattr(ab, "_mps_available", lambda: False)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(
        ab, "_probe_available", _probe({"whisperx", "faster-whisper", "pytorch-whisper"})
    )
    assert ab._auto_detect() == "whisperx"


def test_ctranslate2_never_gets_cuda_on_a_rocm_build(monkeypatch):
    """The crash itself: whisperx's device pick must refuse HIP-flavoured cuda."""
    from core.device_caps import HostCaps

    # The compute-device override gate consults the probe FIRST; pin it to a
    # CUDA family so this test keeps exercising the ROCm-specific refusal
    # (on a cpu-family CI host the gate would short-circuit before it).
    monkeypatch.setattr(
        "core.device_caps.detect_host_caps",
        lambda: HostCaps(family="cuda", available_families=("cuda", "cpu")),
    )
    monkeypatch.setattr(ab, "_rocm_torch", lambda: True)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    assert ab._ctranslate2_cuda_ok() is False
    assert ab.WhisperXBackend._pick_device() == ("cpu", "int8")

    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    assert ab._ctranslate2_cuda_ok() is True
    assert ab.WhisperXBackend._pick_device() == ("cuda", "float16")


def test_ctranslate2_gate_fails_safe_when_the_probe_breaks(monkeypatch):
    """A broken capability probe must mean CPU, never a torch-derived guess —
    guessing would bypass a cpu override and re-open #1529 on ROCm."""
    def _boom():
        raise RuntimeError("probe exploded")

    monkeypatch.setattr("core.device_caps.detect_host_caps", _boom)
    monkeypatch.setattr(ab, "_cuda_reported_available", lambda: True)
    monkeypatch.setattr(ab, "_rocm_torch", lambda: False)
    assert ab._ctranslate2_cuda_ok() is False
