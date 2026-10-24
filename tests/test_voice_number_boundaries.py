"""Specialized verbalizers must not corrupt identifiers, codes, or ranges."""
import pytest


@pytest.mark.parametrize("text", ["$5abc", "12:34abc", "abc12:34", "3-5%", "+5%", "007%", "$000007", "007.5", "007th", "v12:34", "$5/6", "$5%", "3.5.1%", "1st-2nd", "21st-23rd", "21st/23rd", "21st+23rd", "21st:23rd", "21st.23rd"])
def test_ambiguous_number_forms_stay_unchanged(text):
    from services.text_normalization import normalize_text
    assert normalize_text(text, "English") == text
    assert normalize_text(normalize_text(text, "English"), "English") == text


@pytest.mark.parametrize("text, expected", [("It costs $5.", "It costs five dollars."), ("At 03:04 sharp", "At three oh four sharp"), ("battery at 50%", "battery at fifty percent"), ("battery at 0.5%", "battery at zero point five percent"), ("rated 3.5 stars", "rated three point five stars"), ("the 21st of May", "the twenty-first of May")])
def test_unambiguous_forms_still_verbalize(text, expected):
    from services.text_normalization import normalize_text
    assert normalize_text(text, "English") == expected


@pytest.mark.parametrize("language", ["German", "Malayalam"])
@pytest.mark.parametrize("text", ["3-5%", "007%", "007.5"])
def test_other_verbalizers_keep_the_shared_conservative_boundaries(language, text):
    from services.text_normalization import normalize_text
    assert normalize_text(text, language) == text


@pytest.mark.parametrize("text, expected", [
    ("It costs £3.", "It costs three pounds."),
    ("It costs £5.50 now", "It costs five pounds, fifty pence now"),
    ("only £1.01 left", "only one pound, one penny left"),
    ("It costs €20.", "It costs twenty euros."),
    ("It costs €1.99, please", "It costs one euro, ninety-nine cents, please"),
    ("$5 or £5 or €5", "five dollars or five pounds or five euros"),
])
def test_pound_and_euro_amounts_are_spoken_as_money(text, expected):
    from services.text_normalization import normalize_text
    assert normalize_text(text, "English") == expected


@pytest.mark.parametrize("language, text", [
    ("English", "¥300 and ₹50"),
    ("English", "It costs £1,000."),
    ("English", "It costs €5.5."),
    ("German", "Das kostet €20 oder 20€."),
    ("French", "Il coûte 3€."),
    ("Malayalam", "₹50"),
])
def test_a_number_glued_to_a_currency_symbol_keeps_its_digits(language, text):
    from services.text_normalization import normalize_text
    assert normalize_text(text, language) == text
