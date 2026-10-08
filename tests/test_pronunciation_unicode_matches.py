"""Every literal matched by the lexicon must use that entry's replacement."""
import pytest


@pytest.mark.parametrize('term,text', [('GIF', 'gıf'), ('index', 'İNDEX'), ('seek', 'ſeek'), ('kelvin', 'KELVIN')])
def test_regex_case_matches_use_the_matched_entry(term, text):
    from services.pronunciation import apply_lexicon

    assert apply_lexicon('Say ' + text + '.', {term: 'replacement'}) == 'Say replacement.'


def test_distinct_literals_do_not_share_a_casefold_replacement():
    from services.pronunciation import apply_lexicon

    assert apply_lexicon('Straße STRASSE', {'Straße': 'street', 'STRASSE': 'avenue'}) == 'street avenue'
    assert apply_lexicon('Dr. Smith Dr.', {'Dr.': 'Doctor', 'Dr. Smith': 'Professor Smith'}) == 'Professor Smith Doctor'


def test_case_variants_keep_last_entry_precedence():
    from services.pronunciation import apply_lexicon

    assert apply_lexicon('GIF gif', {'GIF': 'global', 'gif': 'local'}) == 'local local'
    assert apply_lexicon('GIF gif', {'gif': 'local', 'GIF': 'global'}) == 'global global'


def test_language_specific_case_variant_overrides_global():
    from services.pronunciation import apply_lexicon

    from services.pronunciation import entries_for_language

    entries = [
        {'term': 'GIF', 'replacement': 'global', 'type': 'respelling', 'language': '*', 'enabled': 1},
        {'term': 'gif', 'replacement': 'local', 'type': 'respelling', 'language': 'en', 'enabled': 1},
    ]
    assert apply_lexicon('GIF gif', entries_for_language(entries, 'en-US')) == 'local local'
