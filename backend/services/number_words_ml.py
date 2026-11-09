"""Malayalam number words (cardinals, decimals, percentages).

``num2words`` has no Malayalam (``ml``) locale, so without this module
Malayalam text would reach the TTS engine with raw digits. The rendering here
is pure Python with no dependencies and is deliberately narrow, matching the
conservative scope of :mod:`services.text_normalization`: integers up to six
digits (Indian grouping with ``laksham``; crore-scale is out), decimals read
digit by digit, and percentages.

Output is Malayalam script only, words separated by single ASCII spaces, in
modern atomic-chillu spelling, with no digits and no joiner characters.
"""
from __future__ import annotations

_ZERO = "പൂജ്യം"
_UNITS = ["", "ഒന്ന്", "രണ്ട്", "മൂന്ന്", "നാല്", "അഞ്ച്", "ആറ്", "ഏഴ്", "എട്ട്", "ഒൻപത്"]
_TEENS = [
    "പത്ത്", "പതിനൊന്ന്", "പന്ത്രണ്ട്", "പതിമൂന്ന്", "പതിനാല്",
    "പതിനഞ്ച്", "പതിനാറ്", "പതിനേഴ്", "പതിനെട്ട്", "പത്തൊൻപത്",
]
# Indexed by the tens digit (2..9); entries 0 and 1 are unused.
_TENS = ["", "", "ഇരുപത്", "മുപ്പത്", "നാൽപത്", "അൻപത്", "അറുപത്", "എഴുപത്", "എൺപത്", "തൊണ്ണൂറ്"]
_TENS_PREFIX = ["", "", "ഇരുപത്തി", "മുപ്പത്തി", "നാൽപത്തി", "അൻപത്തി", "അറുപത്തി", "എഴുപത്തി", "എൺപത്തി", "തൊണ്ണൂറ്റി"]
# Indexed by the hundreds digit (1..9).
_HUNDREDS = ["", "നൂറ്", "ഇരുനൂറ്", "മുന്നൂറ്", "നാനൂറ്", "അഞ്ഞൂറ്", "അറുനൂറ്", "എഴുനൂറ്", "എണ്ണൂറ്", "തൊള്ളായിരം"]
_HUNDREDS_PREFIX = [
    "", "നൂറ്റി", "ഇരുനൂറ്റി", "മുന്നൂറ്റി", "നാനൂറ്റി", "അഞ്ഞൂറ്റി",
    "അറുനൂറ്റി", "എഴുനൂറ്റി", "എണ്ണൂറ്റി", "തൊള്ളായിരത്തി",
]
# "N thousand" for N = 1..19 (irregular, so tabulated; index 0 unused).
_THOUSANDS_SMALL = [
    "", "ആയിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം", "ആറായിരം",
    "ഏഴായിരം", "എണ്ണായിരം", "ഒൻപതിനായിരം", "പതിനായിരം", "പതിനൊന്നായിരം",
    "പന്ത്രണ്ടായിരം", "പതിമൂന്നായിരം", "പതിനാലായിരം", "പതിനയ്യായിരം",
    "പതിനാറായിരം", "പതിനേഴായിരം", "പതിനെട്ടായിരം", "പത്തൊൻപതിനായിരം",
]
# Whole tens of thousands, indexed by the tens digit (2..9).
_THOUSANDS_TENS = [
    "", "", "ഇരുപതിനായിരം", "മുപ്പതിനായിരം", "നാൽപതിനായിരം", "അൻപതിനായിരം",
    "അറുപതിനായിരം", "എഴുപതിനായിരം", "എൺപതിനായിരം", "തൊണ്ണൂറായിരം",
]
# "unit thousand" after a tens prefix, indexed by the unit digit (1..9).
_THOUSANDS_AFTER_TENS = [
    "", "ഒന്നായിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം",
    "ആറായിരം", "ഏഴായിരം", "എട്ടായിരം", "ഒൻപതിനായിരം",
]
_LAKH = "ലക്ഷം"
_ONE_BEFORE_LAKH = "ഒരു"
_DECIMAL_POINT = "ദശാംശം"
_PERCENT = "ശതമാനം"

_MAX = 999_999

_I_SIGN = "\u0d3f"  # vowel sign i, the tail of every prefix form
_VIRAMA = "\u0d4d"
_GLIDE = "\u0d2f"  # ya
_FIVE_THOUSAND = "\u0d05\u0d2f\u0d4d\u0d2f\u0d3e\u0d2f\u0d3f\u0d30\u0d02"  # ayyayiram
_ANUSVARA = "\u0d02"
_LINK_TAIL = "\u0d24\u0d4d\u0d24\u0d3f"  # tthi

