"""#3422 — per-line ``[emotion:]`` reaches the engine that takes it, and only it.

The parser carries a span's emotion (golden corpus in
``tests/fixtures/longform_parser_cases.json``); these pin the render side:
every untagged book keeps byte-identical cache keys, a tagged span moves them,
each engine kind applies the emotion through its own mechanism, and a synth
without emotion support is still called exactly as before.
"""
import importlib

import pytest

from services.audiobook import Span, parse_audiobook_script, synthesize_chapter
from services.expression import apply_span_emotion
from services.longform_render import chapter_cache_key, segment_cache_key


def _router():
    return importlib.import_module("api.routers.audiobook")


def test_untagged_keys_are_byte_identical():
    """An untagged span hashes exactly as it did before the feature."""
    plain = [("v", "Hello.", 0, None), ("v", "Bye.", 0, None, "continue")]
    router = _router()
    rebuilt = [s[:4] + router._span_key_extras(Span(*s[:4], join=(s[4] if len(s) > 4 else None)))
               for s in plain]
    assert chapter_cache_key(rebuilt, sample_rate=24000, engine_id="e") == chapter_cache_key(
        plain, sample_rate=24000, engine_id="e")
    assert segment_cache_key("Hello.", sample_rate=24000, engine_id="e", emotion=None) == (
        segment_cache_key("Hello.", sample_rate=24000, engine_id="e"))


def test_emotion_moves_both_cache_keys():
    """A changed emotion must re-render: it changes the audio."""
    base = segment_cache_key("Hello.", sample_rate=24000, engine_id="e")
    sad = segment_cache_key("Hello.", sample_rate=24000, engine_id="e", emotion="sad")
    happy = segment_cache_key("Hello.", sample_rate=24000, engine_id="e", emotion="happy")
    assert len({base, sad, happy}) == 3
    chapter = lambda extra: chapter_cache_key(  # noqa: E731
        [("v", "Hello.", 0, None) + extra], sample_rate=24000, engine_id="e")
    assert chapter(()) != chapter(({"emotion": "sad"},))


def test_emotion_never_hashes_like_a_join():
    """An emotion that reads "continue" must not collide with a join of "continue"."""
    join = chapter_cache_key([("v", "x", 0, None, "continue")], sample_rate=1, engine_id="e")
    emotion = chapter_cache_key([("v", "x", 0, None, {"emotion": "continue"})],
                                sample_rate=1, engine_id="e")
    assert join != emotion


@pytest.mark.parametrize(
    "control, attrs",
    [("graded", {"supports_emotion": True}),
     ("direction", {"instruct_directs_delivery": True}),
     ("none", {})],
)
def test_each_engine_kind_applies_emotion_its_own_way(control, attrs):
    """Graded → emo_text, direction → instruct, none → untouched."""
    engine = type("Engine", (), attrs)
    book = {"emo_vector": [0.5] + [0.0] * 7, "emo_text": "book-level calm"}
    call_extra = dict(book)
    instruct = apply_span_emotion(engine, call_extra, "warm", "quietly terrified")
    if control == "graded":
        assert call_extra["emo_text"] == "quietly terrified"
        assert call_extra["use_emo_text"] is True
        assert "emo_vector" not in call_extra  # the span replaces book-level emotion
        assert call_extra["emo_alpha"] == 0.6
        assert instruct == "warm"
    elif control == "direction":
        assert instruct == "warm, quietly terrified"
        assert call_extra == book
    else:
        assert instruct == "warm"
        assert call_extra == book


def test_graded_emotion_keeps_the_books_strength():
    engine = type("Engine", (), {"supports_emotion": True})
    call_extra: dict = {}
    apply_span_emotion(engine, call_extra, None, "sad", emo_alpha=0.3)
    assert call_extra["emo_alpha"] == 0.3


def test_direction_without_a_voice_instruct_sends_only_the_emotion():
    engine = type("Engine", (), {"instruct_directs_delivery": True})
    assert apply_span_emotion(engine, {}, None, "sad") == "sad"
    assert apply_span_emotion(engine, {}, "  ", "sad") == "sad"


def test_synth_gets_emotion_only_for_tagged_spans():
    """A synth that predates emotion keeps working for untagged books."""
    torch = pytest.importorskip("torch")
    calls = []

    def synth(text, voice_id, speed=None, **kw):
        calls.append((text, kw.get("emotion", "<absent>")))
        return torch.ones(10)

    plan = parse_audiobook_script("Calm. [emotion:sad] Sad. [emotion:] Calm again.",
                                  default_voice="v")
    synthesize_chapter(plan.chapters[0].spans, synth, 16000)
    assert calls == [("Calm.", "<absent>"), ("Sad.", "sad"), ("Calm again.", "<absent>")]

    legacy_calls = []
    plain = parse_audiobook_script("Plain line.", default_voice="v")
    synthesize_chapter(plain.chapters[0].spans,
                       lambda t, v, s=None: legacy_calls.append(t) or torch.ones(10), 16000)
    assert legacy_calls == ["Plain line."]


def test_span_round_trips_through_the_resume_manifest():
    span = Span(voice_id="v", text="Sad.", join="continue", emotion="sad")
    assert Span(**span.to_dict()) == span
    assert "emotion" not in Span(voice_id="v", text="Plain.").to_dict()


def test_stories_request_accepts_and_bounds_emotion():
    """Stories posts pre-built spans; the schema carries emotion, capped like the parser."""
    from pydantic import ValidationError

    model = _router().LongformSpan
    assert model(text="Sad.", emotion="sad").emotion == "sad"
    assert model(text="Plain.").emotion is None
    with pytest.raises(ValidationError):
        model(text="x", emotion="a" * 201)
