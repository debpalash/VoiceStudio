"""Two-stage translation quality for the LLM dub engine — auto-glossary
(theme + terminology injected into every segment prompt, user glossary wins)
and the reflect pass (critique→rewrite polish that silently falls back to the
direct translation on any failure). No network — the LLM is a scripted fake
resolved through the LLM Skills seam, like the other dub_translate tests."""
import types

import pytest


# ── Fakes ────────────────────────────────────────────────────────────────────


class _ScriptedLLMClient:
    """OpenAI-compatible fake. `script(call_kwargs) -> str | Exception` decides
    each response; every chat.completions.create() call is recorded."""

    def __init__(self, script):
        self.calls = []
        outer = self

        class _Completions:
            def create(self, **kw):
                outer.calls.append(kw)
                out = script(kw)
                if isinstance(out, Exception):
                    raise out
                msg = type("M", (), {"content": out})
                choice = type("C", (), {"message": msg})
                return type("R", (), {"choices": [choice]})

        self.chat = type("Chat", (), {"completions": _Completions()})()

    def system_of(self, call):
        return call["messages"][0]["content"]

    def user_of(self, call):
        return call["messages"][1]["content"]


def _is_context_call(client, call):
    return "translation brief" in client.system_of(call)


def _is_review_call(client, call):
    return "script reviewer" in client.system_of(call)


def _is_polish_call(client, call):
    return "script writer" in client.system_of(call)


def _is_direct_call(client, call):
    return "professional dubbing translator" in client.system_of(call)


def _wire_skill_client(monkeypatch, client, *, model="test-model", timeout=9.0):
    from services import llm_skills

    handle = types.SimpleNamespace(
        client=client, model=model, provider_id="test", timeout=timeout)
    monkeypatch.setattr(llm_skills, "resolve_skill_client", lambda sid: handle)


def _req(segments, *, target_lang="es", provider="openai", **kw):
    from schemas.requests import TranslateRequest
    return TranslateRequest(
        segments=segments, target_lang=target_lang, provider=provider,
        source_lang="en", **kw,
    )


def _segs(*texts):
    from schemas.requests import TranslateSegment
    return [TranslateSegment(id=f"s{i + 1}", text=t) for i, t in enumerate(texts)]


_CONTEXT_BODY = (
    "THEME: A casual cooking show about regional street food.\n"
    "TERM: Chef Okonkwo || Chef Okonkwo\n"
    "TERM: flat-top grill || plancha\n"
)


# ── Stage 1: extraction — prompt assembly + parsing ─────────────────────────


def test_extract_context_prompt_assembly_and_parse():
    """The single context call carries the FULL transcript + language names,
    and the THEME/TERM response parses into {theme, terms}."""
    from services import translation_quality as tq

    client = _ScriptedLLMClient(lambda kw: _CONTEXT_BODY)
    ctx = tq.extract_context_sync(
        client, "m", 5.0,
        segment_texts=["Welcome back to the show.", "Chef Okonkwo fires up the flat-top grill."],
        source_lang="en", target_lang="es",
        source_name="English", target_name="Spanish",
    )
    assert len(client.calls) == 1
    user = client.user_of(client.calls[0])
    assert "Welcome back to the show." in user
    assert "Chef Okonkwo fires up the flat-top grill." in user
    assert "English" in user and "Spanish" in user
    system = client.system_of(client.calls[0])
    assert "Spanish" in system  # target rendering asked for by name
    assert ctx["theme"] == "A casual cooking show about regional street food."
    assert ctx["terms"] == [
        {"source": "Chef Okonkwo", "target": "Chef Okonkwo"},
        {"source": "flat-top grill", "target": "plancha"},
    ]


def test_extract_context_none_on_failure_or_garbage():
    from services import translation_quality as tq

    boom = _ScriptedLLMClient(lambda kw: RuntimeError("provider down"))
    assert tq.extract_context_sync(
        boom, "m", 5.0, segment_texts=["hi"], source_lang="en", target_lang="es",
    ) is None

    garbage = _ScriptedLLMClient(lambda kw: "sure, here is a translation!")
    assert tq.extract_context_sync(
        garbage, "m", 5.0, segment_texts=["hi"], source_lang="en", target_lang="es",
    ) is None

    # Empty transcript → no call at all
    idle = _ScriptedLLMClient(lambda kw: _CONTEXT_BODY)
    assert tq.extract_context_sync(
        idle, "m", 5.0, segment_texts=["", "  "], source_lang="en", target_lang="es",
    ) is None
    assert idle.calls == []


