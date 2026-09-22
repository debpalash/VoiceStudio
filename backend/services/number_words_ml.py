"""Malayalam (ml) number verbalization — a native backend for text_normalization.

``num2words`` has no Malayalam locale (it ships bn/kn/te but not ml, hi or ta),
so a Malayalam dub or audiobook reached the TTS engine with raw digits, which
OmniVoice reads in English or garbles. This module renders integers, decimals
and percentages the way a Malayalam news reader says them, with the sandhi
rules that make the compounds pronounceable:

  * tens/hundreds prefixes end in ``ി`` and take a glide ``യ`` before a
    vowel-initial word: ഇരുപത്തി + ഒന്ന് → ഇരുപത്തിയൊന്ന്, നൂറ്റി + അൻപത് →
    നൂറ്റിയൻപത്;
  * a following hard consonant (പ, ത) geminates: നൂറ്റി + പത്ത് → നൂറ്റിപ്പത്ത്,
    നൂറ്റി + തൊണ്ണൂറ് → നൂറ്റിത്തൊണ്ണൂറ്;
  * thousands and lakhs take the linking form ``-ത്തി`` and are written as
    separate words: 2026 → രണ്ടായിരത്തി ഇരുപത്തിയാറ്, 1,50,000 →
    ഒരു ലക്ഷത്തി അൻപതിനായിരം (Indian grouping: 10^5 = ലക്ഷം).

Scope matches the conservative rules of :mod:`services.text_normalization`:
integers 0–999999 (six digits, so crores are out of scope), decimals read as
"<int> ദശാംശം <digit> <digit>…", percentages as "<number> ശതമാനം". Years are
plain cardinals in Malayalam (no "nineteen eighty-four" form), so no special
year handling is needed.

Pure Python, no dependencies; output contains no digits, ZWJ or ZWNJ.
"""

from __future__ import annotations

# ── Tables ───────────────────────────────────────────────────────────────────

_UNITS = [
    "പൂജ്യം", "ഒന്ന്", "രണ്ട്", "മൂന്ന്", "നാല്", "അഞ്ച്", "ആറ്", "ഏഴ്", "എട്ട്", "ഒൻപത്",
]
_TEENS = [
    "പത്ത്", "പതിനൊന്ന്", "പന്ത്രണ്ട്", "പതിമൂന്ന്", "പതിനാല്", "പതിനഞ്ച്",
    "പതിനാറ്", "പതിനേഴ്", "പതിനെട്ട്", "പത്തൊൻപത്",
]
# Standalone tens (20, 30, …) and the prefix form used before a unit.
_TENS = [None, None, "ഇരുപത്", "മുപ്പത്", "നാൽപത്", "അൻപത്", "അറുപത്", "എഴുപത്", "എൺപത്", "തൊണ്ണൂറ്"]
_TENS_PREFIX = [
    None, None, "ഇരുപത്തി", "മുപ്പത്തി", "നാൽപത്തി", "അൻപത്തി", "അറുപത്തി",
    "എഴുപത്തി", "എൺപത്തി", "തൊണ്ണൂറ്റി",
]
# Standalone hundreds and their prefix form before a remainder.
_HUNDREDS = [
    None, "നൂറ്", "ഇരുനൂറ്", "മുന്നൂറ്", "നാനൂറ്", "അഞ്ഞൂറ്", "അറുനൂറ്", "എഴുനൂറ്",
    "എണ്ണൂറ്", "തൊള്ളായിരം",
]
_HUNDREDS_PREFIX = [
    None, "നൂറ്റി", "ഇരുനൂറ്റി", "മുന്നൂറ്റി", "നാനൂറ്റി", "അഞ്ഞൂറ്റി", "അറുനൂറ്റി",
    "എഴുനൂറ്റി", "എണ്ണൂറ്റി", "തൊള്ളായിരത്തി",
]
# N × 1000 for N = 1..19 (irregular), then whole tens of thousands.
_THOUSANDS_SMALL = [
    None, "ആയിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം", "ആറായിരം",
    "ഏഴായിരം", "എണ്ണായിരം", "ഒൻപതിനായിരം", "പതിനായിരം", "പതിനൊന്നായിരം",
    "പന്ത്രണ്ടായിരം", "പതിമൂന്നായിരം", "പതിനാലായിരം", "പതിനയ്യായിരം",
    "പതിനാറായിരം", "പതിനേഴായിരം", "പതിനെട്ടായിരം", "പത്തൊൻപതിനായിരം",
]
_TENS_THOUSAND = [
    None, None, "ഇരുപതിനായിരം", "മുപ്പതിനായിരം", "നാൽപതിനായിരം", "അൻപതിനായിരം",
    "അറുപതിനായിരം", "എഴുപതിനായിരം", "എൺപതിനായിരം", "തൊണ്ണൂറായിരം",
]
# Unit-thousand forms that follow a tens prefix (21000 = ഇരുപത്തി + ഒന്നായിരം).
_UNIT_THOUSAND_COMPOUND = [
    None, "ഒന്നായിരം", "രണ്ടായിരം", "മൂവായിരം", "നാലായിരം", "അയ്യായിരം",
    "ആറായിരം", "ഏഴായിരം", "എട്ടായിരം", "ഒൻപതിനായിരം",
]

