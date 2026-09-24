"""Unit tests for dub_translate — no network, pure helpers + monkeypatched translator."""
import asyncio
from unittest.mock import AsyncMock

import pytest


def test_translate_codes_cover_popular_iso():
    from api.routers.dub_translate import TRANSLATE_CODES
    popular = ['en', 'es', 'fr', 'de', 'it', 'pt', 'ru', 'zh', 'ja', 'ko', 'ar', 'hi']
    for code in popular:
        assert code in TRANSLATE_CODES, f"{code} missing from TRANSLATE_CODES"


@pytest.mark.parametrize("raw,expected", [
    # Already-normal ISO 639-1 codes pass through.
    ("zh", "zh"),
    ("en", "en"),
    # BCP-47 tags with a region/script suffix strip to the base language.
    ("zh-CN", "zh"),
    ("cmn-Hans", "zh"),
    # Human / display names from the dub UI's own label list.
    ("Chinese", "zh"),
    ("Chinese (Simplified)", "zh"),
    ("Mandarin", "zh"),
    # Three-letter ISO 639-2 / bibliographic codes used by some asset pipelines.
    ("zho", "zh"),
    # Legacy / deprecated ISO 639-1 codes still seen in older corpora.
    ("in", "id"),  # Indonesian: pre-1989 code 'in' → modern 'id'.
    ("iw", "he"),  # Hebrew: pre-1989 code 'iw' → modern 'he'.
    # ISO 639-2/T for Tagalog, frequently shipped under the 'fil' label.
    ("fil", "tl"),
    # Leading / trailing whitespace is stripped before lookup, mirroring how
    # the function tolerates the trailing space some clients append.
    ("  zh  ", "zh"),
    ("\ncmn-Hans\t", "zh"),
])
def test_argos_lang_code_normalizes_names_and_bcp47(raw, expected):
    from services.translation_engines import argos_lang_code
    assert argos_lang_code(raw) == expected


@pytest.mark.parametrize("raw", ["", " ", "\t\n", "Chinese ("])
def test_argos_lang_code_rejects_invalid_inputs(raw):
    from services.translation_engines import argos_lang_code
    # Two distinct input shapes both fail user-facing validation:
    #   - empty / whitespace-only -> nothing left after strip() -> regex rejects
    #   - "Chinese ("             -> looks like a label, not a code -> regex rejects
    # In both cases the caller should see the actionable error message instead
    # of a silently-empty language token that would later produce a confusing
    # "no Argos package available for en -> " failure (the bug #2140 reports).
    with pytest.raises(ValueError, match="Choose a valid source and target language"):
        argos_lang_code(raw)


def test_flores_codes_cover_core_languages():
    from api.routers.dub_translate import FLORES_CODES
    for code in ('en', 'de', 'es', 'fr', 'hi', 'ja'):
        assert code in FLORES_CODES


@pytest.mark.parametrize('code,expected', [
    ('zh-TW', 'zho_Hant'), ('cmn-Hant', 'zho_Hant'), ('bn', 'ben_Beng'),
    ('tam', 'tam_Taml'), ('zho_Hant', 'zho_Hant'), ('xx', None), ('kas', None),
])
def test_nllb_language_resolution(code, expected):
    from api.routers.dub_translate import _nllb_language
    assert _nllb_language(code) == expected


def test_nllb_batching_scales_with_large_cuda_memory(monkeypatch):
    from api.routers import dub_translate
    import torch

    monkeypatch.delenv("OMNIVOICE_NLLB_BATCH_SIZE", raising=False)
    monkeypatch.setattr(dub_translate, "_nllb_device", "cuda")
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (20 * 1024**3, 24 * 1024**3))

    assert dub_translate._nllb_batch_size() == 24
    assert dub_translate._nllb_hypothesis_budget() == 64


