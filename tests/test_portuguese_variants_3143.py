"""#3143 / #2752: Brazilian and European Portuguese as separate choices.

The pickers offer "Portuguese (Brazil)" and "Portuguese (Portugal)". Every
engine and the bundled vocabulary know a single Portuguese, so the variant
must resolve to that base language at the engine boundary while the dialect
still reaches what can use it (number words, translation prompts), and a
saved plain "Portuguese"/"pt" keeps working as Brazilian Portuguese.
"""
from __future__ import annotations

import asyncio

import pytest


# ── the shared table ────────────────────────────────────────────────────────


@pytest.mark.parametrize(("language", "dialect"), [
    ("Portuguese (Brazil)", "pt-BR"),
    ("Portuguese (Portugal)", "pt-PT"),
    ("portuguese  (portugal)", "pt-PT"),
    ("pt-PT", "pt-PT"),
    ("pt_pt", "pt-PT"),
    ("pt-br", "pt-BR"),
    # Saved settings from before the split mean Brazilian Portuguese.
    ("Portuguese", "pt-BR"),
    ("pt", "pt-BR"),
    # Languages without a variant split carry no dialect here.
    ("en-US", None),
    ("Spanish", None),
    ("Auto", None),
    (None, None),
])
def test_dialect_for(language, dialect):
    from core.language_variants import dialect_for
    assert dialect_for(language) == dialect


def test_base_language_only_rewrites_variant_names():
    from core.language_variants import base_language
    assert base_language("Portuguese (Portugal)") == "Portuguese"
    assert base_language("Portuguese (Brazil)") == "Portuguese"
    for unchanged in ("Portuguese", "pt-PT", "English", None, "Auto"):
        assert base_language(unchanged) == unchanged


def test_base_language_converts_native_batch_lists():
    from core.language_variants import base_language
    assert base_language(["Portuguese (Portugal)", "English", None]) == [
        "Portuguese", "English", None,
    ]
    assert base_language(("Portuguese (Brazil)",)) == ("Portuguese",)


def test_regional_target_splits_into_base_and_dialect():
    from core.language_variants import split_target
    assert split_target("pt-PT") == ("pt", "pt-PT")
    assert split_target("pt") == ("pt", None)
    assert split_target("es-AR") == ("es-AR", None)  # no picker split for Spanish


# ── TTS engine boundary ─────────────────────────────────────────────────────


@pytest.fixture
def recording_backend():
    from services.tts_backend import TTSBackend

    class Recording(TTSBackend):
        id = "recording"
        display_name = "Recording"

        def __init__(self, supported=("multi",)):
            self._supported = list(supported)
            self.calls: list[dict] = []

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
            self.calls.append(kw)
            import torch
            return torch.zeros(1, 1)

    return Recording


@pytest.mark.parametrize(("picked", "engine_sees"), [
    ("Portuguese (Portugal)", "Portuguese"),
    ("Portuguese (Brazil)", "Portuguese"),
    ("Portuguese", "Portuguese"),
    ("pt-PT", "pt-PT"),
])
def test_engines_receive_the_language_they_know(recording_backend, picked, engine_sees):
    backend = recording_backend()
    backend.generate("Olá", language=picked)
    assert backend.calls[-1]["language"] == engine_sees


def test_native_batch_engines_receive_base_languages(recording_backend):
    class Batching(recording_backend):
        def generate_batch(self, texts, **kw):  # type: ignore[override]
            self.calls.append(kw)
            return []

    backend = Batching()
    backend.generate_batch(["a", "b"], language=["Portuguese (Portugal)", "Portuguese (Brazil)"])
    assert backend.calls[-1]["language"] == ["Portuguese", "Portuguese"]


def test_kokoro_resolves_portuguese_variants(monkeypatch):
    import sys
    import types

    from services import tts_backend

    pipeline = types.ModuleType("mlx_audio.tts.models.kokoro.pipeline")
    pipeline.ALIASES = {"pt": "p", "pt-br": "p"}
    pipeline.LANG_CODES = {"p": "pt-br"}
    monkeypatch.setitem(sys.modules, "mlx_audio.tts.models.kokoro.pipeline", pipeline)
    for picked in ("Portuguese (Portugal)", "Portuguese (Brazil)", "pt-PT", "pt-BR", "Portuguese"):
        assert tts_backend.resolve_kokoro_lang_code(picked) == "p"


def test_finite_engine_accepts_both_variants(recording_backend):
    backend = recording_backend(["en", "pt"])
    for picked in ("Portuguese (Brazil)", "Portuguese (Portugal)", "Portuguese", "pt"):
        assert backend._check_language(picked) == "pt"
    with pytest.raises(ValueError, match="doesn't support"):
        recording_backend(["en"])._check_language("Portuguese (Portugal)")


