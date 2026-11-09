"""Second-pass ASR QC scoring (Wave 3.3 / Spec 5) — pure, no ASR/main import."""

import pytest

from services.dub_qc import score_dub, word_error_rate


# ── word_error_rate ──────────────────────────────────────────────────────────

def test_wer_identical_is_zero():
    assert word_error_rate("hello there world", "hello there world") == 0.0


def test_wer_case_and_punctuation_insensitive():
    assert word_error_rate("Hello, world!", "hello world") == 0.0


def test_wer_one_substitution():
    # 1 edit over 3 reference tokens.
    assert word_error_rate("the cat sat", "the dog sat") == pytest.approx(1 / 3)


def test_wer_empty_reference_with_hypothesis_is_one():
    assert word_error_rate("", "something") == 1.0


def test_wer_both_empty_is_zero():
    assert word_error_rate("", "") == 0.0


def test_wer_completely_different():
    assert word_error_rate("alpha beta", "x y z") >= 1.0


def test_wer_no_space_scripts_score_per_character():
    # ``\w+`` took a whole Chinese, Japanese or Thai clause as one token, so a
    # single wrong character scored as total drift. One codepoint per token.
    assert word_error_rate("你好世界", "你好世晚") == pytest.approx(1 / 4)
    assert word_error_rate("こんにちは世界", "こんにちは世界です") == pytest.approx(2 / 7)
    # Thai vowel marks are not word characters; they count as codepoints too.
    assert word_error_rate("สวัสดีชาวโลก", "สวัสดีชาวโลกครับ") == pytest.approx(4 / 12)


def test_wer_supplementary_ideographs_score_per_character():
    # Extension B ideographs are outside the BMP; a wrong one is one character.
    reference = "\U00020000\U00020001\U00020002\U00020003"
    hypothesis = "\U00020000\U00020001\U00020002\U00020004"
    assert word_error_rate(reference, hypothesis) == pytest.approx(1 / 4)


def test_wer_no_space_script_punctuation_is_stripped():
    # ・ and 。 are punctuation, not characters of the line; so is Thai ๏.
    assert word_error_rate("東京・大阪。", "東京大阪") == pytest.approx(0.0)
    assert word_error_rate("สวัสดี๏", "สวัสดี") == pytest.approx(0.0)
    assert word_error_rate("東京・大阪", "東京・京都") == pytest.approx(2 / 4)


def test_wer_spaced_scripts_keep_word_tokens():
    assert word_error_rate("naïve café", "naïve cafe") == pytest.approx(1 / 2)


def test_wer_canonical_unicode_spellings_score_zero():
    # Composed vs. decomposed accents spell the same word; drift must be 0.
    # See issue #2693 — composed "ï" (U+00EF) vs. decomposed "i" + U+0308
    # should not register as drift; reversed direction also passes.
    import unicodedata

    composed = "naïve café"
    decomposed = unicodedata.normalize("NFD", composed)
    assert word_error_rate(composed, decomposed) == 0.0
    assert word_error_rate(decomposed, composed) == 0.0

    # Hangul pre-composed syllable vs. jamo sequence for the same syllable.
    composed_hangul = "한글"
    jamo_hangul = unicodedata.normalize("NFD", composed_hangul)
    assert word_error_rate(composed_hangul, jamo_hangul) == 0.0
    assert word_error_rate(jamo_hangul, composed_hangul) == 0.0

    # Japanese voiced katakana (ガ U+30AC vs. カ U+30AB + ゛ U+3099).
    composed_kana = "ガラス"
    decomposed_kana = unicodedata.normalize("NFD", composed_kana)
    assert word_error_rate(composed_kana, decomposed_kana) == 0.0


def test_wer_genuine_accent_difference_still_scores_drift():
    # NFC normalization collapses canonical equivalence only, so a real accent
    # change is preserved as drift.
    assert word_error_rate("naïve", "naive") == pytest.approx(1 / 1)


# ── score_dub ────────────────────────────────────────────────────────────────

def _seg(start, end, text, sid=None):
    d = {"start": start, "end": end, "text": text}
    if sid is not None:
        d["id"] = sid
    return d


def test_clean_dub_no_flags():
    dub = [_seg(0, 3, "hello world", "a"), _seg(3, 6, "good morning", "b")]
    recog = [_seg(0.1, 2.9, "hello world"), _seg(3.0, 5.8, "good morning")]
    out = score_dub(dub, recog)
    assert [q.flagged for q in out] == [False, False]
    assert out[0].seg_id == "a"
    assert all(q.drift == 0.0 for q in out)


def test_canonical_unicode_respelling_is_not_drift_in_score_dub():
    import unicodedata

    text = "naïve café"
    recog = [_seg(0, 3, unicodedata.normalize("NFD", text))]
    out = score_dub([_seg(0, 3, text, "a")], recog)
    assert out[0].drift == 0.0
    assert out[0].flagged is False
    assert out[0].target_text == text
    assert out[0].recognized_text == unicodedata.normalize("NFD", text)


def test_drifted_segment_is_flagged():
    dub = [_seg(0, 3, "the quarterly report is ready", "a")]
    # ASR heard something quite different (mispronunciation / bad clone).
    recog = [_seg(0, 3, "the quarter lee deport is read")]
    out = score_dub(dub, recog, drift_threshold=0.5)
    assert out[0].flagged is True
    assert out[0].recognized_text == "the quarter lee deport is read"


def test_one_wrong_character_does_not_flag_a_chinese_line():
    dub = [_seg(0, 3, "今天天气很好，我们去公园吧", "a")]
    recog = [_seg(0, 3, "今天天气很好，我们去公园把")]  # one character off
    out = score_dub(dub, recog, drift_threshold=0.5)
    assert out[0].drift == pytest.approx(1 / 12, abs=1e-3)
    assert out[0].flagged is False


def test_measured_timing_from_recognition():
    dub = [_seg(0.0, 5.0, "hello", "a")]
    recog = [_seg(0.4, 1.2, "hello")]
    out = score_dub(dub, recog)
    assert out[0].new_start == pytest.approx(0.4)
    assert out[0].new_end == pytest.approx(1.2)


def test_segment_with_no_overlap_scores_full_drift():
    dub = [_seg(0, 3, "spoken line", "a")]
    recog = [_seg(10, 12, "elsewhere")]  # no time overlap
    out = score_dub(dub, recog)
    assert out[0].recognized_text == ""
    assert out[0].drift == 1.0 and out[0].flagged
    assert out[0].new_start is None


def test_multiple_recognized_segments_concatenate():
    dub = [_seg(0, 6, "one two three four", "a")]
    recog = [_seg(0, 3, "one two"), _seg(3, 6, "three four")]
    out = score_dub(dub, recog)
    assert out[0].recognized_text == "one two three four"
    assert out[0].drift == 0.0


def test_seg_ids_override():
    dub = [_seg(0, 3, "x")]
    out = score_dub(dub, [_seg(0, 3, "x")], seg_ids=["custom"])
    assert out[0].seg_id == "custom"
