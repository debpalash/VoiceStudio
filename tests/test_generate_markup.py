"""Generate honours sentence/paragraph gaps and SSML-lite like Audiobook does.

A 1,600-character Chinese narration rendered through Generate ran sentences
and topic changes together (1,490 of ~1,500 word gaps under 80 ms), and
``[emphasis]``/``[slow]`` reached the engine as text. ``/generate`` and the MCP
``generate_speech`` tool now take ``sentence_gap_ms``, ``paragraph_gap_ms`` and
``trim_engine_silence`` (all off by default) and parse SSML-lite tags.
"""
import os

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from omnivoice.utils.text import parse_pause_markers  # noqa: E402
from services.generate_markup import (  # noqa: E402
    MAX_JOIN_SILENCE_MS,
    JoinOptions,
    Span,
    needs_span_render,
    plan_spans,
)

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


def test_chinese_sentences_and_paragraphs_get_their_gaps():
    text = "第一件事很重要。第二件事呢？\n\n第三件事！最后一句。"
    spans = plan_spans(text, JoinOptions(sentence_gap_ms=300, paragraph_gap_ms=900))
    assert [s.pause_ms for s in spans] == [300, 900, 300, 0]
    assert "".join(s.text for s in spans) == text


def test_ascii_periods_inside_numbers_and_urls_do_not_split():
    spans = plan_spans("Pi is 3.14 at example.com today. Next!", JoinOptions(200))
    assert [s.text for s in spans] == ["Pi is 3.14 at example.com today.", " Next!"]


def test_closing_quotes_stay_with_their_sentence():
    spans = plan_spans('他说：“走吧。”然后离开。 He said "Go." Then', JoinOptions(150))
    assert [s.text for s in spans] == ["他说：“走吧。”", "然后离开。", ' He said "Go."', " Then"]


def test_explicit_pause_replaces_the_gap_and_the_end_gets_none():
    spans = plan_spans("One. [pause 1s] Two. Three.", JoinOptions(200))
    assert [(s.text.strip(), s.pause_ms) for s in spans] == [
        ("One.", 1000), ("Two.", 200), ("Three.", 0)]


def test_paragraph_gap_falls_back_to_the_sentence_gap():
    spans = plan_spans("A line\n\nB line", JoinOptions(sentence_gap_ms=250))
    assert [s.pause_ms for s in spans] == [250, 0]


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
    text = "好。" * (MAX_JOIN_SILENCE_MS // 5000 + 2)
    with pytest.raises(ValueError, match="minutes"):
        plan_spans(text, JoinOptions(sentence_gap_ms=5000))


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
                    JoinOptions(sentence_gap_ms=300, paragraph_gap_ms=900), calls)
    assert [t.strip() for t, _ in calls] == ["第一句。", "第二句！", "第三句。"]
    assert audio.shape[-1] == 3 * CHUNK + int(SR * 1.2)


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
    form = _mcp_generate(monkeypatch, sentence_gap_ms=300, paragraph_gap_ms=900,
                         trim_engine_silence=True)
    assert form["sentence_gap_ms"] == "300"
    assert form["paragraph_gap_ms"] == "900"
    assert form["trim_engine_silence"] == "true"


def test_mcp_default_request_is_unchanged(monkeypatch):
    form = _mcp_generate(monkeypatch)
    assert not {"sentence_gap_ms", "paragraph_gap_ms", "trim_engine_silence"} & form.keys()