def test_nllb_stays_warm_only_with_safe_cuda_headroom(monkeypatch):
    from api.routers import dub_translate
    import torch

    monkeypatch.delenv("OMNIVOICE_UNLOAD_NLLB", raising=False)
    monkeypatch.setattr(dub_translate, "_nllb_device", "cuda")
    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (18 * 1024**3, 24 * 1024**3))
    assert dub_translate._should_unload_nllb() is False

    monkeypatch.setattr(torch.cuda, "mem_get_info", lambda: (6 * 1024**3, 24 * 1024**3))
    assert dub_translate._should_unload_nllb() is True

    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "1")
    assert dub_translate._should_unload_nllb() is True


@pytest.mark.asyncio
async def test_nllb_rejects_unsupported_segment_before_loading(monkeypatch):
    from api.routers.dub_translate import dub_translate
    from schemas.requests import TranslateRequest
    from services import translation_engines
    monkeypatch.setattr(translation_engines, 'is_installed', lambda _: True)
    monkeypatch.setattr(translation_engines, 'is_ready', lambda _: True)
    request = TranslateRequest(provider='nllb', source_lang='en', target_lang='de',
        segments=[{'id': '1', 'text': 'Hello', 'target_lang': 'unsupported'}])
    response = await dub_translate(request)
    assert response.status_code == 400
    assert b'unsupported_translation_language' in response.body


