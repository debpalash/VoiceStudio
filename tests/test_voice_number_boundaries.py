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
