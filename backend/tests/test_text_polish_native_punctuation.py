"""Dictation must not append a Latin stop to native terminal punctuation."""

import importlib

import pytest


@pytest.fixture
def polisher():
    return importlib.import_module("services.text_polish")


@pytest.mark.parametrize("text", [
    "\u0643\u064a\u0641 \u062d\u0627\u0644\u0643\u061f",  # Arabic question
    "\u06cc\u06c1 \u062a\u06be\u06cc\u06a9 \u06c1\u06d2\u06d4",  # Urdu stop
    "\u0928\u092e\u0938\u094d\u0924\u0947\u0964",  # Devanagari danda
    "\u0936\u093e\u0928\u094d\u0924\u093f\u0965",  # double danda
])
def test_native_sentence_terminators_are_already_complete(polisher, text):
    assert polisher.polish_text(text) == text
    assert polisher.polish_text(polisher.polish_text(text)) == text


def test_native_terminator_before_closing_quote_is_complete(polisher):
    text = '\u00ab\u0643\u064a\u0641 \u062d\u0627\u0644\u0643\u061f\u00bb'
    assert polisher.polish_text(text) == text


def test_unterminated_non_latin_text_still_gets_a_stop(polisher):
    text = '\u0645\u0631\u062d\u0628\u0627'
    assert polisher.polish_text(text) == text + '.'