@pytest.mark.asyncio
async def test_nllb_batches_segments_by_target_language(monkeypatch):
    """NLLB pays one forward pass per batch while preserving row order/targets."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest
    from services import translation_engines

    class FakeTokenizer:
        src_lang = None

        def __call__(self, texts, **kwargs):
            assert kwargs["padding"] is True
            return {"input_ids": list(texts)}

        def convert_tokens_to_ids(self, target):
            return target

        def batch_decode(self, tokens, **kwargs):
            return list(tokens)

    class FakeModel:
        def __init__(self):
            self.calls = []

        def generate(self, *, input_ids, forced_bos_token_id, **kwargs):
            self.calls.append((list(input_ids), forced_bos_token_id))
            return [f"{forced_bos_token_id}:{text}" for text in input_ids]

    model = FakeModel()
    monkeypatch.setattr(dub_translate, "_nllb_tokenizer", FakeTokenizer())
    monkeypatch.setattr(dub_translate, "_nllb_model", model)
    monkeypatch.setattr(dub_translate, "_nllb_device", "cpu")
    monkeypatch.setattr(translation_engines, "is_installed", lambda _: True)
    monkeypatch.setattr(translation_engines, "is_ready", lambda _: True)
    monkeypatch.setenv("OMNIVOICE_NLLB_BATCH_SIZE", "2")
    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "0")

    request = TranslateRequest(
        provider="nllb",
        source_lang="en",
        target_lang="de",
        quality="fast",
        segments=[
            {"id": "1", "text": "one"},
            {"id": "2", "text": "two"},
            {"id": "3", "text": "tres", "target_lang": "es"},
            {"id": "4", "text": "four"},
            {"id": "5", "text": "five"},
        ],
    )

    response = await dub_translate.dub_translate(request)

    assert len(model.calls) == 3  # two German batches + one Spanish batch
    assert [row["id"] for row in response["translated"]] == ["1", "2", "3", "4", "5"]
    assert response["translated"][2]["text"].startswith("spa_Latn:")


@pytest.mark.asyncio
async def test_nllb_mps_fallback_releases_the_old_allocator(monkeypatch):
    from unittest.mock import Mock

    import torch
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest
    from services import model_manager, translation_engines

    class Tensor:
        def __init__(self, values):
            self.values = values

        def to(self, device):
            return self

    class Tokenizer:
        def __call__(self, texts, **kwargs):
            return {"input_ids": Tensor(texts)}

        def convert_tokens_to_ids(self, target):
            return target

        def batch_decode(self, tokens, **kwargs):
            return tokens

    class Model:
        def __init__(self):
            self.device = "mps"

        def to(self, device):
            self.device = device
            return self

        def generate(self, *, input_ids, **kwargs):
            if self.device == "mps":
                raise RuntimeError("MPS allocation failed")
            return [f"translated:{text}" for text in input_ids.values]

    flushed = Mock()
    monkeypatch.setattr(model_manager, "release_device_cache", flushed)
    monkeypatch.setattr(dub_translate, "_nllb_tokenizer", Tokenizer())
    monkeypatch.setattr(dub_translate, "_nllb_model", Model())
    monkeypatch.setattr(dub_translate, "_nllb_device", "mps")
    monkeypatch.setattr(torch.cuda, "is_available", lambda: False)
    monkeypatch.setattr(torch.backends.mps, "is_available", lambda: True)
    monkeypatch.setattr(translation_engines, "is_installed", lambda _: True)
    monkeypatch.setattr(translation_engines, "is_ready", lambda _: True)
    monkeypatch.setenv("OMNIVOICE_UNLOAD_NLLB", "0")

    response = await dub_translate.dub_translate(
        TranslateRequest(
            provider="nllb", source_lang="en", target_lang="de",
            segments=[{"id": "1", "text": "hello"}],
        )
    )
    assert response["translated"][0]["text"] == "translated:hello"
    assert dub_translate._nllb_device == "cpu"
    flushed.assert_called_once_with(device="mps")

def test_resolve_source_lang_priority(monkeypatch):
    from api.routers import dub_translate

    class Req:
        def __init__(self, src=None, jid=None):
            self.source_lang = src
            self.job_id = jid

    # Explicit source_lang wins
    assert dub_translate._resolve_source_lang(Req(src='fr')) == 'fr'

    # Fall through to job-detected source_lang
    monkeypatch.setattr(
        dub_translate, '_get_job',
        lambda jid: {'source_lang': 'de'} if jid == 'j1' else None,
    )
    assert dub_translate._resolve_source_lang(Req(jid='j1')) == 'de'

    # No job, no explicit → default en
    assert dub_translate._resolve_source_lang(Req()) == 'en'
    assert dub_translate._resolve_source_lang(Req(jid='missing')) == 'en'


class _FakeSeg:
    def __init__(self, sid, text, target_lang=None):
        self.id = sid
        self.text = text
        self.target_lang = target_lang


class _FakeReq:
    def __init__(self, segments, target_lang, provider='google', source_lang=None):
        self.segments = segments
        self.target_lang = target_lang
        self.provider = provider
        self.source_lang = source_lang
        self.job_id = None


@pytest.mark.asyncio
async def test_google_path_passes_correct_target_code(monkeypatch):
    """GoogleTranslator constructed with the expected src/tgt codes for German."""
    from api.routers import dub_translate

    calls = []

    class FakeTranslator:
        def __init__(self, source=None, target=None, **kwargs):
            calls.append({'source': source, 'target': target})
        def translate(self, text):
            return f"[{calls[-1]['target']}]{text}"

    class FakeModule:
        GoogleTranslator = FakeTranslator
        DeepL = FakeTranslator
        MyMemoryTranslator = FakeTranslator
        MicrosoftTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', 'Hello'), _FakeSeg('s2', 'World')],
        target_lang='de',
        provider='google',
        source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    assert resp['target_lang'] == 'de'
    assert resp['source_lang'] == 'en'
    texts = {t['id']: t['text'] for t in resp['translated']}
    assert texts['s1'] == '[de]Hello'
    assert texts['s2'] == '[de]World'
    # Each segment built a translator with de as target
    assert all(c['target'] == 'de' for c in calls)
    # Source came through as "en"
    assert any(c['source'] == 'en' for c in calls)


@pytest.mark.asyncio
async def test_google_path_uses_seg_target_lang_override(monkeypatch):
    from api.routers import dub_translate

    class FakeTranslator:
        def __init__(self, source=None, target=None, **kwargs):
            self.target = target
        def translate(self, text):
            return f"[{self.target}]{text}"

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[
            _FakeSeg('s1', 'Hi', target_lang='bn'),  # per-segment override
            _FakeSeg('s2', 'Ok'),
        ],
        target_lang='de',
        provider='google',
        source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    texts = {t['id']: t['text'] for t in resp['translated']}
    assert texts['s1'] == '[bn]Hi'
    assert texts['s2'] == '[de]Ok'


@pytest.mark.asyncio
async def test_google_retries_then_falls_back_to_auto(monkeypatch):
    """Transient failure → retry → still fails → fall back to auto source."""
    from api.routers import dub_translate

    attempts = []

    class FakeTranslator:
        def __init__(self, source=None, target=None, **kwargs):
            self.source = source
            self.target = target
        def translate(self, text):
            attempts.append(self.source)
            if self.source != 'auto':
                raise RuntimeError('transient google error')
            return f"[auto:{self.target}]{text}"

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', 'Hello')],
        target_lang='de', provider='google', source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    assert resp['translated'][0]['text'] == '[auto:de]Hello'
    assert 'error' not in resp['translated'][0]
    # explicit src tried at least once before auto
    assert attempts[0] == 'en'
    assert attempts[-1] == 'auto'


@pytest.mark.asyncio
async def test_google_reports_error_when_all_attempts_fail(monkeypatch):
    from api.routers import dub_translate

    class FakeTranslator:
        def __init__(self, **kwargs): pass
        def translate(self, text): raise RuntimeError('total failure')

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', 'Hello')],
        target_lang='de', provider='google', source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    seg = resp['translated'][0]
    assert seg['text'] == 'Hello', 'original text preserved on failure'
    assert 'error' in seg
    assert 'total failure' in seg['error']


@pytest.mark.asyncio
async def test_empty_text_skipped(monkeypatch):
    from api.routers import dub_translate

    class FakeTranslator:
        def __init__(self, **kwargs): pass
        def translate(self, text): return '[xx]' + text

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', '  '), _FakeSeg('s2', 'hi')],
        target_lang='de', provider='google', source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    texts = {t['id']: t['text'] for t in resp['translated']}
    assert texts['s1'].strip() == ''  # untouched
    assert texts['s2'] == '[xx]hi'


@pytest.mark.asyncio
async def test_empty_translation_preserves_original(monkeypatch):
    from api.routers import dub_translate

    class FakeTranslator:
        def __init__(self, **kwargs): pass
        def translate(self, text): return ''  # always empty

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', 'hi')],
        target_lang='de', provider='google', source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    seg = resp['translated'][0]
    assert seg['text'] == 'hi'
    assert 'error' in seg


@pytest.mark.asyncio
async def test_google_rejects_http_200_error_page(monkeypatch):
    """Provider error HTML must never replace the user's transcript."""
    from api.routers import dub_translate

    attempts = 0

    class FakeTranslator:
        def __init__(self, **kwargs):
            pass

        def translate(self, text):
            nonlocal attempts
            attempts += 1
            return (
                "Error 500 (Server Error)!! That's an error. "
                "There was an error. Please try again later."
            )

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(__import__('sys').modules, 'deep_translator', FakeModule)

    req = _FakeReq(
        segments=[_FakeSeg('s1', 'Keep this transcript')],
        target_lang='de', provider='google', source_lang='en',
    )
    resp = await dub_translate.dub_translate(req)
    seg = resp['translated'][0]
    assert attempts == 3
    assert seg['text'] == 'Keep this transcript'
    assert seg['error'] == 'translation provider returned invalid output'


