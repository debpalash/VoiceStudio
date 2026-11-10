"""#3685: Chinese (Simplified) / (Traditional) on engines that speak Chinese.

The pickers offer the two written standards ("Chinese (Simplified)" stored
as ``cmn-Hans``, "Chinese (Traditional)" as ``cmn-Hant``) while finite
engines declare ``zh``. The synthesis check, the picker inventory the dub
gate reads, and the request an engine receives must all agree.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def stub():
    from services.tts_backend import TTSBackend

    class Stub(TTSBackend):
        id = "stub"
        display_name = "Stub"
        sample_rate = 24000

        @property
        def supported_languages(self):  # type: ignore[override]
            return ["zh", "en"]

        @classmethod
        def is_available(cls):  # type: ignore[override]
            return True, "ready"

        def generate(self, text, **kw):  # type: ignore[override]
            raise AssertionError("not used")

    return object.__new__(Stub)


@pytest.mark.parametrize("language", [
    "Chinese (Simplified)", "Chinese (Traditional)", "cmn-Hans", "cmn-Hant", "Mandarin", "zh-TW",
])
def test_chinese_scripts_are_spoken_as_declared_chinese(stub, language):
    assert stub._check_language(language) == "zh"


@pytest.mark.parametrize("language", ["cmn", "Min Nan Chinese", "Hakka Chinese"])
def test_other_chinese_codes_and_varieties_are_not_guessed(stub, language):
    # #2104: a bare "cmn" and the other Sinitic languages are not folded.
    with pytest.raises(ValueError, match="doesn't support"):
        stub._check_language(language)


@pytest.mark.parametrize("engine", ["voxcpm2", "indextts2", "confucius4-tts", "cosyvoice"])
def test_chinese_engines_advertise_both_scripts(engine, monkeypatch):
    from services import tts_backend

    monkeypatch.delenv("OMNIVOICE_INDEXTTS_DIR", raising=False)
    options = tts_backend.language_options(engine)
    assert {"chinese (simplified)", "chinese (traditional)"} <= set(options)
    assert "min nan chinese" not in options


def test_english_only_engine_does_not_advertise_chinese():
    from services import tts_backend

    options = tts_backend.language_options("kittentts")
    assert "chinese (simplified)" not in options


@pytest.mark.parametrize(("module", "name"), [
    ("engines.confucius4", "Confucius4Backend"),
    ("engines.cosyvoice_subprocess", "CosyVoiceSubprocessBackend"),
])
def test_sidecars_receive_the_declared_chinese_code(module, name, monkeypatch):
    from importlib import import_module

    cls = getattr(import_module(module), name)
    sent = []
    backend = object.__new__(cls)
    monkeypatch.setattr(backend, "_exchange", lambda msg, **_kw: sent.append(msg), raising=False)
    backend.generate("ni hao", ref_audio="/tmp/ref.wav", language="Chinese (Traditional)")
    assert sent[0]["language"] == "zh"
