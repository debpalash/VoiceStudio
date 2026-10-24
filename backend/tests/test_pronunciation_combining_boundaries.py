"""A pronunciation entry must not match part of a Unicode word/grapheme."""

import importlib

import pytest


@pytest.fixture
def pronunciation():
    return importlib.import_module("services.pronunciation")


def test_unaccented_term_does_not_replace_part_of_decomposed_accent(pronunciation):
    text = 'cafe\u0301 cafe'
    assert pronunciation.apply_lexicon(text, {'cafe': 'coffee'}) == 'cafe\u0301 coffee'


def test_indic_term_ending_in_vowel_mark_remains_whole_word(pronunciation):
    term = '\u0915\u093f'  # ki
    longer = '\u0915\u093f\u0938\u093e\u0928'  # kisan
    assert pronunciation.apply_lexicon(f'{longer} {term}', {term: 'ki'}) == f'{longer} ki'


def test_decomposed_whole_word_can_still_be_replaced(pronunciation):
    term = 'cafe\u0301'
    assert pronunciation.apply_lexicon(f'{term}s {term},', {term: 'coffee'}) == f'{term}s coffee,'


def test_letter_after_combining_mark_is_not_a_new_word(pronunciation):
    assert pronunciation.apply_lexicon('a\u0301b b', {'b': 'bee'}) == 'a\u0301b bee'


def test_nonspacing_script_keys_keep_existing_substring_semantics(pronunciation):
    assert pronunciation.apply_lexicon('\u6211\u5728\u5317\u4eac', {'\u5317\u4eac': 'Beijing'}) == '\u6211\u5728Beijing'


def test_rejected_compound_does_not_consume_later_valid_term(pronunciation):
    assert pronunciation.apply_lexicon(
        'a\u0301cat dog', {'cat dog': 'wrong', 'dog': 'hound'}
    ) == 'a\u0301cat hound'


def test_rejected_compound_allows_shorter_valid_alternative_at_same_start(pronunciation):
    assert pronunciation.apply_lexicon(
        'cat dog\u0301', {'cat dog': 'wrong', 'cat': 'feline'}
    ) == 'feline dog\u0301'


@pytest.mark.parametrize('mark', ['\ufe00', '\U000e0100', '\u0301'])
def test_no_space_base_keeps_substring_matching_with_trailing_marks(pronunciation, mark):
    term = '\u845b' + mark
    assert pronunciation.apply_lexicon(term + '\u98fe', {term: 'katsu'}) == 'katsu\u98fe'


def test_spaced_script_base_with_multiple_marks_keeps_word_boundary(pronunciation):
    term = 'a\u0301\u0323'
    assert pronunciation.apply_lexicon(term + 'b ' + term, {term: 'letter'}) == term + 'b letter'


def test_leading_or_standalone_marks_do_not_match_inside_another_grapheme(pronunciation):
    mark = '\u0301'
    assert pronunciation.apply_lexicon('a' + mark + 'b ' + mark, {mark: 'accent'}) == 'a' + mark + 'b accent'
    term = '\u3099\u304b'  # a leading dakuten is not the base of the following kana
    assert pronunciation.apply_lexicon('\u304b' + term, {term: 'wrong'}) == '\u304b' + term