# ── P0: Cinematic/Autofit must run on the non-deep_translator engines ────────
# Before this fix the argos/nllb/openai branches returned BEFORE
# _maybe_cinematic, so picking Cinematic/Autofit on the DEFAULT Argos engine
# silently produced plain Fast output (no quality_used/refine/rate badges).


def _install_fake_argos(monkeypatch):
    """Register a fake `argostranslate` package that translates en→es to
    `[es]<text>` with a pre-installed package, so the argos branch runs offline."""
    import sys
    import types

    class _Pkg:
        from_code = "en"
        to_code = "es"

    pkg = types.ModuleType("argostranslate.package")
    pkg.get_installed_packages = lambda: [_Pkg()]
    pkg.update_package_index = lambda: None
    pkg.get_available_packages = lambda: []
    pkg.install_from_path = lambda p: None
    tr = types.ModuleType("argostranslate.translate")
    tr.translate = lambda text, frm, to: f"[{to}]{text}"
    root = types.ModuleType("argostranslate")
    root.package = pkg
    root.translate = tr
    monkeypatch.setitem(sys.modules, "argostranslate", root)
    monkeypatch.setitem(sys.modules, "argostranslate.package", pkg)
    monkeypatch.setitem(sys.modules, "argostranslate.translate", tr)


