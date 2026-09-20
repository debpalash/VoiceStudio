"""#2104: every TTS engine must enforce its declared ``supported_languages`` set.

The reporter rendered a 30-second script in English, Polish and Spanish
through every TTS engine and got wrong-accent audio from four of them
when the language was outside their declared set. The base class now
owns the check so engines only have to declare their set honestly.

These tests cover the base-class helper directly (no model weights
needed) and assert the batch dispatch invokes it on a real
``generate_batch`` call.
"""
from __future__ import annotations

import pytest



# ── helpers ──────────────────────────────────────────────────────────────


@pytest.fixture
def _StubBackend():
    from services.tts_backend import TTSBackend

    class StubBackend(TTSBackend):
        """A throwaway backend whose ``supported_languages`` and
        ``display_name`` are set per-instance for the test.

        The base class is abstract (it has three ``@abstractmethod``
        properties), so any test that touches ``supported_languages`` or
        ``display_name`` needs a subclass that fills them in. ``generate``
        is overridden to raise so a bug that lets an unsupported call
        through never silently succeeds.
        """

        def __init__(self, supported, *, display_name="Stub Engine", id="stub"):
            self._supported = list(supported)
            self._display_name = display_name
            self._id = id
            self.calls: list[dict] = []

        @property
        def id(self) -> str:  # type: ignore[override]
            return self._id

        @property
        def display_name(self) -> str:  # type: ignore[override]
            return self._display_name

        @property
        def sample_rate(self) -> int:  # type: ignore[override]
            return 24000

        @property
        def supported_languages(self) -> list[str]:  # type: ignore[override]
            return self._supported

        @classmethod
        def is_available(cls):  # type: ignore[override]
            return True, "ready"

        def generate(self, text, **kw):  # type: ignore[override]
            self.calls.append({"text": text, **kw})
            # Caller is asserting a *rejection*; reaching this body is the
            # regression we are testing for. Returning a 1-sample tensor is
            # enough to let the batch test distinguish "no raise" from "raise".
            import torch
            return torch.zeros(1, 1)

    return StubBackend

# ── _normalize_language_code ────────────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        # No preference
        (None, None),
        ("", None),
        ("   ", None),
        ("auto", None),
        ("Auto", None),
        ("AUTO", None),
        # Exact ISO
        ("en", "en"),
        ("EN", "en"),
        ("  en  ", "en"),
        # BCP-47 / region suffix → first 2 alpha chars of the head (BCP-47
        # form: language[-region[-script]]; the head is the language).
        ("zh-CN", "zh"),
        ("cmn-Hans", "cmn"),  # 3-letter base — preserved for engine-side match
        ("ZH_CN", "zh"),       # underscore separator, not BCP-47 — noise
        # Display-name → ISO (the picker sometimes sends the label).
        ("English", "en"),
        ("Spanish", "es"),
        ("Mandarin", "zh"),  # common picker label — Mandarin is Chinese
        # 3-letter code preserved for engine-side prefix match.
        ("zho", "zho"),
        ("eng", "eng"),
        ("cmn", "cmn"),
    ],
)
def test_normalize_language_code(_StubBackend, raw, expected):
    be = _StubBackend(["en", "zh", "es"])
    assert be._normalize_language_code(raw) == expected


# ── _check_language: rejection path ─────────────────────────────────────


def test_check_language_raises_valueerror_with_engine_name_and_supported_set(_StubBackend):
    """The reporter's symptom was a 30s Polish sample rendered by an
    English-only engine as 55s of English phonemes. The fix raises
    BEFORE generation, names the engine, lists what it does support, and
    points at the picker. _language_rejection_or (generation.py:1055)
    turns it into the standard user-facing rewrite."""
    be = _StubBackend(["en"], display_name="KittenTTS (English, 8 preset voices)")
    with pytest.raises(ValueError) as excinfo:
        be._check_language("pl")
    msg = str(excinfo.value)
    assert "KittenTTS" in msg, "the user must learn WHICH engine refused"
    assert "'pl'" in msg, "...and which language was asked for"
    assert "'en'" in msg, "...and what IS supported"
    assert "Model Catalogue" in msg, "...and where to switch engine"


def test_check_language_accepts_every_declared_set_member(_StubBackend):
    be = _StubBackend(["zh", "en", "ja", "ko", "de", "fr", "es", "id", "it",
                       "th", "pt", "ru", "ms", "vi"])
    # Every one of the 14 must pass — Confucius4 case before #2104 had
    # ``["multi"]`` and silently mis-spoke Polish.
    for lang in ("zh", "en", "ja", "ko", "de", "fr", "es", "id", "it",
                 "th", "pt", "ru", "ms", "vi"):
        be._check_language(lang)  # no raise