def test_merge_glossary_user_wins():
    """User entries beat auto entries on the same source (case-insensitive);
    auto extras still ride along; blank entries are dropped."""
    from services.translation_quality import merge_glossary

    user = [
        {"source": "Flat-Top Grill", "target": "parrilla", "note": "house style"},
        {"source": "", "target": "x"},  # invalid — dropped
    ]
    auto = [
        {"source": "flat-top grill", "target": "plancha"},   # loses to user
        {"source": "Chef Okonkwo", "target": "Chef Okonkwo"},  # survives
    ]
    merged = merge_glossary(user, auto)
    assert merged == [
        {"source": "Flat-Top Grill", "target": "parrilla", "note": "house style"},
        {"source": "Chef Okonkwo", "target": "Chef Okonkwo"},
    ]
    # user-only / auto-only / empty all behave
    assert merge_glossary(None, auto) == [
        {"source": "flat-top grill", "target": "plancha"},
        {"source": "Chef Okonkwo", "target": "Chef Okonkwo"},
    ]
    assert merge_glossary(user, None) == [user[0]]
    assert merge_glossary(None, None) == []


# ── Stage 1: injection into every per-segment translation prompt ────────────


@pytest.mark.asyncio
async def test_auto_glossary_injected_into_every_segment_prompt(monkeypatch):
    """One context call up front; every direct-translate prompt then carries
    the theme + the MERGED glossary — the user's target for a clashing source,
    plus the auto-only terms."""
    from api.routers import dub_translate

    def script(kw):
        sys_msg = kw["messages"][0]["content"]
        if "translation brief" in sys_msg:
            return _CONTEXT_BODY
        return "hola"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    req = _req(
        _segs("Fire up the grill.", "Chef Okonkwo tastes it."),
        glossary=[{"source": "flat-top grill", "target": "parrilla", "note": ""}],
        reflect=False,  # isolate stage 1
    )
    resp = await dub_translate.dub_translate(req)
    assert all(r["text"] == "hola" for r in resp["translated"])

    context_calls = [c for c in client.calls if _is_context_call(client, c)]
    direct_calls = [c for c in client.calls if _is_direct_call(client, c)]
    assert len(context_calls) == 1
    assert len(direct_calls) == 2  # one per segment — no other extras
    assert len(client.calls) == 3
    for call in direct_calls:
        sys_msg = client.system_of(call)
        assert "A casual cooking show" in sys_msg           # theme
        assert "flat-top grill → parrilla" in sys_msg        # user term WON
        assert "flat-top grill → plancha" not in sys_msg     # auto clash dropped
        assert "Chef Okonkwo → Chef Okonkwo" in sys_msg      # auto extra kept


@pytest.mark.asyncio
async def test_manual_glossary_still_injected_with_auto_glossary_off(monkeypatch):
    """auto_glossary=False skips the transcript pass (no extra LLM call) but
    the user's manual glossary still rides every segment prompt for free."""
    from api.routers import dub_translate

    client = _ScriptedLLMClient(lambda kw: "hola")
    _wire_skill_client(monkeypatch, client)

    req = _req(
        _segs("Fire up the grill."),
        glossary=[{"source": "grill", "target": "parrilla", "note": ""}],
        auto_glossary=False, reflect=False,
    )
    resp = await dub_translate.dub_translate(req)
    assert resp["translated"][0]["text"] == "hola"
    assert len(client.calls) == 1
    assert "grill → parrilla" in client.system_of(client.calls[0])