@pytest.mark.asyncio
async def test_argos_cinematic_refines_with_llm(monkeypatch):
    """DEFAULT engine + Cinematic + a usable LLM → refine actually runs and the
    response carries quality_used=='cinematic' plus the literal/critique fields."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    _install_fake_argos(monkeypatch)

    async def fake_refine_many(pairs, **kw):
        return [
            {"id": sid, "text": f"CINE:{lit}", "literal": lit, "critique": "crit"}
            for sid, _src, lit in pairs
        ]

    monkeypatch.setattr(dub_translate, "cinematic_available", lambda: True)
    monkeypatch.setattr(dub_translate, "cinematic_refine_many", fake_refine_many)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="argos", source_lang="en", quality="cinematic",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["quality_used"] == "cinematic"
    row = resp["translated"][0]
    assert row["literal"] == "[es]Hello"      # the argos literal is preserved
    assert row["text"] == "CINE:[es]Hello"    # and it was actually refined
    assert row["critique"] == "crit"


@pytest.mark.asyncio
async def test_argos_cinematic_skipped_without_llm(monkeypatch):
    """DEFAULT engine + Cinematic + NO LLM → degrades to Fast with an explicit
    cinematic_skipped flag (not a silent success)."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    _install_fake_argos(monkeypatch)
    monkeypatch.setattr(dub_translate, "cinematic_available", lambda: False)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="argos", source_lang="en", quality="cinematic",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["cinematic_skipped"] == "no-llm-configured"
    assert resp["quality_used"] == "fast"
    assert resp["translated"][0]["text"] == "[es]Hello"  # literal kept


