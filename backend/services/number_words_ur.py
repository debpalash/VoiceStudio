"""Urdu (ur) number verbalization — a native backend for text_normalization.

``num2words`` has no Urdu locale, so Urdu text reached the TTS engine with raw
digits, which OmniVoice garbles (1956 was heard as "انیس سو سپنس"); the same
sentence written out in Urdu words came back with 0% CER. This module renders
integers, decimals and percentages the way an Urdu speaker reads them:

  * 0–99 are irregular (55 = پچپن, 56 = چھپن, 49 = انچاس, 99 = ننانوے), so they
    come from a full 100-entry table rather than tens + units rules;
  * hundreds, thousands and lakhs are separate words with Indian grouping
    (10^5 = لاکھ): 150 → ایک سو پچاس, 2026 → دو ہزار چھبیس, 150000 →
    ایک لاکھ پچاس ہزار;
  * 1100–1999 are read in hundreds, as Urdu speakers say years and amounts
    alike: 1956 → انیس سو چھپن, 1500 → پندرہ سو. Outside that range
    (1000, 2000 and up) the ہزار form is the spoken one.

Its deliberately narrow scope matches ``services.text_normalization``: only
integers from 0 through 999 999 are supported, so crore and longer forms stay
out of scope. Decimals say the integer, ``DECIMAL_WORD``, then each fractional
digit separately; percentages say the number followed by ``PERCENT_WORD``.

Spelling uses Urdu letters (ی U+06CC, ہ U+06C1, ک U+06A9, ے U+06D2, ھ U+06BE
for aspirates), never their Arabic look-alikes. Pure Python, no dependencies;
output contains no digits.
"""

from __future__ import annotations

# ── Tables ───────────────────────────────────────────────────────────────────

_BELOW_HUNDRED = [
    "صفر", "ایک", "دو", "تین", "چار", "پانچ", "چھ", "سات", "آٹھ", "نو",                      # 0-9
    "دس", "گیارہ", "بارہ", "تیرہ", "چودہ", "پندرہ", "سولہ", "سترہ", "اٹھارہ", "انیس",          # 10-19
    "بیس", "اکیس", "بائیس", "تئیس", "چوبیس", "پچیس", "چھبیس", "ستائیس", "اٹھائیس", "انتیس",   # 20-29
    "تیس", "اکتیس", "بتیس", "تینتیس", "چونتیس", "پینتیس", "چھتیس", "سینتیس", "اڑتیس", "انتالیس",  # 30-39
    "چالیس", "اکتالیس", "بیالیس", "تینتالیس", "چوالیس", "پینتالیس", "چھیالیس", "سینتالیس", "اڑتالیس", "انچاس",  # 40-49
    "پچاس", "اکاون", "باون", "ترپن", "چون", "پچپن", "چھپن", "ستاون", "اٹھاون", "انسٹھ",     # 50-59
    "ساٹھ", "اکسٹھ", "باسٹھ", "ترسٹھ", "چونسٹھ", "پینسٹھ", "چھیاسٹھ", "سڑسٹھ", "اڑسٹھ", "انہتر",  # 60-69
    "ستر", "اکہتر", "بہتر", "تہتر", "چوہتر", "پچہتر", "چھہتر", "ستتر", "اٹھتر", "اناسی",     # 70-79
    "اسی", "اکیاسی", "بیاسی", "تراسی", "چوراسی", "پچاسی", "چھیاسی", "ستاسی", "اٹھاسی", "نواسی",  # 80-89
    "نوے", "اکانوے", "بانوے", "ترانوے", "چورانوے", "پچانوے", "چھیانوے", "ستانوے", "اٹھانوے", "ننانوے",  # 90-99
]
assert len(_BELOW_HUNDRED) == 100

HUNDRED = "سو"
THOUSAND = "ہزار"
LAKH = "لاکھ"
DECIMAL_WORD = "اعشاریہ"
PERCENT_WORD = "فیصد"


# ── Building blocks ──────────────────────────────────────────────────────────

def _below_thousand(n: int) -> str:
    """0-999: "<n> سو <rest>" (150 → ایک سو پچاس, 900 → نو سو)."""
    if n < 100:
        return _BELOW_HUNDRED[n]
    hundreds, rest = divmod(n, 100)
    head = f"{_BELOW_HUNDRED[hundreds]} {HUNDRED}"
    return f"{head} {_BELOW_HUNDRED[rest]}" if rest else head


# ── Public API ───────────────────────────────────────────────────────────────

def cardinal(n: int) -> str:
    if type(n) is not int:
        raise TypeError("Urdu cardinal values must be plain integers")
    if not 0 <= n <= 999_999:
        raise ValueError("Urdu cardinal values must be between 0 and 999999")
    if n < 1000:
        return _below_thousand(n)
    if 1100 <= n <= 1999:
        hundreds, remainder = divmod(n, 100)
        parts = [_BELOW_HUNDRED[hundreds], HUNDRED]
        if remainder:
            parts.append(_BELOW_HUNDRED[remainder])
        return " ".join(parts)

    lakhs = n // 100_000
    thousands = (n % 100_000) // 1000
    units = n % 1000
    grouped_phrases = (
        f"{_BELOW_HUNDRED[lakhs]} {LAKH}" if lakhs else "",
        f"{_BELOW_HUNDRED[thousands]} {THOUSAND}" if thousands else "",
        _below_thousand(units) if units else "",
    )
    return " ".join(phrase for phrase in grouped_phrases if phrase)


def decimal(int_part: int, frac_digits: str) -> str:
    digit_words = (_BELOW_HUNDRED[int(digit)] for digit in frac_digits)
    return " ".join((cardinal(int_part), DECIMAL_WORD, *digit_words))


def percent(raw: str) -> str:
    if "." in raw:
        left, right = raw.split(".", 1)
        number = decimal(int(left), right)
    else:
        number = cardinal(int(raw))
    return f"{number} {PERCENT_WORD}"
