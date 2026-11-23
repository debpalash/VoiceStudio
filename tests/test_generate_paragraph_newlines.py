"""Generate should identify paragraphs independently of the script's line endings."""

import pytest


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_paragraph_spacing_preserves_text_and_counts_one_boundary(newline):
    from services.generate_markup import JoinOptions, plan_spans

    text = f"First line.{newline}Still first paragraph.{newline * 4}Second paragraph."
    spans = plan_spans(text, JoinOptions(paragraph_gap_ms=300))
    assert [span.pause_ms for span in spans] == [300, 0]
    assert "".join(span.text for span in spans) == text