@pytest.mark.asyncio
async def test_argos_fast_stamps_rate_ratio(monkeypatch):
    """Fast on the DEFAULT engine still reaches the rate-ratio stamping so the
    UI's seg-rate-badge has data (it used to return before _maybe_cinematic)."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    _install_fake_argos(monkeypatch)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello", slot_seconds=2.0)],
        target_lang="es", provider="argos", source_lang="en", quality="fast",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["quality_used"] == "fast"
    assert "rate_ratio" in resp["translated"][0]


def _install_fake_openai(monkeypatch, *, content="hola mundo", raises=None):
    """Register a fake `openai.OpenAI` whose chat.completions.create returns
    `content` (or raises `raises`). Accepts the max_retries kwarg the code adds.

    Also sets TRANSLATE_API_KEY so provider="openai" requests deterministically
    take the legacy env-fallback branch — since the provider-wiring fix, a fully
    unconfigured LLM engine 400s up front instead of reaching a client (the
    skills-resolved path has its own tests below). Returns the recorded
    chat.completions.create kwargs for call-shape assertions."""
    import sys
    import types

    calls = []

    class _Completions:
        def create(self, **kw):
            calls.append(kw)
            if raises is not None:
                raise raises
            msg = type("M", (), {"content": content})
            choice = type("C", (), {"message": msg})
            return type("R", (), {"choices": [choice]})

    class _Chat:
        completions = _Completions()

    class _FakeClient:
        def __init__(self, **kw):
            pass
        chat = _Chat()

    mod = types.ModuleType("openai")
    mod.OpenAI = _FakeClient
    monkeypatch.setitem(sys.modules, "openai", mod)
    monkeypatch.setenv("TRANSLATE_API_KEY", "sk-test-env")
    return calls


# ── P1: the Autofit fit pass must be bounded by the cinematic wall-clock ─────


@pytest.mark.asyncio
async def test_agent_fit_forwards_exact_render_measurements(monkeypatch):
    from api.routers import dub_translate
    from schemas.requests import AgentFitRequest, AgentFitSegment
    from services import llm_skills, speech_rate
    from types import SimpleNamespace

    seen = []

    async def _fit_many(items, **_kwargs):
        seen.extend(items)
        return {
            "s1": {
                "text": "Shorter line.",
                "changed": True,
                "measured_seconds": 3.2,
                "target_seconds": 2.0,
                "measured_ratio": 1.6,
            }
        }

    monkeypatch.setattr(speech_rate, "adjust_for_measured_slot_many", _fit_many)
    monkeypatch.setattr(
        llm_skills,
        "resolve_skill",
        lambda _skill_id: SimpleNamespace(ready=True, reason=None),
    )
    result = await dub_translate.dub_agent_fit(
        AgentFitRequest(
            target_lang="en",
            segments=[
                AgentFitSegment(
                    id="s1",
                    text="A line that rendered too long.",
                    source_text="A line that rendered too long.",
                    slot_seconds=2.0,
                    measured_seconds=3.2,
                )
            ],
        )
    )

    assert seen[0][2:4] == (2.0, 3.2)
    assert result["segments"][0]["text"] == "Shorter line."
    assert result["segments"][0]["changed"] is True


@pytest.mark.asyncio
async def test_agent_fit_rejects_an_unavailable_slot_fitting_skill(monkeypatch):
    from api.routers import dub_translate
    from fastapi import HTTPException
    from schemas.requests import AgentFitRequest, AgentFitSegment
    from services import llm_skills, speech_rate
    from types import SimpleNamespace

    fit = AsyncMock()
    monkeypatch.setattr(speech_rate, "adjust_for_measured_slot_many", fit)
    monkeypatch.setattr(
        llm_skills,
        "resolve_skill",
        lambda _skill_id: SimpleNamespace(ready=False, reason="no_provider"),
    )

    with pytest.raises(HTTPException) as caught:
        await dub_translate.dub_agent_fit(
            AgentFitRequest(
                target_lang="es",
                segments=[
                    AgentFitSegment(
                        id="s1",
                        text="Una línea larga.",
                        source_text="A long line.",
                        slot_seconds=1.0,
                        measured_seconds=2.0,
                    )
                ],
            )
        )

    assert caught.value.status_code == 409
    assert caught.value.detail == {
        "error": "llm_skill_unavailable",
        "skill": "slot_fitting",
        "reason": "no_provider",
    }
    fit.assert_not_awaited()


@pytest.mark.asyncio
async def test_openai_autofit_fit_pass_is_budget_bounded(monkeypatch):
    """A slow fit LLM must not spin one adjust_for_slot per segment unbounded:
    the whole translate returns within the budget and unfinished segments
    degrade to their literal (fit-budget) instead of hanging."""
    import time
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    from services import speech_rate
    _install_fake_openai(monkeypatch, content="hola mundo")

    monkeypatch.setenv("OMNIVOICE_CINEMATIC_BUDGET_S", "0.3")

    def _slow_fit(text, *, slot_seconds, target_lang, source_text=None, strict=False):
        time.sleep(3.0)  # far over the 0.3s budget
        return {"text": "FIT-SHOULD-NOT-WIN", "rate_ratio": 1.0}

    monkeypatch.setattr(speech_rate, "adjust_for_slot", _slow_fit)

    req = TranslateRequest(
        segments=[
            TranslateSegment(id="s1", text="Hello", slot_seconds=1.0),
            TranslateSegment(id="s2", text="World", slot_seconds=1.0),
        ],
        target_lang="es", provider="openai", source_lang="en", quality="autofit",
    )
    t0 = time.time()
    resp = await dub_translate.dub_translate(req)
    dt = time.time() - t0
    assert dt < 2.0, f"fit pass not budget-bounded (took {dt:.1f}s)"
    assert resp["quality_used"] == "autofit"
    for row in resp["translated"]:
        assert row["text"] == "hola mundo"          # degraded to literal, not the slow fit
        assert row.get("rate_error") == "fit-budget"


# ── P2: provider errors on the translate path must be scrubbed ──────────────


@pytest.mark.asyncio
async def test_openai_segment_error_is_scrubbed(monkeypatch):
    """An OpenAI-compatible provider that echoes a key / home path in its error
    body must not leak it into the per-segment error response."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    boom = RuntimeError(
        "401 invalid key sk-LEAKLEAKLEAKLEAKLEAK12345 for user at /Users/bob/proj"
    )
    _install_fake_openai(monkeypatch, raises=boom)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="openai", source_lang="en",
    )
    resp = await dub_translate.dub_translate(req)
    seg = resp["translated"][0]
    assert seg["text"] == "Hello"                 # fell back to source text
    assert "sk-LEAKLEAKLEAKLEAKLEAK12345" not in seg["error"]
    assert "/Users/bob" not in seg["error"]
    assert "***REDACTED***" in seg["error"]


