"""An author's per-occurrence respelling overrides every dictionary layer."""

import importlib

import pytest


@pytest.fixture
def pronunciation():
    return importlib.import_module("services.pronunciation")


@pytest.mark.parametrize('inline,expected', [
    ('[[GIF|SQL]]', 'SQL'),
    ('[[SQL]]', 'SQL'),
    ('[[GIF|SQL GIF]]', 'SQL GIF'),
])
def test_inline_respelling_is_not_rewritten_by_project_dictionary(pronunciation, inline, expected):
    lexicon = {'GIF': 'jiff', 'SQL': 'sequel'}
    assert pronunciation.apply_pronunciation(f'GIF {inline} SQL', lexicon=lexicon) == f'jiff {expected} sequel'


def test_database_dictionary_cannot_rewrite_inline_replacement(pronunciation):
    entries = [{'term': 'SQL', 'replacement': 'sequel', 'type': 'respelling',
                'language': '*', 'enabled': 1}]
    assert pronunciation.apply_pronunciation('SQL [[label|SQL]]', entries) == 'sequel SQL'


def test_dictionary_phrase_cannot_swallow_an_inline_override(pronunciation):
    lexicon = {'say [[word|chosen]] now': 'wrong', 'now': 'later'}
    assert pronunciation.apply_pronunciation('say [[word|chosen]] now', lexicon=lexicon) == 'say chosen later'


def test_empty_inline_and_ordinary_dictionary_substitution_still_work(pronunciation):
    assert pronunciation.apply_pronunciation('GIF [[]] GIF', lexicon={'GIF': 'jiff'}) == 'jiff  jiff'