@pytest.mark.asyncio
async def test_context_cached_on_job_and_reused(monkeypatch):
    """The extraction result persists on the dub job (job_data blob, no schema
    change) and an unchanged transcript re-translates with ZERO extra context
    calls; an edited transcript re-extracts."""
    from api.routers import dub_translate

    job = {"filename": "clip.mp4"}
    saved = []
    monkeypatch.setattr(dub_translate, "_get_job", lambda jid: job if jid == "j1" else None)
    monkeypatch.setattr(
        dub_translate, "_save_job",
        lambda jid, j, *a, **kw: saved.append((jid, j)),
    )

    def script(kw):
        if "translation brief" in kw["messages"][0]["content"]:
            return _CONTEXT_BODY
        return "hola"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    req = _req(_segs("Fire up the grill."), job_id="j1", reflect=False)
    await dub_translate.dub_translate(req)
    assert saved and saved[0][0] == "j1"
    stored = job["translation_context"]["es"]
    assert stored["theme"].startswith("A casual cooking show")
    assert stored["terms"] and stored["fingerprint"]
    assert len([c for c in client.calls if _is_context_call(client, c)]) == 1

    # Same transcript again → cache hit, still exactly one context call ever.
    await dub_translate.dub_translate(
        _req(_segs("Fire up the grill."), job_id="j1", reflect=False))
    assert len([c for c in client.calls if _is_context_call(client, c)]) == 1

    # Edited transcript → fingerprint miss → re-extract.
    await dub_translate.dub_translate(
        _req(_segs("Fire up the flat-top."), job_id="j1", reflect=False))
    assert len([c for c in client.calls if _is_context_call(client, c)]) == 2


@pytest.mark.asyncio
async def test_context_failure_never_fails_translation(monkeypatch):
    """A context pass that blows up (provider error) degrades silently — the
    per-segment translation still runs and succeeds."""
    from api.routers import dub_translate

    def script(kw):
        if "translation brief" in kw["messages"][0]["content"]:
            return RuntimeError("rate limited")
        return "hola"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(
        _req(_segs("Fire up the grill."), reflect=False))
    row = resp["translated"][0]
    assert row["text"] == "hola"
    assert "error" not in row


# ── Stage 2: reflect pass ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_reflect_polishes_direct_translation(monkeypatch):
    """Happy path: direct → critique → rewrite; the polished line ships and the
    direct translation is kept as `literal` for the 3-column view."""
    from api.routers import dub_translate

    def script(kw):
        sys_msg = kw["messages"][0]["content"]
        if "script reviewer" in sys_msg:
            return "Too stiff for spoken dialogue."
        if "script writer" in sys_msg:
            return "enciende la parrilla ya"
        return "procede a encender la parrilla ahora"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(
        _req(_segs("Fire up the grill now."), auto_glossary=False))
    row = resp["translated"][0]
    assert row["text"] == "enciende la parrilla ya"
    assert row["literal"] == "procede a encender la parrilla ahora"
    assert "error" not in row
    # exactly 3 calls: direct + review + polish
    assert len(client.calls) == 3
    review = [c for c in client.calls if _is_review_call(client, c)][0]
    polish = [c for c in client.calls if _is_polish_call(client, c)][0]
    # the review sees source + draft; the polish additionally sees the critique
    assert "Fire up the grill now." in client.user_of(review)
    assert "procede a encender la parrilla ahora" in client.user_of(review)
    assert "Too stiff for spoken dialogue." in client.user_of(polish)


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_on", ["reviewer", "writer"])
async def test_reflect_falls_back_silently_on_failure(monkeypatch, fail_on):
    """ANY failure in the extra steps (critique or rewrite) keeps the direct
    translation with NO per-segment error — refinement never fails a segment."""
    from api.routers import dub_translate

    def script(kw):
        sys_msg = kw["messages"][0]["content"]
        if f"script {fail_on}" in sys_msg:
            return RuntimeError("timeout")
        if "script reviewer" in sys_msg:
            return "A bit wordy."
        if "script writer" in sys_msg:
            return "should never ship"
        return "hola mundo"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(
        _req(_segs("Hello world."), auto_glossary=False))
    row = resp["translated"][0]
    assert row["text"] == "hola mundo"
    assert "error" not in row


@pytest.mark.asyncio
async def test_reflect_falls_back_on_divergent_rewrite(monkeypatch):
    """A rewrite that diverges from the draft (runaway length — hallucinated
    dialogue / commentary) is refused; the direct translation ships."""
    from api.routers import dub_translate

    runaway = "bla " * 200

    def script(kw):
        sys_msg = kw["messages"][0]["content"]
        if "script reviewer" in sys_msg:
            return "Could be tighter."
        if "script writer" in sys_msg:
            return runaway
        return "hola mundo, esta es una traduccion normal"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(
        _req(_segs("Hello world, this is a normal line."), auto_glossary=False))
    row = resp["translated"][0]
    assert row["text"] == "hola mundo, esta es una traduccion normal"
    assert "error" not in row