# ── provider="openai" resolves through the LLM Providers/Skills system ───────
# The engine used to read only the TRANSLATE_* env vars, so a provider the user
# configured + tested in Settings → LLM Providers silently didn't power it.


class _RecordingLLMClient:
    """Minimal OpenAI-compatible fake that records create() kwargs."""

    def __init__(self, content):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                msg = type("M", (), {"content": content})
                choice = type("C", (), {"message": msg})
                return type("R", (), {"choices": [choice]})

        self.chat = type("Chat", (), {"completions": _Completions()})()


@pytest.mark.asyncio
async def test_openai_uses_provider_configured_in_settings(monkeypatch):
    """A ready dub_translation skill (Settings → LLM Providers) powers the
    engine — its client, its model, its timeout — with no env vars set."""
    import types
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    from services import llm_skills, translation_engines

    for var in ("TRANSLATE_API_KEY", "TRANSLATE_BASE_URL", "TRANSLATE_MODEL"):
        monkeypatch.delenv(var, raising=False)

    fake = _RecordingLLMClient("hallo welt")
    handle = types.SimpleNamespace(
        client=fake, model="provider-model", provider_id="groq", timeout=7.0)
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: handle)
    monkeypatch.setattr(translation_engines, "is_ready", lambda provider: True)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="de", provider="openai", source_lang="en",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["translated"][0]["text"] == "hallo welt"
    assert fake.calls, "the skills-resolved client was not used"
    assert fake.calls[0]["model"] == "provider-model"
    assert fake.calls[0]["timeout"] == 7.0