def test_check_language_accepts_display_names_that_resolve_to_supported_iso(_StubBackend):
    """The frontend picker sends the label, not the ISO. ``Spanish`` is
    how the user spells what an ``["es"]`` engine understands; without
    the display-name map, the base check would reject ``Spanish``
    despite ``es`` being in the set."""
    be = _StubBackend(["es", "en"])
    be._check_language("Spanish")
    be._check_language("spanish")  # case-insensitive


def test_check_language_accepts_3letter_code_when_engine_advertises_it(_StubBackend):
    """3-letter ISO 639 codes are passed through unmodified. An engine
    that declares ``["cmn", "yue", "zh"]`` (3-letter form) is matched
    exactly by the base class; the engine itself decides whether 3-letter
    tokens are valid. The 2-letter-prefix fold was intentionally dropped
    because it varies per engine and a wrong fold silently mis-routes.
    """
    be = _StubBackend(["zh", "yue", "cmn"])
    be._check_language("cmn")  # exact match in the 3-letter set


# ── _check_language: skip paths ──────────────────────────────────────────


@pytest.mark.parametrize("auto_value", [None, "", "auto", "Auto", "AUTO", "   "])
def test_check_language_skips_when_no_preference_expressed(_StubBackend, auto_value):
    """``auto`` / None / empty must NEVER raise — the picker offers
    every language and the engine picks its own default."""
    be = _StubBackend(["en"])
    be._check_language(auto_value)


def test_check_language_skips_for_multi_engine(_StubBackend):
    """``["multi"]`` is the open-ended contract: OmniVoice's 600-language
    zero-shot, mlx-audio's per-model multiplexer, PocketTTS' own
    per-engine strict check. The base class must NOT clobber their
    semantics with a 2-letter-code check."""
    be = _StubBackend(["multi"], display_name="VoiceStudio (k2-fsa/OmniVoice, 600+ languages)")
    for lang in ("pl", "ar", "vi", "th", "anything"):
        be._check_language(lang)


def test_check_language_skips_for_empty_engine_list(_StubBackend):
    """An engine that hasn't declared a set yet (``[]``) is a
    configuration error, but the base class must NOT crash the call —
    the engine's own check (or its absence) is the engine's problem."""
    be = _StubBackend([])
    be._check_language("pl")


@pytest.mark.parametrize("language", ["Klingon", "??", "Ukrainian", "Albanian"])
def test_check_language_rejects_unavailable_named_language(_StubBackend, language):
    be = _StubBackend(["en"])
    with pytest.raises(ValueError, match="doesn't support"):
        be._check_language(language)


def test_every_picker_label_resolves_to_an_explicit_code(_StubBackend):
    import json
    from pathlib import Path
    root = Path(__file__).resolve().parents[3]
    be = _StubBackend(["en"])
    for picker in [root / "frontend/src/languages.json", root / "electron/src/renderer/src/lib/languages.json"]:
        for label in json.loads(picker.read_text()):
            code = be._normalize_language_code(label)
            if label.lower() == "auto":
                assert code is None
            else:
                assert code and code.isascii() and code.isalpha() and len(code) in (2, 3), label


# ── generate_batch: enforcement wired through ──────────────────────────


def test_generate_batch_rejects_off_set_language_before_calling_generate(_StubBackend):
    """A bug-shape regression: a caller picks Polish on a single-language
    engine via ``generate_batch``. The base class must raise BEFORE the
    per-item ``generate`` loop runs, so the engine never produces
    wrong-accent audio."""
    import torch

    be = _StubBackend(
        ["en"],
        display_name="KittenTTS (English, 8 preset voices)",
    )
    with pytest.raises(ValueError, match="KittenTTS"):
        # 1 item is enough — the check is on the whole call's language.
        be.generate_batch(["hello"], language="pl")
    assert be.calls == [], (
        "the rejection must happen before any per-item generate() ran; "
        f"got {be.calls}"
    )


def test_generate_batch_passes_supported_language_through_to_generate(_StubBackend):
    """Confucius4 case: a caller picks Spanish (in the 14-language set)
    via ``generate_batch`` — must reach the engine, not be rejected."""
    import torch

    be = _StubBackend(["zh", "en", "ja", "ko", "de", "fr", "es", "id", "it",
                       "th", "pt", "ru", "ms", "vi"],
                      display_name="Confucius4-TTS")
    out = be.generate_batch(["hola"], language="es")
    assert len(out) == 1
    assert be.calls[0]["language"] == "es"


def test_generate_batch_skips_check_when_language_is_auto(_StubBackend):
    """``auto`` is the picker-default — must reach the engine so its
    own default-language logic runs (VoxCPM2, mlx-audio, OmniVoice
    each pick their own default)."""
    be = _StubBackend(["en"])
    out = be.generate_batch(["hello"], language="auto")
    assert len(out) == 1