LAKH = "ലക്ഷം"
DECIMAL_WORD = "ദശാംശം"
PERCENT_WORD = "ശതമാനം"

# Independent vowel → dependent vowel sign, for the യ-glide sandhi.
_VOWEL_SIGN = {
    "അ": "", "ആ": "ാ", "ഇ": "ി", "ഈ": "ീ", "ഉ": "ു", "ഊ": "ൂ",
    "എ": "െ", "ഏ": "േ", "ഐ": "ൈ", "ഒ": "ൊ", "ഓ": "ോ", "ഔ": "ൗ",
}
_GEMINATE = {"പ", "ത", "ക", "ച"}
_VIRAMA = "്"


# ── Sandhi ───────────────────────────────────────────────────────────────────

def _join(prefix: str, word: str) -> str:
    """Attach ``word`` to a prefix ending in ``ി`` with Malayalam sandhi."""
    first = word[0]
    if word.startswith("അയ്യായിരം"):
        # ഇരുപത്തി + അയ്യായിരം → ഇരുപത്തയ്യായിരം (the ി is absorbed).
        return prefix[:-1] + word[1:]
    if first in _VOWEL_SIGN:
        return prefix + "യ" + _VOWEL_SIGN[first] + word[1:]
    if first in _GEMINATE:
        return prefix + first + _VIRAMA + word
    return prefix + word


def _linking(word: str) -> str:
    """ആയിരം → ആയിരത്തി, ലക്ഷം → ലക്ഷത്തി (final anusvara → -ത്തി)."""
    return word[:-1] + "ത്തി"


# ── Building blocks ──────────────────────────────────────────────────────────

def _below_hundred(n: int) -> str:
    """0-99: units, teens, round tens, or tens-prefix + unit with sandhi."""
    if n < 10:
        return _UNITS[n]
    if n < 20:
        return _TEENS[n - 10]
    tens, unit = divmod(n, 10)
    if unit == 0:
        return _TENS[tens]
    return _join(_TENS_PREFIX[tens], _UNITS[unit])


def _below_thousand(n: int) -> str:
    """0-999: hundreds joined to the remainder as one word (നൂറ്റിയൻപത്)."""
    if n < 100:
        return _below_hundred(n)
    hundreds, rest = divmod(n, 100)
    if rest == 0:
        return _HUNDREDS[hundreds]
    return _join(_HUNDREDS_PREFIX[hundreds], _below_hundred(rest))


def _thousands(n: int) -> str:
    """N × 1000 for 1 ≤ N ≤ 99."""
    if n < 20:
        return _THOUSANDS_SMALL[n]
    tens, unit = divmod(n, 10)
    if unit == 0:
        return _TENS_THOUSAND[tens]
    return _join(_TENS_PREFIX[tens], _UNIT_THOUSAND_COMPOUND[unit])


# ── Public API ───────────────────────────────────────────────────────────────

def cardinal(n: int) -> str:
    """Render an integer 0 ≤ n ≤ 999999 as Malayalam words."""
    if isinstance(n, bool) or not isinstance(n, int):
        raise TypeError("cardinal() expects an int")
    if n < 0 or n > 999_999:
        raise ValueError("cardinal() supports 0..999999")
    if n < 1000:
        return _below_thousand(n)
    lakhs, rest = divmod(n, 100_000)
    parts: list[str] = []
    if lakhs:
        head = "ഒരു" if lakhs == 1 else _below_hundred(lakhs)
        parts.append(f"{head} {_linking(LAKH) if rest else LAKH}")
    thousands, units = divmod(rest, 1000)
    if thousands:
        word = _thousands(thousands)
        parts.append(_linking(word) if units else word)
    if units:
        parts.append(_below_thousand(units))
    return " ".join(parts)


def decimal(int_part: int, frac_digits: str) -> str:
    """3.14 → മൂന്ന് ദശാംശം ഒന്ന് നാല് (fraction digits are read one by one)."""
    digits = " ".join(_UNITS[int(d)] for d in frac_digits)
    return f"{cardinal(int_part)} {DECIMAL_WORD} {digits}"


def percent(raw: str) -> str:
    """'50' → അൻപത് ശതമാനം; '2.5' → രണ്ട് ദശാംശം അഞ്ച് ശതമാനം."""
    if "." in raw:
        a, b = raw.split(".", 1)
        body = decimal(int(a), b)
    else:
        body = cardinal(int(raw))
    return f"{body} {PERCENT_WORD}"