@pytest.mark.asyncio
async def test_openai_unconfigured_400_names_llm_providers(monkeypatch):
    """Nothing configured anywhere → an up-front actionable 400 pointing at
    Settings → LLM Providers, not a raw per-segment 401."""
    import types
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    from services import llm_skills, translation_engines

    for var in ("TRANSLATE_API_KEY", "TRANSLATE_BASE_URL", "TRANSLATE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: None)
    monkeypatch.setattr(
        llm_skills, "resolve_skill",
        lambda sid: types.SimpleNamespace(reason="no_provider"))
    # Isolate the branch's actionable response from the registry preflight;
    # registry readiness has its own contract tests.
    monkeypatch.setattr(translation_engines, "is_ready", lambda provider: True)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="openai", source_lang="en",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp.status_code == 400
    assert b"LLM Providers" in resp.body


@pytest.mark.asyncio
async def test_openai_disabled_skill_400_names_llm_skills(monkeypatch):
    """A deliberately disabled dub_translation skill names the Skills page."""
    import types
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    from services import llm_skills, translation_engines

    for var in ("TRANSLATE_API_KEY", "TRANSLATE_BASE_URL", "TRANSLATE_MODEL"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: None)
    monkeypatch.setattr(
        llm_skills, "resolve_skill",
        lambda sid: types.SimpleNamespace(reason="disabled"))
    monkeypatch.setattr(translation_engines, "is_ready", lambda provider: True)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="openai", source_lang="en",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp.status_code == 400
    assert b"LLM Skills" in resp.body


@pytest.mark.asyncio
async def test_openai_env_fallback_still_works(monkeypatch):
    """Legacy env-only setups (no provider in the app) keep working unchanged,
    including TRANSLATE_MODEL selection."""
    from api.routers import dub_translate
    from schemas.requests import TranslateRequest, TranslateSegment
    from services import llm_skills

    calls = _install_fake_openai(monkeypatch, content="hola mundo")
    monkeypatch.setenv("TRANSLATE_MODEL", "env-model")
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: None)

    req = TranslateRequest(
        segments=[TranslateSegment(id="s1", text="Hello")],
        target_lang="es", provider="openai", source_lang="en",
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["translated"][0]["text"] == "hola mundo"
    assert calls and calls[0]["model"] == "env-model"

@pytest.mark.asyncio
async def test_argos_native_loader_error_does_not_expose_paths(monkeypatch):
    import builtins
    from core import execstack
    from api.routers.dub_translate import dub_translate
    from schemas.requests import TranslateRequest
    _install_fake_argos(monkeypatch)
    private = '/home/private-user/secrets/native/library.so'
    monkeypatch.setattr(execstack, 'ensure_ctranslate2_loadable', lambda: (False, private))
    original = builtins.__import__
    def fail_native(name, *args, **kwargs):
        if name == 'argostranslate.translate':
            raise OSError(private)
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, '__import__', fail_native)
    response = await dub_translate(TranslateRequest(provider='argos', source_lang='en', target_lang='es', segments=[{'id':'1','text':'Hello'}]))
    assert response.status_code == 400
    assert private.encode() not in response.body
    assert b'CTranslate2' in response.body and b'reinstall' in response.body
    import json
    assert json.loads(response.body)['detail']['code'] == 'argos_runtime_unavailable'


@pytest.mark.parametrize("raw", ["Chinese (Traditional)", "zh-TW", "zh-Hant", "cmn-Hant", "zho_Hant", "zh-HK", "zh-MO"])
def test_argos_does_not_silently_change_chinese_script(raw):
    from services.translation_engines import argos_lang_code
    with pytest.raises(ValueError, match="Traditional Chinese.*NLLB"):
        argos_lang_code(raw)


@pytest.mark.parametrize("raw", ["zh_CN", "cmn_Hans", "zho_CN"])
def test_argos_underscore_locales(raw):
    from services.translation_engines import argos_lang_code
    assert argos_lang_code(raw) == "zh"


@pytest.mark.asyncio
async def test_argos_batch_retry_reports_unsupported_script(tmp_path, monkeypatch):
    from api.routers import batch
    from services import asr_backend, translation_engines
    from fastapi import HTTPException
    source = tmp_path / "source.mp4"
    source.touch()
    monkeypatch.setattr(batch, "_jobs", {"retry-script": {
        "status": "failed", "video_path": str(source), "source_lang": "en",
        "langs": ["zh-TW"], "translation_provider": "argos",
    }})
    monkeypatch.setattr(batch, "_batch_voice", lambda value: None)
    monkeypatch.setattr(asr_backend, "asr_model_missing_error", lambda: None)
    monkeypatch.setattr(translation_engines, "is_ready", lambda provider: True)
    def packs(source, targets):
        for target in targets:
            translation_engines.argos_lang_code(target)
    monkeypatch.setattr(translation_engines, "argos_pack_status", packs)
    with pytest.raises(HTTPException) as err:
        await batch.retry_batch_job("retry-script")
    assert err.value.status_code == 422
    assert "NLLB" in err.value.detail
