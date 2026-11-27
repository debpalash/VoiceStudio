"""Generate honours paragraph gaps and SSML-lite like Audiobook does.

A 1,600-character Chinese narration rendered through Generate ran topic
changes together, and
``[emphasis]``/``[slow]`` reached the engine as text. ``/generate`` and the MCP
``generate_speech`` tool now take ``paragraph_gap_ms`` and
``trim_engine_silence`` (both off by default) and parse SSML-lite tags.
"""
import os

import pytest
import torch

from test_text_normalization_routes import fake_engine  # noqa: F401

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")



# Application modules are resolved when a test runs, not at import: another
# test may replace them in sys.modules and leave module-level names stale.
def parse_pause_markers(text):
    from omnivoice.utils.text import parse_pause_markers as fn

    return fn(text)


def plan_spans(*args, **kwargs):
    from services.generate_markup import plan_spans as fn

    return fn(*args, **kwargs)


def needs_span_render(*args, **kwargs):
    from services.generate_markup import needs_span_render as fn

    return fn(*args, **kwargs)


def JoinOptions(*args, **kwargs):  # noqa: N802
    from services.generate_markup import JoinOptions as cls

    return cls(*args, **kwargs)


def Span(*args, **kwargs):  # noqa: N802
    from services.generate_markup import Span as cls

    return cls(*args, **kwargs)


def _max_join_silence_ms():
    from services.generate_markup import MAX_JOIN_SILENCE_MS

    return MAX_JOIN_SILENCE_MS


SR = 24000
CHUNK = 2400  # every probe call renders 0.1 s


@pytest.mark.parametrize("text", [
    "Plain text. With two sentences.",
    "第一件事。第二件事！\n\n最后？",
    "Before [pause 500ms] after.",
    "[pause 1s]Leading silence.",
    "",
])
def test_default_options_keep_the_old_plan(text):
    spans = plan_spans(text)
    assert [(s.text, s.pause_ms) for s in spans] == parse_pause_markers(text)
    assert all(s.speed_factor is None for s in spans)


def test_blank_lines_get_the_paragraph_gap_and_sentences_do_not():
    text = "第一件事很重要。第二件事呢？\n\n第三件事！最后一句。\n \n结尾。"
    spans = plan_spans(text, JoinOptions(paragraph_gap_ms=900))
    assert [s.pause_ms for s in spans] == [900, 900, 0]
    assert "".join(s.text for s in spans) == text


def test_explicit_pause_replaces_the_gap_and_the_end_gets_none():
    spans = plan_spans("One.\n\n[pause 1s]Two.\n\nThree.", JoinOptions(paragraph_gap_ms=200))
    assert [(s.text.strip(), s.pause_ms) for s in spans] == [
        ("One.", 1000), ("Two.", 200), ("Three.", 0)]


def test_ssml_lite_tags_become_speed_factors_not_text():
    spans = plan_spans("Say [emphasis]this[/emphasis] and [slow]that[/slow]. [fast]Go[/fast]")
    assert "[" not in "".join(s.text for s in spans)
    factors = {s.text.strip(): s.speed_factor for s in spans}
    assert factors["this"] == pytest.approx(0.92)
    assert factors["that."] == pytest.approx(0.85)  # the "." joins the tagged run
    assert factors["Go"] == pytest.approx(1.15)
    assert needs_span_render(spans)


def test_spell_tag_is_spelled_out():
    spans = plan_spans("Code [spell]AB[/spell]")
    assert spans[-1].text == "A B"