def test_reflect_unit_fallbacks():
    """reflect_translation_sync returns None (keep direct) for: empty draft,
    empty rewrite, rewrite == draft, and critique echoed back as the line."""
    from services.translation_quality import reflect_translation_sync

    kw = dict(source_text="Hi.", source_lang="en", target_lang="es")

    # empty draft → no calls at all
    idle = _ScriptedLLMClient(lambda k: "x")
    assert reflect_translation_sync(idle, "m", 5.0, direct_text="  ", **kw) is None
    assert idle.calls == []

    # rewrite identical to the draft → None (nothing to apply)
    same = _ScriptedLLMClient(lambda k: "hola")
    assert reflect_translation_sync(same, "m", 5.0, direct_text="hola", **kw) is None

    # critique echoed back as the "translation" → refused
    def echo(k):
        return "this draft is far too wordy and stiff for dubbing work"
    echoed = _ScriptedLLMClient(echo)
    assert reflect_translation_sync(
        echoed, "m", 5.0, direct_text="hola amigo como estas hoy", **kw) is None


# ── Toggles + MT engines ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_toggles_off_exactly_one_llm_call_per_segment(monkeypatch):
    """auto_glossary=False + reflect=False → the LLM engine behaves exactly as
    before: one call per segment, nothing else."""
    from api.routers import dub_translate

    client = _ScriptedLLMClient(lambda kw: "hola")
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(
        _req(_segs("One.", "Two.", "Three."), auto_glossary=False, reflect=False))
    assert [r["text"] for r in resp["translated"]] == ["hola"] * 3
    assert len(client.calls) == 3
    assert all(_is_direct_call(client, c) for c in client.calls)


@pytest.mark.asyncio
async def test_defaults_are_on_for_llm_engine(monkeypatch):
    """Flags omitted (old clients / fresh UI) → both stages run: 1 context call
    + 3 calls per segment."""
    from api.routers import dub_translate

    def script(kw):
        sys_msg = kw["messages"][0]["content"]
        if "translation brief" in sys_msg:
            return _CONTEXT_BODY
        if "script reviewer" in sys_msg:
            return "Fine but stiff."
        if "script writer" in sys_msg:
            return "hola pulida"
        return "hola directa"

    client = _ScriptedLLMClient(script)
    _wire_skill_client(monkeypatch, client)

    resp = await dub_translate.dub_translate(_req(_segs("Hello there.")))
    row = resp["translated"][0]
    assert row["text"] == "hola pulida"
    assert row["literal"] == "hola directa"
    assert len(client.calls) == 4  # context + direct + review + polish


@pytest.mark.asyncio
async def test_mt_engine_unaffected_by_quality_flags(monkeypatch):
    """MT engines can't run either stage: with both flags forced on, the
    google path neither touches the LLM Skills seam nor changes its output."""
    import sys
    from api.routers import dub_translate
    from services import llm_skills

    def _no_llm(sid):
        raise AssertionError("MT engine must not resolve an LLM client")

    monkeypatch.setattr(llm_skills, "resolve_skill_client", _no_llm)

    class FakeTranslator:
        def __init__(self, source=None, target=None, **kwargs):
            self.target = target

        def translate(self, text):
            return f"[{self.target}]{text}"

    class FakeModule:
        GoogleTranslator = FakeTranslator

    monkeypatch.setitem(sys.modules, "deep_translator", FakeModule)

    resp = await dub_translate.dub_translate(
        _req(_segs("Hello"), auto_glossary=True, reflect=True, provider="google"))
    row = resp["translated"][0]
    assert row["text"] == "[es]Hello"
    assert "literal" not in row  # response shape unchanged for MT engines


@pytest.mark.asyncio
async def test_custom_style_reaches_direct_translation(monkeypatch):
    from api.routers import dub_translate
    client = _ScriptedLLMClient(lambda _: "hola")
    _wire_skill_client(monkeypatch, client)
    req = _req(_segs("Hello there."), auto_glossary=False, reflect=False,
               translation_instructions="Keep it warm and conversational; preserve jokes.")
    await dub_translate.dub_translate(req)
    direct = [c for c in client.calls if _is_direct_call(client, c)]
    assert direct
    assert all("Keep it warm and conversational; preserve jokes." in client.system_of(c) for c in direct)
