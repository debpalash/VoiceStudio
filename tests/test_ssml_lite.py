"""SSML-LITE inline markup parser (PR 8).

Pure tests: every tag type, nesting, unclosed-to-EOL, plain text, mixed input,
stray closes, and the spell_out helper. No torch, no I/O, no main import.
"""
from __future__ import annotations

from services.ssml_lite import (
    EMPHASIS_SPEED,
    FAST_SPEED,
    SLOW_SPEED,
    parse_ssml_lite,
    spell_out,
)


def test_plain_text_single_segment():
    segs = parse_ssml_lite("just a plain line")
    assert segs == [
        {"text": "just a plain line", "speed": None, "spell": False, "emphasis": False}
    ]


def test_empty_and_none():
    assert parse_ssml_lite("") == []
    assert parse_ssml_lite(None) == []


def test_slow_tag():
    segs = parse_ssml_lite("a [slow]b[/slow] c")
    assert [s["text"] for s in segs] == ["a ", "b", " c"]
    assert segs[0]["speed"] is None
    assert segs[1]["speed"] == SLOW_SPEED
    assert segs[2]["speed"] is None


def test_fast_tag():
    segs = parse_ssml_lite("[fast]zoom[/fast]")
    assert len(segs) == 1
    assert segs[0]["text"] == "zoom"
    assert segs[0]["speed"] == FAST_SPEED
    assert segs[0]["spell"] is False


def test_emphasis_tag_sets_speed_and_flag():
    segs = parse_ssml_lite("[emphasis]wow[/emphasis]")
    assert segs[0]["speed"] == EMPHASIS_SPEED
    assert segs[0]["emphasis"] is True
    assert segs[0]["spell"] is False


def test_spell_tag_sets_flag_not_speed():
    segs = parse_ssml_lite("call [spell]NASA[/spell] now")
    middle = segs[1]
    assert middle["text"] == "NASA"
    assert middle["spell"] is True
    assert middle["speed"] is None


def test_nesting_innermost_speed_wins():
    # [fast] nested inside [slow] -> fast wins inside the inner run.
    segs = parse_ssml_lite("[slow]a[fast]b[/fast]c[/slow]")
    by_text = {s["text"]: s for s in segs}
    assert by_text["a"]["speed"] == SLOW_SPEED
    assert by_text["b"]["speed"] == FAST_SPEED
    assert by_text["c"]["speed"] == SLOW_SPEED


def test_nesting_spell_inside_slow_keeps_both():
    segs = parse_ssml_lite("[slow]x[spell]Y[/spell]z[/slow]")
    by_text = {s["text"]: s for s in segs}
    assert by_text["Y"]["speed"] == SLOW_SPEED  # outer slow still applies
    assert by_text["Y"]["spell"] is True
    assert by_text["x"]["spell"] is False
    assert by_text["z"]["spell"] is False


def test_unclosed_applies_to_eol():
    segs = parse_ssml_lite("start [slow]rest of line")
    assert segs[0]["text"] == "start "
    assert segs[0]["speed"] is None
    assert segs[1]["text"] == "rest of line"
    assert segs[1]["speed"] == SLOW_SPEED


def test_unclosed_nested_both_to_eol():
    segs = parse_ssml_lite("[slow]a[spell]b")
    by_text = {s["text"]: s for s in segs}
    assert by_text["a"]["speed"] == SLOW_SPEED and by_text["a"]["spell"] is False
    assert by_text["b"]["speed"] == SLOW_SPEED and by_text["b"]["spell"] is True


def test_stray_close_ignored():
    segs = parse_ssml_lite("hello[/slow]world")
    # Markers stripped; the two plain runs merge into one segment.
    assert segs == [
        {"text": "helloworld", "speed": None, "spell": False, "emphasis": False}
    ]


def test_only_markers_yields_no_segments():
    assert parse_ssml_lite("[slow][/slow]") == []


def test_mixed_tags_in_one_line():
    segs = parse_ssml_lite("Say [slow]hi[/slow] then [fast]bye[/fast]!")
    texts = [s["text"] for s in segs]
    assert texts == ["Say ", "hi", " then ", "bye", "!"]
    assert segs[1]["speed"] == SLOW_SPEED
    assert segs[3]["speed"] == FAST_SPEED
    assert segs[0]["speed"] is None and segs[4]["speed"] is None


def test_adjacent_plain_runs_merge():
    # Open+immediate-close around nothing, surrounded by text -> single seg.
    segs = parse_ssml_lite("foo[slow][/slow]bar")
    assert segs == [
        {"text": "foobar", "speed": None, "spell": False, "emphasis": False}
    ]


def test_case_insensitive_tags():
    segs = parse_ssml_lite("[SLOW]x[/Slow]")
    assert segs[0]["speed"] == SLOW_SPEED


def test_spell_out_basic():
    assert spell_out("USA") == "U S A"
    assert spell_out("a") == "a"


def test_spell_out_strips_and_joins_whitespace():
    assert spell_out("  hi  ") == "h i"
    assert spell_out("go USA") == "g o U S A"
    assert spell_out("") == ""


def test_spell_out_keeps_combining_marks_with_their_base():
    # NFD input (e + combining acute) must spell as "c a f é", with the
    # combining mark kept on the base character rather than spoken alone.
    # Mirrors the JS-side fix in 55f3d58 (electron/src/shared/ssmlLite).
    import unicodedata

    nfd = unicodedata.normalize("NFD", "café")
    assert spell_out(nfd) == "c a f é"
    assert spell_out(unicodedata.normalize("NFD", "Zürich")) == "Z ü r i c h"