def test_silence_budget_is_enforced():
    text = "好。\n\n" * (_max_join_silence_ms() // 5000 + 2)
    with pytest.raises(ValueError, match="minutes"):
        plan_spans(text, JoinOptions(paragraph_gap_ms=5000))


def test_spell_only_input_is_not_sent_to_the_engine_as_tags():
    text = "[spell]USA[/spell]"
    spans = plan_spans(text)
    assert [s.text for s in spans] == ["U S A"]
    assert needs_span_render(spans, JoinOptions(), text)
    # Plain text keeps the one-shot path.
    assert not needs_span_render(plan_spans("Plain."), JoinOptions(), "Plain.")


def test_paragraph_gap_survives_ssml_lite_boundaries():
    spans = plan_spans("[slow]One.[/slow]\n\nTwo.", JoinOptions(paragraph_gap_ms=700))
    assert [(s.text.strip(), s.pause_ms) for s in spans] == [("One.", 700), ("Two.", 0)]
    assert spans[0].speed_factor == pytest.approx(0.85)
    assert spans[1].speed_factor is None


def test_ssml_scope_stays_open_across_pauses_and_blank_lines():
    spans = plan_spans("[slow]One[pause 500ms]Two[/slow] Three")
    assert [(s.text.strip(), s.pause_ms) for s in spans] == [
        ("One", 500), ("Two", 0), ("Three", 0)]
    assert [s.speed_factor for s in spans][:2] == [pytest.approx(0.85)] * 2
    assert spans[2].speed_factor is None
    spans = plan_spans("[slow]A\n\nB[/slow]", JoinOptions(paragraph_gap_ms=300))
    assert [s.speed_factor for s in spans] == [pytest.approx(0.85)] * 2
    assert spans[0].pause_ms == 300


def test_separately_authored_pauses_are_not_clamped_when_merged():
    spans = plan_spans("[pause 10s] [pause 10s]Hello")
    assert sum(s.pause_ms for s in spans) == 20000
    assert spans[-1].text == "Hello"


def test_many_paragraphs_plan_in_linear_time():
    import time

    def best_of_three(paragraphs: int) -> float:
        timings = []
        for _ in range(3):
            start = time.perf_counter()
            plan_spans("A.\n\n" * paragraphs, JoinOptions(paragraph_gap_ms=1))
            timings.append(time.perf_counter() - start)
        return min(timings)

    # Warm up first: the wrapper imports the planner on first call, and that
    # import is not planning time. Then compare growth, not wall-clock time,
    # so a loaded CI runner cannot fail it: 4x the paragraphs costs ~4x when
    # linear and ~16x when quadratic.
    plan_spans("A.", JoinOptions(paragraph_gap_ms=1))
    small = best_of_three(10000)
    large = best_of_three(40000)
    assert large < max(small, 0.005) * 8


def test_trim_engine_silence_alone_takes_the_span_renderer():
    spans = plan_spans("Plain text.")
    assert not needs_span_render(spans, JoinOptions())
    assert needs_span_render(spans, JoinOptions(trim_engine_silence=True))


def _probe(calls):
    class Probe:
        sample_rate = SR
        supports_generation_seed = True

        def generate(self, text, **kw):
            calls.append((text, kw.get("speed")))
            return torch.ones(1, CHUNK) * 0.1

    return Probe()


def _render(text, joins, calls, speed=1.0):
    from api.routers.generation import _run_backend_inference

    return _run_backend_inference(
        _probe(calls), text, "Chinese", None, None, None, None,
        32, 2, speed, True, True, 1, "raw", 800, 0, joins=joins)


def test_backend_render_inserts_the_gaps(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_PAUSE_FILL", "silence")
    calls = []
    audio = _render("第一句。第二句！\n\n第三句。",
                    JoinOptions(paragraph_gap_ms=900), calls)
    assert [t.strip() for t, _ in calls] == ["第一句。第二句！", "第三句。"]
    assert audio.shape[-1] == 2 * CHUNK + int(SR * 0.9)


def test_trim_keeps_the_requested_duration_for_one_unsplit_span():
    from api.routers.generation import _run_backend_inference

    seen = []

    class Probe:
        sample_rate = SR
        supports_generation_seed = True

        def generate(self, text, **kw):
            seen.append((text, kw.get("duration")))
            return torch.ones(1, CHUNK) * 0.1

    _run_backend_inference(
        Probe(), "Short line.", "English", None, None, None, 5.0,
        32, 2, 1.0, True, True, 1, "raw", 800, 0,
        joins=JoinOptions(trim_engine_silence=True))
    assert seen == [("Short line.", 5.0)]


def test_backend_render_scales_speed_for_tagged_runs():
    calls = []
    _render("Normal [slow]slower[/slow]", None, calls, speed=1.2)
    speeds = {t.strip(): s for t, s in calls}
    assert speeds["Normal"] == pytest.approx(1.2)
    assert speeds["slower"] == pytest.approx(1.2 * 0.85)


def test_backend_render_without_options_is_one_call():
    calls = []
    _render("第一句。第二句！", None, calls)
    assert calls == [("第一句。第二句！", 1.0)]


def test_span_is_a_pause_tuple():
    # _render_with_pauses unpacks (text, pause) from old two-item segments too.
    assert Span("a", 5)[:2] == ("a", 5)


def _mcp_generate(monkeypatch, **arguments):
    import asyncio

    pytest.importorskip("mcp")
    from fastapi import FastAPI, Request, Response
    import mcp_server

    forms = []
    backend = FastAPI()

    @backend.post("/generate")
    async def synthesize(request: Request):
        forms.append(dict(await request.form()))
        return Response(b"WAVE", headers={"X-Audio-Id": "audio42"})

    monkeypatch.delenv("OMNIVOICE_API_URL", raising=False)
    server = mcp_server.create_mcp_server(app=backend)
    asyncio.run(server.call_tool("generate_speech", {"text": "Hi.", **arguments}))
    return forms[0]


def test_mcp_forwards_the_join_options(monkeypatch):
    form = _mcp_generate(monkeypatch, paragraph_gap_ms=900,
                         trim_engine_silence=True)
    assert form["paragraph_gap_ms"] == "900"
    assert form["trim_engine_silence"] == "true"


def test_mcp_default_request_is_unchanged(monkeypatch):
    form = _mcp_generate(monkeypatch)
    assert not {"paragraph_gap_ms", "trim_engine_silence"} & form.keys()


@pytest.mark.parametrize("stream", ["0", "1"])
def test_budget_is_rechecked_after_respellings_as_a_400(fake_engine, monkeypatch, stream):
    import asyncio
    import importlib

    import httpx

    from main import app

    pronunciation = importlib.import_module("services.pronunciation")
    monkeypatch.setattr(pronunciation, "prepare_speech_text",
                        lambda text, *a, **k: "a\n\n" * 200)

    async def run():
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://localhost") as api:
            return await api.post("/generate", data={
                "engine": "fake-norm-route", "text": "short", "language": "English",
                "paragraph_gap_ms": "5000", "stream": stream})

    response = asyncio.run(run())
    assert response.status_code == 400
    assert "minutes" in response.text


def test_nested_tags_over_many_paragraphs_plan_in_linear_time():
    import time

    text = "[slow]" * 400 + "A\n\n" * 20000
    start = time.perf_counter()
    spans = plan_spans(text, JoinOptions(paragraph_gap_ms=1))
    assert time.perf_counter() - start < 3
    assert all(s.speed_factor == pytest.approx(0.85) for s in spans)


def test_too_many_tags_are_refused():
    with pytest.raises(ValueError, match="Too many"):
        plan_spans("[slow]" * 10000 + "A")