# Independent vowel letter -> dependent sign it becomes after the ya glide.
# The inherent-a vowel needs no sign at all.
_VOWEL_TO_SIGN = {
    "\u0d05": "",
    "\u0d06": "\u0d3e",
    "\u0d07": "\u0d3f",
    "\u0d08": "\u0d40",
    "\u0d09": "\u0d41",
    "\u0d0a": "\u0d42",
    "\u0d0b": "\u0d43",
    "\u0d0e": "\u0d46",
    "\u0d0f": "\u0d47",
    "\u0d10": "\u0d48",
    "\u0d12": "\u0d4a",
    "\u0d13": "\u0d4b",
    "\u0d14": "\u0d57",
}
# Stops that geminate after a prefix form: pa, ta, ka, ca.
_GEMINATING = frozenset("\u0d2a\u0d24\u0d15\u0d1a")


def _fuse(head: str, tail: str) -> str:
    """Join a prefix form (ending in the i-sign) with the word that follows it."""
    if not head.endswith(_I_SIGN) or not tail:
        return head + tail
    if tail.startswith(_FIVE_THOUSAND):
        return head[:-1] + tail[1:]
    first = tail[0]
    if first in _VOWEL_TO_SIGN:
        return head + _GLIDE + _VOWEL_TO_SIGN[first] + tail[1:]
    if first in _GEMINATING:
        return head + first + _VIRAMA + tail
    return head + tail


def _chain_form(word: str) -> str:
    """Form a group word takes when another group comes after it."""
    if word.endswith(_ANUSVARA):
        return word[:-1] + _LINK_TAIL
    return word


def _two_digit(n: int) -> str:
    """Words for 1..99 as one compound."""
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, unit = divmod(n, 10)
    if unit == 0:
        return _TENS[tens]
    return _fuse(_TENS_PREFIX[tens], _UNITS[unit])


def _three_digit(n: int) -> str:
    """Words for 1..999 as one compound."""
    hundreds, rest = divmod(n, 100)
    if hundreds == 0:
        return _two_digit(rest)
    if rest == 0:
        return _HUNDREDS[hundreds]
    return _fuse(_HUNDREDS_PREFIX[hundreds], _two_digit(rest))


def _thousand_word(k: int) -> str:
    """Single word for k thousand, k in 1..99."""
    if k < 20:
        return _THOUSANDS_SMALL[k]
    tens, unit = divmod(k, 10)
    if unit == 0:
        return _THOUSANDS_TENS[tens]
    return _fuse(_TENS_PREFIX[tens], _THOUSANDS_AFTER_TENS[unit])


def cardinal(n: int) -> str:
    """Malayalam words for an integer in 0..999999."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError(f"Malayalam cardinal expects a plain int, received {type(n).__name__}")
    if n < 0 or n > _MAX:
        raise ValueError(f"Malayalam cardinal supports 0 to {_MAX}; {n} is outside that span")
    if n == 0:
        return _ZERO

    lakhs = n // 100_000
    thousands = (n // 1000) % 100
    low = n % 1000

    groups: list[str] = []
    if lakhs:
        lead = _ONE_BEFORE_LAKH if lakhs == 1 else cardinal(lakhs)
        lakh_word = _chain_form(_LAKH) if (thousands or low) else _LAKH
        groups.append(f"{lead} {lakh_word}")
    if thousands:
        word = _thousand_word(thousands)
        groups.append(_chain_form(word) if low else word)
    if low:
        groups.append(_three_digit(low))
    return " ".join(groups)


def _digit_word(ch: str) -> str:
    if len(ch) != 1 or ch not in "0123456789":
        raise ValueError(f"fractional part may hold only ASCII digits, found {ch!r}")
    d = int(ch)
    return _ZERO if d == 0 else _UNITS[d]


def decimal(int_part: int, frac_digits: str) -> str:
    """Integer part as a cardinal, the point word, then each fraction digit."""
    words = [cardinal(int_part), _DECIMAL_POINT]
    words.extend(_digit_word(ch) for ch in frac_digits)
    return " ".join(words)


def percent(raw: str) -> str:
    """Read a digit string (optionally with one '.') followed by the percent word."""
    whole, dot, frac = raw.partition(".")
    if not whole.isdigit() or not whole.isascii():
        raise ValueError(f"percentage needs ASCII digits before any point, got {raw!r}")
    number = decimal(int(whole), frac) if (dot and frac) else cardinal(int(whole))
    return f"{number} {_PERCENT}"