def test_spell_out_collapses_nfd_hangul_jamo_to_one_unit():
    # Hangul syllables in NFD are 2-3 conjoining jamo per syllable; NFC
    # normalization collapses each syllable to one code point, which is the
    # unit we want to space.
    import unicodedata

    composed = "한글"
    jamo = unicodedata.normalize("NFD", composed)
    assert spell_out(composed) == "한 글"
    assert spell_out(jamo) == "한 글"


def test_spell_out_handles_precomposed_unicode_letters():
    # Each precomposed accented letter is a single code point; this is the
    # case the old code already handled and the new code must not regress.
    assert spell_out("naïve") == "n a ï v e"
    assert spell_out("piñata") == "p i ñ a t a"


def test_spell_out_keeps_devanagari_vowel_sign_with_its_base():
    # Devanagari vowel signs are category Mc (spacing combining mark) and
    # report unicodedata.combining() == 0, so a check on combining() alone
    # would split the base consonant from its vowel sign. The category
    # check (Mn/Mc/Me) keeps the pair together.
    assert spell_out("का") == "का"  # k + aa vowel sign
    # हिंदी = ह (Lo) + ि (Mc) + ं (Mn) + द (Lo) + ी (Mc)
    # → two graphemes: हिं and दी
    assert spell_out("हिंदी") == "हिं दी"


def test_spell_out_long_combining_run_is_linear_time():
    # A run of combining marks must stay O(n): an 8x longer input costs ~8x,
    # while repeated ``units[-1] += ch`` costs ~64x. The 24x limit leaves wide
    # margin both ways, so shared-runner noise cannot fail a linear build.
    import time

    def best(n):
        payload = "a" + "े" * n
        runs = []
        for _ in range(5):
            t0 = time.perf_counter()
            out = spell_out(payload)
            runs.append(time.perf_counter() - t0)
        assert out == payload  # one base, all marks attached: a single unit
        return min(runs)

    small, large = best(50_000), best(400_000)
    assert large < max(small, 0.005) * 24, (small, large)


def test_spell_out_does_not_fuse_separate_tokens():
    # Whitespace separates tokens: two lone conjoining jamo stay two units.
    assert spell_out("\u1100 \u1161") == "\u1100 \u1161"


def test_spell_out_keeps_flag_emoji_whole():
    from services.ssml_lite import spell_out

    # Two regional indicators form one flag; four form two flags.
    assert spell_out("\U0001f1fa\U0001f1f8") == "\U0001f1fa\U0001f1f8"
    assert (
        spell_out("\U0001f1fa\U0001f1f8\U0001f1f0\U0001f1f7")
        == "\U0001f1fa\U0001f1f8 \U0001f1f0\U0001f1f7"
    )


def test_spell_out_keeps_zwj_and_skin_tone_emoji_whole():
    from services.ssml_lite import spell_out

    technologist = "\U0001f469\u200d\U0001f4bb"  # woman + ZWJ + laptop
    thumbs_up = "\U0001f44d\U0001f3fd"  # thumbs up + medium skin tone
    assert spell_out(f"a{technologist}{thumbs_up}") == f"a {technologist} {thumbs_up}"


def test_spell_out_keeps_subdivision_flag_whole():
    from services.ssml_lite import spell_out

    # Black flag + TAG "gbsct" + CANCEL TAG is the Scotland flag.
    scotland = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
    assert spell_out(f"a{scotland}b") == f"a {scotland} b"


def test_spell_out_splits_letters_after_a_zwj():
    from services.ssml_lite import spell_out

    # A ZWJ between letters stays on the first letter; the next letter is
    # still its own unit, and a flag after a letter + ZWJ keeps its pair.
    assert spell_out("\u0645\u200d\u06cc") == "\u0645\u200d \u06cc"
    assert spell_out("a\u200db") == "a\u200d b"
    assert spell_out("A\u200d\U0001f1fa\U0001f1f8") == "A\u200d \U0001f1fa\U0001f1f8"


def test_redos_safe_on_adversarial_input():
    # Many bracket-like fragments must not blow up (linear-time guarantee).
    import time

    payload = "[slow]" * 5000 + "x"
    t0 = time.perf_counter()
    segs = parse_ssml_lite(payload)
    assert time.perf_counter() - t0 < 1.0
    # All 5000 opens unclosed -> the single 'x' run is slow.
    assert segs[-1]["text"] == "x"
    assert segs[-1]["speed"] == SLOW_SPEED


def test_spell_out_long_zwj_emoji_run_is_linear_time():
    # A long ZWJ emoji sequence stays one unit and O(n): copying the unit on
    # every ZWJ cost ~64x for an 8x longer input; the 24x limit leaves margin.
    import time

    from services.ssml_lite import spell_out

    def best(n):
        payload = "\U0001f469" + "‍\U0001f4bb" * n
        runs = []
        for _ in range(5):
            t0 = time.perf_counter()
            out = spell_out(payload)
            runs.append(time.perf_counter() - t0)
        assert out == payload  # a single grapheme unit, nothing spaced out
        return min(runs)

    small, large = best(5_000), best(40_000)
    assert large < max(small, 0.005) * 24, (small, large)


def test_zwj_joins_only_after_a_pictograph_base():
    from services.ssml_lite import spell_out

    assert spell_out("\U0001f469\U0001f3fd‍\U0001f4bb") == "\U0001f469\U0001f3fd‍\U0001f4bb"
    assert spell_out("\U0001f3f3️‍\U0001f308") == "\U0001f3f3️‍\U0001f308"
    assert spell_out("a‍\U0001f4bb") == "a‍ \U0001f4bb"
