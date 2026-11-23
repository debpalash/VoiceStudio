"""W3C PLS lexicons for the pronunciation dictionary (services.pronunciation_pls)."""
import importlib

import pytest

LEXICON = """<?xml version="1.0" encoding="UTF-8"?>
<lexicon version="1.0" xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"
         alphabet="ipa" xml:lang="en-US">
  <lexeme><grapheme>tomato</grapheme><phoneme>təˈmeɪtoʊ</phoneme></lexeme>
  <lexeme><grapheme>UN</grapheme><grapheme>U.N.</grapheme><alias>United Nations</alias></lexeme>
  <lexeme><grapheme>GIF</grapheme><phoneme alphabet="x-cmu">JH IH1 F</phoneme></lexeme>
  <lexeme><grapheme>Nevada</grapheme><phoneme alphabet="x-sampa">n@"v{d@</phoneme></lexeme>
  <lexeme>
    <grapheme>read</grapheme>
    <phoneme>ɹiːd</phoneme>
    <phoneme prefer="true">ɹɛd</phoneme>
  </lexeme>
</lexicon>
"""


def _pls():
    return importlib.import_module("services.pronunciation_pls")


def test_a_lexicon_maps_onto_dictionary_types():
    parsed = _pls().parse_pls(LEXICON)
    assert parsed["language"] == "en-US"
    assert parsed["skipped"] == 1  # X-SAMPA has no dictionary type
    assert parsed["entries"] == [
        {"term": "tomato", "replacement": "təˈmeɪtoʊ", "type": "ipa"},
        {"term": "UN", "replacement": "United Nations", "type": "respelling"},
        {"term": "U.N.", "replacement": "United Nations", "type": "respelling"},
        {"term": "GIF", "replacement": "JH IH1 F", "type": "cmu"},
        {"term": "read", "replacement": "ɹɛd", "type": "ipa"},
    ]


def test_export_round_trips():
    entries = _pls().parse_pls(LEXICON)["entries"]
    again = _pls().parse_pls(_pls().to_pls(entries, "en"))
    assert again["entries"] == entries and again["language"] == "en"


def test_escaping_survives_the_round_trip():
    entries = [{"term": "R&D <x>", "replacement": "research \"and\" development", "type": "respelling"}]
    assert _pls().parse_pls(_pls().to_pls(entries, "en"))["entries"] == entries


@pytest.mark.parametrize("text", [
    '<?xml version="1.0"?><!DOCTYPE lexicon [<!ENTITY a "aaaa">]><lexicon/>',
    '<lexicon><lexeme><grapheme>&a;</grapheme></lexeme></lexicon><!ENTITY a "x">',
    "not xml",
    "<speak/>",
])
def test_unsafe_or_foreign_documents_are_refused(text):
    with pytest.raises(_pls().PlsError):
        _pls().parse_pls(text)
