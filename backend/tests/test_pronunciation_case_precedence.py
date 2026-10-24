"""Saved entry order and scope priority must survive duplicate case variants."""

import importlib

import pytest


@pytest.fixture
def pronunciation():
    return importlib.import_module("services.pronunciation")


def row(term, replacement, language='*'):
    return {'term': term, 'replacement': replacement, 'type': 'respelling',
            'language': language, 'enabled': 1}


def test_latest_global_entry_wins_after_case_variant(pronunciation):
    entries = [row('GIF', 'first'), row('gif', 'second'), row('GIF', 'latest')]
    assert pronunciation.apply_pronunciation('GIF gif', entries) == 'latest latest'


def test_language_override_wins_over_all_global_case_variants(pronunciation):
    entries = [row('GIF', 'first'), row('gif', 'global'), row('GIF', 'local', 'de')]
    assert pronunciation.apply_pronunciation('GIF gif', entries, 'German') == 'local local'
    assert pronunciation.apply_pronunciation('GIF gif', entries, 'English') == 'global global'


def test_project_overlay_wins_over_preexisting_case_variant(pronunciation):
    entries = [row('GIF', 'first'), row('gif', 'global')]
    assert pronunciation.apply_pronunciation('GIF gif', entries, lexicon={'GIF': 'project'}) == 'project project'


def test_latest_language_entry_wins_after_case_variant(pronunciation):
    entries = [row('GIF', 'first', 'de'), row('gif', 'second', 'de'),
               row('GIF', 'latest', 'de')]
    assert pronunciation.apply_pronunciation('GIF gif', entries, 'de') == 'latest latest'


def test_longest_term_still_wins_over_later_short_term(pronunciation):
    entries = [row('Dr. Smith', 'Doctor Smith'), row('Dr', 'Doctor')]
    assert pronunciation.apply_pronunciation('Dr. Smith and Dr Jones', entries) == 'Doctor Smith and Doctor Jones'