def test_generate_batch_multi_engine_never_rejects(_StubBackend):
    """A ``["multi"]`` engine processes the batch normally — the base
    class skips the check, the engine's own logic decides."""
    be = _StubBackend(["multi"])
    out = be.generate_batch(["hi"], language="Klingon")
    assert len(out) == 1


# ── Confucius4-specific: the engine that triggered the issue ───────────


def test_confucius4_declares_its_real_14_languages_not_multi():
    """The reporter's defect 4: Confucius4 returns ``["multi"]`` for an
    engine that supports 14 languages. The new declaration matches
    upstream's README; the base-class check rejects anything else."""
    import os as _os, sys as _sys
    backend_root = _os.path.join(
        _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
        "..", "..", "backend",
    )
    if backend_root not in _sys.path:
        _sys.path.insert(0, backend_root)
    from engines.confucius4 import Confucius4Backend

    langs = Confucius4Backend().supported_languages
    # The literal contract: 14 distinct ISO codes from upstream README.
    expected = {
        "zh", "en", "ja", "ko", "de", "fr", "es", "id", "it",
        "th", "pt", "ru", "ms", "vi",
    }
    assert set(langs) == expected, (
        f"Confucius4 advertises 14 languages upstream; the engine "
        f"contract should match. Got: {sorted(langs)}"
    )
    assert "multi" not in langs, (
        "``[\"multi\"]`` lies about a finite-set engine; the base-class "
        "check is now wired, so the truthful list must be declared."
    )

def test_batch_validates_all_languages_before_first_generation(_StubBackend):
    backend = _StubBackend(["en"])
    with pytest.raises(ValueError, match="pl"):
        backend.generate_batch(["hello", "czesc"], language=["en", "pl"])
    assert backend.calls == []


@pytest.mark.parametrize("module,class_name", [
    ("services.tts_backend", "VoxCPM2Backend"),
    ("services.tts_backend", "MossTTSNanoBackend"),
    ("services.tts_backend", "KittenTTSBackend"),
    ("services.tts_backend", "CosyVoiceBackend"),
    ("services.tts_backend", "GPTSoVITSBackend"),
    ("engines.confucius4", "Confucius4Backend"),
    ("engines.audiocpp", "AudioCPPBackend"),
    ("engines.indextts", "IndexTTS2Backend"),
    ("engines.voxcpm2_subprocess", "VoxCPM2SubprocessBackend"),
    ("engines.cosyvoice_subprocess", "CosyVoiceSubprocessBackend"),
    ("engines.moss_tts_nano_subprocess", "MossTTSNanoSubprocessBackend"),
])
def test_direct_generation_rejects_before_loading_or_starting_sidecar(module, class_name):
    from importlib import import_module
    cls = getattr(import_module(module), class_name)
    # No constructor/model/runtime: unsupported input must fail before any of
    # those resources are accessed, including override methods forwarding lang.
    backend = object.__new__(cls)
    with pytest.raises(ValueError, match="doesn't support"):
        backend.generate("hello", language="not-a-supported-language")


@pytest.mark.parametrize('language', ['Spanish', 'es-MX'])
def test_cosyvoice_uses_canonical_cross_lingual_tag(monkeypatch, language):
    from services.tts_backend import CosyVoiceBackend
    from types import SimpleNamespace
    import torch
    seen = []
    def inference(text, *args, **kwargs):
        seen.append(text)
        return [{'tts_speech': torch.zeros(1, 10)}]
    backend = object.__new__(CosyVoiceBackend)
    backend._model = SimpleNamespace(inference_cross_lingual=inference)
    monkeypatch.setattr(backend, '_ensure_loaded', lambda: None)
    backend.generate('hola', ref_audio='voice.wav', language=language)
    assert seen == [CosyVoiceBackend.LANG_TAGS['es'] + 'hola']


@pytest.mark.parametrize('language', ['Korean', 'ko-KR'])
def test_gptsovits_uses_canonical_target_language(monkeypatch, language):
    from services.tts_backend import GPTSoVITSBackend
    from services import outbound_http
    import json
    seen = []
    def capture(*args, **kwargs):
        seen.append(json.loads(kwargs['body']))
        raise RuntimeError('captured request')
    monkeypatch.setattr(outbound_http, 'open_trusted_endpoint', capture)
    backend = object.__new__(GPTSoVITSBackend)
    backend._url = 'http://127.0.0.1:9880'
    with pytest.raises(RuntimeError, match='captured request'):
        backend.generate('hello', ref_audio='voice.wav', language=language)
    assert seen[0]['text_lang'] == 'ko'
