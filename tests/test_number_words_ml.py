"""Behaviour checks for the Malayalam number verbaliser."""
from __future__ import annotations

import pytest

from services import number_words_ml as ml

REFERENCE = [
    (0, "പൂജ്യം"),
    (1, "ഒന്ന്"),
    (9, "ഒൻപത്"),
    (10, "പത്ത്"),
    (11, "പതിനൊന്ന്"),
    (19, "പത്തൊൻപത്"),
    (20, "ഇരുപത്"),
    (21, "ഇരുപത്തിയൊന്ന്"),
    (23, "ഇരുപത്തിമൂന്ന്"),
    (25, "ഇരുപത്തിയഞ്ച്"),
    (28, "ഇരുപത്തിയെട്ട്"),
    (29, "ഇരുപത്തിയൊൻപത്"),
    (30, "മുപ്പത്"),
    (45, "നാൽപത്തിയഞ്ച്"),
    (99, "തൊണ്ണൂറ്റിയൊൻപത്"),
    (100, "നൂറ്"),
    (101, "നൂറ്റിയൊന്ന്"),
    (105, "നൂറ്റിയഞ്ച്"),
    (110, "നൂറ്റിപ്പത്ത്"),
    (125, "നൂറ്റിയിരുപത്തിയഞ്ച്"),
    (199, "നൂറ്റിത്തൊണ്ണൂറ്റിയൊൻപത്"),
    (200, "ഇരുനൂറ്"),
    (305, "മുന്നൂറ്റിയഞ്ച്"),
    (999, "തൊള്ളായിരത്തിത്തൊണ്ണൂറ്റിയൊൻപത്"),
    (1000, "ആയിരം"),
    (1001, "ആയിരത്തി ഒന്ന്"),
    (1100, "ആയിരത്തി നൂറ്"),
    (1999, "ആയിരത്തി തൊള്ളായിരത്തിത്തൊണ്ണൂറ്റിയൊൻപത്"),
    (2000, "രണ്ടായിരം"),
    (3000, "മൂവായിരം"),
    (5000, "അയ്യായിരം"),
    (5001, "അയ്യായിരത്തി ഒന്ന്"),
    (7000, "ഏഴായിരം"),
    (10000, "പതിനായിരം"),
    (12000, "പന്ത്രണ്ടായിരം"),
    (19000, "പത്തൊൻപതിനായിരം"),
    (20000, "ഇരുപതിനായിരം"),
    (21000, "ഇരുപത്തിയൊന്നായിരം"),
    (23000, "ഇരുപത്തിമൂവായിരം"),
    (25000, "ഇരുപത്തയ്യായിരം"),
    (28000, "ഇരുപത്തിയെട്ടായിരം"),
    (30000, "മുപ്പതിനായിരം"),
    (45678, "നാൽപത്തയ്യായിരത്തി അറുനൂറ്റിയെഴുപത്തിയെട്ട്"),
    (99999, "തൊണ്ണൂറ്റിയൊൻപതിനായിരത്തി തൊള്ളായിരത്തിത്തൊണ്ണൂറ്റിയൊൻപത്"),
    (100000, "ഒരു ലക്ഷം"),
    (100001, "ഒരു ലക്ഷത്തി ഒന്ന്"),
    (101000, "ഒരു ലക്ഷത്തി ആയിരം"),
    (150000, "ഒരു ലക്ഷത്തി അൻപതിനായിരം"),
    (200000, "രണ്ട് ലക്ഷം"),
    (250500, "രണ്ട് ലക്ഷത്തി അൻപതിനായിരത്തി അഞ്ഞൂറ്"),
    (999999, "ഒൻപത് ലക്ഷത്തി തൊണ്ണൂറ്റിയൊൻപതിനായിരത്തി തൊള്ളായിരത്തിത്തൊണ്ണൂറ്റിയൊൻപത്"),
]


@pytest.mark.parametrize(("n", "expected"), REFERENCE)
def test_cardinal_reference(n: int, expected: str) -> None:
    assert ml.cardinal(n) == expected


@pytest.mark.parametrize("bad", [True, False, 1.0, "5", None])
def test_cardinal_rejects_non_int(bad: object) -> None:
    with pytest.raises(TypeError):
        ml.cardinal(bad)  # type: ignore[arg-type]


@pytest.mark.parametrize("bad", [-1, 1_000_000, 10**9])
def test_cardinal_rejects_out_of_range(bad: int) -> None:
    with pytest.raises(ValueError):
        ml.cardinal(bad)


def test_decimal_reads_each_digit() -> None:
    assert ml.decimal(3, "05") == "മൂന്ന് ദശാംശം പൂജ്യം അഞ്ച്"


def test_percent_with_fraction() -> None:
    assert ml.percent("12.5") == "പന്ത്രണ്ട് ദശാംശം അഞ്ച് ശതമാനം"


def test_percent_whole() -> None:
    assert ml.percent("40") == "നാൽപത് ശതമാനം"


def test_output_is_digit_free_and_single_spaced() -> None:
    for n in range(0, 1_000_000, 997):
        text = ml.cardinal(n)
        assert not any(ch.isdigit() for ch in text)
        assert "  " not in text and text == text.strip()
        assert "\u200c" not in text and "\u200d" not in text