def test_language_options_list_variants_only_with_portuguese(monkeypatch):
    from services import tts_backend
    options = tts_backend.language_options("omnivoice")
    assert {"portuguese", "portuguese (brazil)", "portuguese (portugal)"} <= set(options)
    english_only = tts_backend.language_options("kittentts")
    assert "portuguese (brazil)" not in english_only


def test_bundled_engines_render_no_regional_accent():
    from services import tts_backend
    assert tts_backend.rendered_dialects("omnivoice") == []
    assert tts_backend.rendered_dialects("no-such-engine") == []


# ── text normalization ──────────────────────────────────────────────────────


@pytest.mark.parametrize(("language", "sixteen"), [
    ("Portuguese (Brazil)", "dezesseis"),
    ("Portuguese (Portugal)", "dezasseis"),
    ("pt-PT", "dezasseis"),
    ("pt-BR", "dezesseis"),  # was European before #3143
    ("Portuguese", "dezesseis"),
    ("pt", "dezesseis"),
])
def test_number_words_follow_the_dialect(language, sixteen):
    from services.text_normalization import normalize_text
    assert normalize_text("Tenho 16 anos, 50%", language) == f"Tenho {sixteen} anos, cinquenta por cento"


def test_pronunciation_scopes_resolve_variants_to_portuguese():
    from services.pronunciation import language_scope_key
    assert language_scope_key("Portuguese (Portugal)") == "pt"
    assert language_scope_key("Portuguese (Brazil)") == "pt"


# ── dub translation ─────────────────────────────────────────────────────────


def _request(**fields):
    from schemas.requests import TranslateRequest
    return TranslateRequest(segments=[], **fields)


def test_plain_portuguese_target_means_brazilian():
    from api.routers.dub_translate import _resolve_target_dialect
    req = _request(target_lang="pt")
    _resolve_target_dialect(req)
    assert (req.target_lang, req.dialect) == ("pt", "pt-BR")


def test_regional_target_becomes_base_plus_dialect():
    from api.routers.dub_translate import _resolve_target_dialect
    req = _request(target_lang="pt-PT")
    _resolve_target_dialect(req)
    assert (req.target_lang, req.dialect) == ("pt", "pt-PT")
    other = _request(target_lang="es")
    _resolve_target_dialect(other)
    assert other.dialect is None


def test_regional_segment_targets_become_base_codes():
    from api.routers.dub_translate import _resolve_target_dialect
    from schemas.requests import TranslateRequest

    req = TranslateRequest(
        segments=[{"id": "s1", "text": "Hi", "target_lang": "pt-PT"},
                  {"id": "s2", "text": "Hi", "target_lang": "es-AR"}],
        target_lang="pt",
    )
    _resolve_target_dialect(req)
    assert [seg.target_lang for seg in req.segments] == ["pt", "es-AR"]


def test_resolver_accepts_request_objects_without_a_dialect_field():
    from types import SimpleNamespace

    from api.routers.dub_translate import _resolve_target_dialect

    req = SimpleNamespace(target_lang="pt-PT", segments=[])
    _resolve_target_dialect(req)
    assert (req.target_lang, req.dialect) == ("pt", "pt-PT")


def test_mt_engine_reports_the_variant_it_wrote():
    """NLLB's por_Latn is Brazilian: pt-BR counts as applied, pt-PT names pt-BR."""
    from api.routers.dub_translate import _dialect_flags
    assert _dialect_flags(_request(target_lang="pt", dialect="pt-BR"), applied=False) == {
        "dialect": "pt-BR", "dialect_applied": True,
    }
    assert _dialect_flags(_request(target_lang="pt", dialect="pt-PT"), applied=False) == {
        "dialect": "pt-PT", "dialect_applied": False, "dialect_used": "pt-BR",
    }
    # An LLM that honoured it says so plainly.
    assert _dialect_flags(_request(target_lang="pt", dialect="pt-PT"), applied=True) == {
        "dialect": "pt-PT", "dialect_applied": True,
    }


def test_fast_mt_translation_to_european_portuguese_says_brazilian_was_used():
    from api.routers import dub_translate

    async def run():
        req = _request(target_lang="pt", quality="fast", dialect="pt-PT")
        return await dub_translate._maybe_cinematic([], req, "en", asyncio.get_running_loop())

    result = asyncio.run(run())
    assert result["dialect_applied"] is False
    assert result["dialect_used"] == "pt-BR"
