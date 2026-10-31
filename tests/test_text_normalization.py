"""Pre-TTS text normalization (services/text_normalization.py).

Covers the conservative per-language pre-pass: table-driven change /
leave-unchanged cases, idempotency (f(f(x)) == f(x)), digit preservation for
unsupported languages, bracket-grammar safety, the pref/env toggle, the
normalization-BEFORE-pronunciation-dictionary ordering, and the /generate
integration (applied exactly once, at the text→engine choke point).

The engine layer is stubbed (no real model loads), matching
test_generate_engine.py.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest
import torch

from services import text_normalization
from services.text_normalization import (
    normalization_enabled,
    normalize_for_tts,
    normalize_text,
)


# ── Table-driven: text that SHOULD change ────────────────────────────────────

_CHANGE_CASES = [
    # (language, input, expected)
    # EN numbers / times / ordinals / currency / percent
    ("English", "I have 2 cats", "I have two cats"),
    ("English", "It is 3:30 now", "It is three thirty now"),
    ("English", "At 3:00 sharp", "At three o'clock sharp"),
    ("English", "At 12:05 sharp", "At twelve oh five sharp"),
    # A 24-hour time on the hour is read in hundreds, not with "o'clock".
    ("English", "Dinner at 19:00 sharp", "Dinner at nineteen hundred sharp"),
    ("English", "Closes at 0:00 tonight", "Closes at zero hundred tonight"),
    ("English", "At 12:00 sharp", "At twelve o'clock sharp"),
    ("English", "At 18:30 sharp", "At eighteen thirty sharp"),
    ("English", "the 21st of May", "the twenty-first of May"),
    ("English", "It costs $5", "It costs five dollars"),
    ("English", "Just $1 more", "Just one dollar more"),
    ("English", "It costs $5.99 now", "It costs five dollars, ninety-nine cents now"),
    # a zero part is not spoken
    ("English", "only $0.50 now", "only fifty cents now"),
    ("English", "just $0.01 more", "just one cent more"),
    ("English", "it costs $5.00 now", "it costs five dollars now"),
    ("English", "only $1.00.", "only one dollar."),
    ("English", "it was $0.00 then", "it was zero dollars then"),
    # Sentence punctuation after an amount is not part of it: the lookahead
    # used to reject any period or comma, so these reached the engine as digits.
    ("English", "It costs $5.", "It costs five dollars."),
    ("English", "Pay $5, please", "Pay five dollars, please"),
    ("English", "It costs $5.99.", "It costs five dollars, ninety-nine cents."),
    ("English", "$12.34, $56.78, and $9.",
     "twelve dollars, thirty-four cents, fifty-six dollars, seventy-eight cents, and nine dollars."),
    ("English", "rated 3.5 stars", "rated three point five stars"),
    # The written trailing zeros are spoken, not dropped by the float.
    ("English", "version 3.0 ships", "version three point zero ships"),
    ("English", "it weighs 2.50 kg", "it weighs two point five zero kg"),
    ("German", "Version 3.0 kommt", "Version drei Komma null kommt"),
    ("English", "version \uff13.\uff10 ships", "version three point zero ships"),
    ("Portuguese", "valor 3.0", "valor três vírgula zero"),
    # English writes decimals with a period, so three digits after it are a
    # decimal here (the same digits are a thousands group in German, below).
    ("English", "pi is 3.141 here", "pi is three point one four one here"),
    ("English", "battery at 50%", "battery at fifty percent"),
    # ...and so are the ones written in a percentage.
    ("English", "rates rose 2.50% today", "rates rose two point five zero percent today"),
    ("English", "inflation was 3.0%", "inflation was three point zero percent"),
    ("German", "etwa 3.50%", "etwa drei Komma fünf null Prozent"),
    ("English", "back in 1984", "back in nineteen eighty-four"),
    ("English", "I have 3.", "I have three."),
    # EN abbreviations (guards: titles need a following capitalized word)
    ("English", "Dr. Smith is here", "Doctor Smith is here"),
    ("English", "Mr. Jones and Mrs. Jones", "Mister Jones and Missus Jones"),
    ("English", "St. Louis is far", "Saint Louis is far"),
    ("English", "cats, dogs, etc. are pets", "cats, dogs, et cetera are pets"),
    ("English", "fruit, e.g. apples", "fruit, for example apples"),
    ("English", "good vs. evil", "good versus evil"),
    ("English", "exhibit No. 5", "exhibit number five"),
    # a number glued to the abbreviation is still spoken as one
    ("English", "exhibit No.5", "exhibit number five"),
    ("German", "Nr.5 gewinnt", "Nummer fünf gewinnt"),
    ("French", "le n°5 gagne", "le numéro cinq gagne"),
    # ISO-ish codes resolve like display names
    ("en", "I have 2 cats", "I have two cats"),
    ("en-US", "I have 2 cats", "I have two cats"),
    # Other languages
    ("German", "Er hat 42 Katzen", "Er hat zweiundvierzig Katzen"),
    ("German", "z.B. Dr. Meier", "zum Beispiel Doktor Meier"),
    ("German", "Nr. 5 gewinnt", "Nummer fünf gewinnt"),
    ("German", "etwa 50%", "etwa fünfzig Prozent"),
    ("German", "etwa 3.5 Kilo", "etwa drei Komma fünf Kilo"),
    # The day of a date keeps its digits; the year still speaks. A number
    # that ends a sentence before any other word still speaks.
    ("German", "Am 3. Mai 2024", "Am 3. Mai zweitausendvierundzwanzig"),
    ("German", "Ich habe 3. Dann 4.", "Ich habe drei. Dann vier."),
    ("German", "Kapitel 3. Der Anfang.", "Kapitel drei. Der Anfang."),
    # A month name glued to a digit is not a month token.
    ("German", "Am 3. Mai2 war es", "Am drei. Mai2 war es"),
    ("Spanish", "tengo 42 gatos", "tengo cuarenta y dos gatos"),
    ("Spanish", "el Sr. García", "el Señor García"),
    ("French", "il a 42 chats", "il a quarante-deux chats"),
    ("French", "Mme Dupont arrive", "Madame Dupont arrive"),
    # Numbers around a space-grouped amount still speak; the group itself
    # keeps its digits.
    ("French", "2 colis de 1 234 pièces", "deux colis de 1 234 pièces"),
    # A year is not a leading group: four digits never start one.
    ("French", "en 2024 500 personnes", "en deux mille vingt-quatre cinq cents personnes"),
    # Narrow and no-break spaces (what French keyboards and word processors
    # insert) collapse to a plain space, and the group stays whole.
    ("French", "soit 1\u202f234\u00a0567 euros", "soit 1 234 567 euros"),
    # English keeps reading separate numbers: "2 100" is not a group there.
    ("English", "room 2 100 meters away", "room two one hundred meters away"),
    ("Russian", "у меня 42 кота", "у меня сорок два кота"),
    # Malayalam: no num2words locale, so these go through number_words_ml.
    ("Malayalam", "എനിക്ക് 5 പൂച്ചകൾ", "എനിക്ക് അഞ്ച് പൂച്ചകൾ"),
    ("Malayalam", "വീട് നമ്പർ 21", "വീട് നമ്പർ ഇരുപത്തിയൊന്ന്"),
    ("Malayalam", "ആകെ 150 പേർ", "ആകെ നൂറ്റിയൻപത് പേർ"),
    ("Malayalam", "വർഷം 2026 ആണ്", "വർഷം രണ്ടായിരത്തി ഇരുപത്തിയാറ് ആണ്"),
    ("Malayalam", "വില 150000 ആണ്", "വില ഒരു ലക്ഷത്തി അൻപതിനായിരം ആണ്"),
    ("Malayalam", "വില 100000 ആണ്", "വില ഒരു ലക്ഷം ആണ്"),
    ("Malayalam", "ഫലം 50% ആണ്", "ഫലം അൻപത് ശതമാനം ആണ്"),
    ("Malayalam", "അളവ് 3.14 ആണ്", "അളവ് മൂന്ന് ദശാംശം ഒന്ന് നാല് ആണ്"),
    ("ml", "എനിക്ക് 5 പൂച്ചകൾ", "എനിക്ക് അഞ്ച് പൂച്ചകൾ"),
    ("ml-IN", "എനിക്ക് 5 പൂച്ചകൾ", "എനിക്ക് അഞ്ച് പൂച്ചകൾ"),
    ("Malayalam", "[pause 300ms] 7 പേർ", "[pause 300ms] ഏഴ് പേർ"),
    ("Malayalam", "[voice:A3] 7 പേർ", "[voice:A3] ഏഴ് പേർ"),
    ("Malayalam", "എനിക്ക് ൫ പൂച്ചകൾ", "എനിക്ക് അഞ്ച് പൂച്ചകൾ"),
    # Urdu goes through the native verbalizer too (num2words has no ur
    # locale): an irregular 0-99 table, سو / ہزار / لاکھ, and 1100-1999 read in
    # hundreds the way Urdu speakers say years ("انیس سو چھپن", not the
    # "انیس سو سپنس" OmniVoice produced from raw digits).
    ("Urdu", "سن 1956 میں 10 لوگ", "سن انیس سو چھپن میں دس لوگ"),
    ("Urdu", "عمر 55 سال", "عمر پچپن سال"),
    ("Urdu", "قیمت 150 روپے", "قیمت ایک سو پچاس روپے"),
    ("Urdu", "سال 2026 میں", "سال دو ہزار چھبیس میں"),
    ("Urdu", "سال 2000 تک", "سال دو ہزار تک"),
    ("Urdu", "1000 لوگ", "ایک ہزار لوگ"),
    ("Urdu", "150000 روپے", "ایک لاکھ پچاس ہزار روپے"),
    ("Urdu", "تقریباً 50%", "تقریباً پچاس فیصد"),
    ("Urdu", "3.5 کلومیٹر", "تین اعشاریہ پانچ کلومیٹر"),
    ("ur", "عمر 55 سال", "عمر پچپن سال"),
    ("ur-PK", "عمر 55 سال", "عمر پچپن سال"),
    ("Urdu", "[pause 300ms] 42 [voice:Ali]", "[pause 300ms] بیالیس [voice:Ali]"),
    # Urdu (Extended Arabic-Indic) digits are digits too.
    ("Urdu", "سن ۱۹۵۶ میں", "سن انیس سو چھپن میں"),
    # Tilde between two numbers is spoken instead of being swallowed.
    ("Korean", "사과 3~5개 주세요", "사과 3에서 5개 주세요"),
    ("Korean", "가격은 1000~2000원입니다", "가격은 1000에서 2000원입니다"),
    ("ko", "기온 -5 ~ 3도", "기온 -5에서 3도"),
    ("Japanese", "参加者は10\u301c20人", "参加者は10から20人"),
    ("Japanese", "気温は15\uff5e25度", "気温は15から25度"),
    ("Chinese", "需要3\uff5e4天", "需要3到4天"),
    ("English", "Open 20~30 minutes", "Open twenty to thirty minutes"),
    ("ko", "[pause 300ms] 1.5~2.5배", "[pause 300ms] 1.5에서 2.5배"),
    # Universal safety filters (language-independent)
    (None, "hello\u200b \u200dworld", "hello \u200dworld"),
    (None, "too   many\t spaces", "too many spaces"),
    (None, "wow!!!!!!!!", "wow!!!"),
    (None, "wait.........", "wait..."),
    (None, "a�b", "ab"),
    (None, "Tom &amp; Jerry", "Tom & Jerry"),
    (None, "one&nbsp;space", "one space"),
    # Markdown emphasis delimiters are stripped, not spoken (#3197)
    ("English", "Say **bold** word", "Say bold word"),
    ("English", "Say __bold__ word", "Say bold word"),
    ("English", "Say *italic* word", "Say italic word"),
    ("English", "Say _italic_ word", "Say italic word"),
    ("English", "**bold** and **more** here", "bold and more here"),
    # Digits still verbalize around a surviving arithmetic asterisk
    ("English", "2 * 3 = 6", "two * three = six"),
    ("English", "2 * 3 * 4 equals", "two * three * four equals"),
    # Emphasis spans soft line breaks
    ("English", "Say **first line\nsecond line** now", "Say first line\nsecond line now"),
    # Emphasis around a speech control strips; the control survives
    ("English", "**Hello [pause 300ms] world**", "Hello [pause 300ms] world"),
    # Asterisks open inside words (CommonMark); underscores do not
    ("English", "un**believ**able now", "unbelievable now"),
    ("English", "pre*fix* this", "prefix this"),
    ("English", "___bold___ here", "bold here"),
    # Nested spans resolve fully, so a second pass is a no-op
    ("English", "**outer **inner** outer**", "outer inner outer"),
    # Escaped markers are literal text and stay put
    (None, "\\*literal\\* word", "\\*literal\\* word"),
    # Removing markers must not leave punctuation above the repeat cap
    (None, "wow!*!*!*!*!", "wow!!!"),
]


@pytest.mark.parametrize("language,raw,expected", _CHANGE_CASES)
def test_normalizes(language, raw, expected):
    assert normalize_text(raw, language) == expected


def test_unsafe_control_filter_has_exact_boundaries():
    """Delete the security control set without widening into adjacent text."""
    expected_codepoints = {
        *range(0x00, 0x09),
        0x0B,
        0x0C,
        *range(0x0E, 0x20),
        *range(0x7F, 0xA0),
        0x200B,
        0x200E,
        0x200F,
        *range(0x202A, 0x202F),
        *range(0x2060, 0x2065),
        0xFEFF,
        0xFFFD,
    }
    unsafe = "".join(map(chr, sorted(expected_codepoints)))
    assert text_normalization._strip_unsafe_controls(unsafe) == ""

    # Tabs/newlines/carriage returns and code points immediately outside each
    # security range remain available to the later whitespace/text passes.
    allowed_boundaries = "\t\n\r ~\u00a0\u200a\u2010\u2029\u202f\u205f\u2065\ufefe\ufffc\ufffe"
    assert text_normalization._strip_unsafe_controls(allowed_boundaries) == allowed_boundaries
    assert text_normalization._UNSAFE_CONTROL_CODEPOINTS == expected_codepoints


# ── Table-driven: conservatism — text that must NOT change ───────────────────

_UNCHANGED_CASES = [
    # (language, input) — ambiguous constructs keep their digits/shape
    ("English", "version v2 shipped"),          # digit glued to a letter
    ("English", "order 1,000 units"),           # thousands separator: ambiguous
    ("English", "The crowd was 10 000 strong."),  # space-grouped: zeros never start a number
    ("English", "up 12 000% on the year"),
    ("English", "worth 1 000 000 dollars"),
    ("English", "It costs $1,000."),            # a dollar amount with a separator
    ("English", "It costs $5.5."),              # one decimal digit: not cents
    ("English", "pages 3-5 tonight"),           # range
    ("English", "agent 007 reporting"),         # leading-zero code
    ("English", "see 3.5.1 in the docs"),       # version string
    ("English", "call 5551234567 now"),         # 7+ digits: an ID, not a number
    ("English", "12:34:56 elapsed"),            # H:MM:SS duration, not a time
    ("English", "the ratio is 1/2 there"),      # fraction: ambiguous
    ("English", "MP3 files and A4 paper"),      # alphanumeric tokens
    ("English", "I met her. I agree."),         # "I" is a pronoun, not a numeral
    ("English", "Chapter II and III stand"),    # roman numerals out of scope
    ("English", "on Elm St. tonight"),          # street suffix: no name follows
    ("English", "I said no. Fine."),            # the word "no.", not "number"
    ("English", "down main st. Anyway"),        # lowercase "st." is not Saint
    ("German", "es kostet 3,5 Euro"),           # decimal comma: ambiguous
    # A period before three digits is a thousands group where the decimal
    # mark is a comma: kept as written, like "1,000" in English (it used to
    # read as 1.0, 2.5 and 12.5).
    ("German", "über 10.000 Menschen"),
    ("German", "Preis 2.500 Euro"),
    ("German", "etwa 12.500%"),                  # the percent rule shares it
    ("Spanish", "más de 1.000 personas"),
    ("French", "plus de 1.000 personnes"),
    # "3. Mai" is an ordinal date where a period marks ordinals; its spoken
    # form inflects ("am dritten", "der dritte"), so the day stays as
    # written for the engine. The integer rule used to speak the cardinal
    # and leave the period behind as a sentence break ("drei. Mai").
    ("German", "Am 3. Mai"),
    ("German", "Der 1. Januar ist frei."),
    ("German", "am 21. dezember"),
    ("German", "Am 3. Mrz. und am 5. Jun."),      # abbreviated months
    ("German", "Am 3. Mär. geschlossen"),
    ("German", "bis 1. Jul. offen"),
    ("Danish", "Den 3. maj"),
    ("Norwegian", "Den 17. mai"),
    ("Finnish", "3. toukokuuta"),
    ("Czech", "3. května"),
    ("Polish", "3. maja"),
    # A space between a 1-3 digit group and 3-digit groups is the other
    # thousands separator in those locales ("1 234", "10 000"): kept whole,
    # digits and all. The integer rule used to read the leading group on
    # its own ("un 234,5", "dix 000 habitants").
    ("French", "Il a 1 234,5 euros."),
    ("French", "Population : 10 000 habitants."),
    ("German", "Das sind 1 234 Euro."),
    ("Russian", "Это 1 234 рубля."),
    ("French", "soit 12 500% du total"),         # the percent rule shares it
    ("French", "soit 1 234 % du total"),
    ("French", "une baisse de -1 234% du total"),  # a sign stays on the run
    ("French", "soit -12 500 euros"),
    # Malayalam / native-digit boundaries: all stay as written.
    ("Malayalam", "വില 1,000 ആണ്"),
    ("Malayalam", "പേജ് 3-5 കാണുക"),
    ("Malayalam", "കോഡ് 007 ആണ്"),
    ("Malayalam", "കോഡ് ൦൦൭ ആണ്"),
    ("Malayalam", "ഐഡി 1234567 ആണ്"),
    ("Malayalam", "[pause 300ms]"),
    ("Malayalam", "[voice:A3]"),
    ("Malayalam", "5ാം ദിവസം"),            # vowel sign glued after the digits
    ("Malayalam", "ക്5 ദിവസം"),             # virama glued before the digits
    ("Malayalam", "ദിവസം5 കഴിഞ്ഞു"),         # consonant + digits word
    # Urdu native path keeps the same conservative boundaries
    ("Urdu", "1,000 روپے"),                       # thousands separator
    ("Urdu", "صفحہ 3-5 پڑھیں"),                   # range
    ("Urdu", "کوڈ 007"),                          # leading-zero code
    ("Urdu", "کوڈ ۰۰۷"),                          # native leading-zero code
    ("Urdu", "1234567 نمبر"),                     # 7+ digits: an ID
    ("Urdu", "ورژن 3.5.1"),                       # version string
    # Arabic num2words must preserve zero-prefixed identifiers.
    ("ar", "رقم الطلب ٠٠٤٢ محفوظ"),
    # Digit-range rule: only a clean tilde between two numbers is rewritten.
    ("Korean", "서울-부산 3-5일 걸려요"),         # hyphen is never a range mark
    ("Korean", "마감일은 2025-03-14 입니다"),     # ISO date
    ("Korean", "전화 010-1234-5678 로 연락"),    # phone-shaped
    ("Korean", "모델 AB12~34CD 재고"),            # letters touching the digits
    ("Korean", "1~2~3 순서"),                    # malformed chain
    ("Japanese", "また明日~"),                    # trailing tilde, no digits
    ("Vietnamese", "Khoảng 20~30 người"),        # no verified form
    (None, "about 20~30 people"),
    # Unsupported languages keep every digit (num2words unmapped)
    ("Japanese", "42 cats and 3.5 stars at 3:30"),
    ("Thai", "42 cats"),
    ("Chinese", "42 cats"),
    ("Korean", "42 cats"),
    # Vietnamese keeps every digit (#1139): num2words' vi cardinals misuse
    # "lẻ" for 2001-2099 and vi has no year form, so years read wrong — the
    # engine pronounces Vietnamese digits natively. Display name AND ISO code
    # must behave identically (the name used to bypass the vetted-locale gate).
    ("Vietnamese", "Sinh năm 1995, gặp lại năm 2024"),
    ("Vietnamese", "Các năm 2008, 2010 và 2025"),
    ("vi", "Sinh năm 1995, gặp lại năm 2024"),
    ("vi-VN", "42 con mèo"),
    (None, "42 cats"),                          # no language pin → no digits pass
    ("Auto", "42 cats"),
    # Bracket grammar is never rewritten
    ("English", "[pause 300ms] then [rate 0.9] speech [voice:Alice]"),
    ("English", "[[term|5]] stays bracket-managed"),
    # Unpaired markers and non-delimiter uses survive (#3197)
    ("English", "use snake_case names"),
    ("English", "foo__bar__baz here"),
    ("English", "имя_файла_здесь test"),
    ("English", "a lone * stays"),
    # Unspaced arithmetic keeps its multiplication signs
    (None, "2*3*4 and 10**2"),
    ("English", "[**bold**] markers inside brackets stay untouched"),
    # Plain text is byte-identical
    ("English", "The quick brown fox jumps."),
    (None, "already clean text"),
]


@pytest.mark.parametrize("language,raw", _UNCHANGED_CASES)
def test_leaves_unchanged(language, raw):
    assert normalize_text(raw, language) == raw


def test_full_name_lookup_gated_by_vetted_set():
    """#1139 recurrence guard: the display-name path must never bypass
    _NUM2WORDS_LANGS. "Vietnamese" used to resolve straight to "vi" (numbers
    mangled) while "vi" itself would have been rejected — the two spellings of
    one language behaved differently. Every resolvable display name must land
    inside the vetted set, or resolve to None."""
    from services.text_normalization import (
        _FULL_NAME_TO_CODE,
        _NUM2WORDS_LANGS,
        _num2words_lang,
    )
    for name in _FULL_NAME_TO_CODE:
        code = _num2words_lang(name)
        assert code is None or code in _NUM2WORDS_LANGS, name
    # And the reported case specifically: both spellings resolve to "leave
    # digits alone".
    assert _num2words_lang("Vietnamese") is None
    assert _num2words_lang("vi") is None


def test_cjk_passthrough_untouched():
    # CJK sentences (incl. CJK punctuation and full-width forms) pass through
    # the safety filters untouched — no punctuation stripped, no words injected.
    # Functional CJK test fixture: tests/ is allowlisted in
    # tests/test_no_hardcoded_cjk.py.
    cases = [
        "今日は3時30分に会いましょう。よろしく！",
        "我有42只猫，真的！？……",
        "안녕하세요. 42마리의 고양이가 있어요!",
    ]
    for s in cases:
        assert normalize_text(s, "Japanese") == s
        assert normalize_text(s, None) == s


def test_malayalam_cardinal_covers_whole_range():
    from services import number_words_ml

    for n in range(1_000_000):
        words = number_words_ml.cardinal(n)
        assert words
        assert not any(ch.isdigit() for ch in words)
        assert "\u200d" not in words and "\u200c" not in words


def test_malayalam_cardinal_rejects_bad_input():
    from services import number_words_ml

    for bad in (-1, 1_000_000):
        with pytest.raises(ValueError):
            number_words_ml.cardinal(bad)
    for bad in (True, 1.5, "3"):
        with pytest.raises(TypeError):
            number_words_ml.cardinal(bad)


def test_native_language_resolution():
    assert text_normalization._native_lang("Malayalam") == "ml"
    assert text_normalization._native_lang("ml-IN") == "ml"
    assert text_normalization._native_lang("English") is None
    assert text_normalization._num2words_lang("Malayalam") is None
    assert text_normalization._num2words_lang("ml") is None


def test_urdu_cardinal_spells_every_supported_integer():
    """Every supported Urdu cardinal is words-only and uses Urdu glyphs."""
    from services import number_words_ur

    arabic_lookalikes = {"\u064a", "\u0643", "\u0647", "\u0649"}
    failures = []
    for number in range(1_000_000):
        words = number_words_ur.cardinal(number)
        if (
            not words
            or any(character.isdigit() for character in words)
            or arabic_lookalikes.intersection(words)
        ):
            failures.append((number, words))
    assert failures == []


@pytest.mark.parametrize("n,expected", [
    (0, "صفر"), (19, "انیس"), (29, "انتیس"), (49, "انچاس"), (55, "پچپن"),
    (56, "چھپن"), (59, "انسٹھ"), (69, "انہتر"), (79, "اناسی"), (89, "نواسی"),
    (99, "ننانوے"), (100, "ایک سو"), (999, "نو سو ننانوے"),
    (1001, "ایک ہزار ایک"), (1099, "ایک ہزار ننانوے"),
    (1100, "گیارہ سو"), (1947, "انیس سو سینتالیس"), (1999, "انیس سو ننانوے"),
    (2001, "دو ہزار ایک"), (10_500, "دس ہزار پانچ سو"),
    (100_000, "ایک لاکھ"), (101_956, "ایک لاکھ ایک ہزار نو سو چھپن"),
    (999_999, "نو لاکھ ننانوے ہزار نو سو ننانوے"),
])
def test_urdu_cardinal_spot_checks(n, expected):
    from services import number_words_ur as ur
    assert ur.cardinal(n) == expected


def test_urdu_decimal_and_percent():
    from services import number_words_ur as ur
    assert ur.decimal(0, "05") == "صفر اعشاریہ صفر پانچ"
    assert ur.percent("2.5") == "دو اعشاریہ پانچ فیصد"
    assert ur.percent("100") == "ایک سو فیصد"


def test_urdu_language_is_outside_num2words_gate():
    """ur resolves to the native path, never to num2words (no ur locale)."""
    from services.text_normalization import _native_lang, _num2words_lang
    assert _num2words_lang("Urdu") is None
    assert _num2words_lang("ur") is None
    assert _native_lang("Urdu") == "ur"
    assert _native_lang("ur-PK") == "ur"


def test_urdu_normalize_for_tts_leaves_no_digits():
    # Resolved at run time: other suites purge and reload services.* modules.
    from services.text_normalization import normalize_for_tts

    out = normalize_for_tts("سن 1956 میں 10 لوگ", "ur")
    assert out == "سن انیس سو چھپن میں دس لوگ"
    assert not any(ch.isdigit() for ch in out)


# ── Idempotency: f(f(x)) == f(x) for every table case ───────────────────────

@pytest.mark.parametrize(
    "language,raw",
    [(lang, raw) for lang, raw, _ in _CHANGE_CASES]
    + [(lang, raw) for lang, raw in _UNCHANGED_CASES],
)
def test_idempotent(language, raw):
    once = normalize_text(raw, language)
    assert normalize_text(once, language) == once


def test_idempotent_double_encoded_entity():
    # "&amp;nbsp;" must not decode one layer per pass.
    once = normalize_text("bad &amp;nbsp; soup", None)
    assert normalize_text(once, None) == once


def test_many_unmatched_openers_stay_fast_and_untouched():
    """Linear scan (#3205 P1): 25k unmatched ``**`` openers must not stall
    synthesis. The lazy-regex version took ~16 s here; the scanner takes ms.
    The 10 s bound has 700x headroom and only trips on quadratic blowup."""
    import time
    from services.text_normalization import _strip_markdown_emphasis
    raw = "**a " * 25000
    start = time.perf_counter()
    assert _strip_markdown_emphasis(raw) == raw
    assert time.perf_counter() - start < 10


def test_many_matched_pairs_stay_fast():
    """One-join assembly (#3205 P1): per-span string rebuilds are quadratic
    on matched pairs (~22 s at this size); retained-slice join takes ms."""
    import time
    from services.text_normalization import _strip_markdown_emphasis
    raw = "**a** " * 300000
    start = time.perf_counter()
    assert _strip_markdown_emphasis(raw) == "a " * 300000
    assert time.perf_counter() - start < 10


@pytest.mark.parametrize("raw", ["__**__**" * 25000, "[pause 300ms] x " * 20000])
def test_long_runs_and_many_controls_stay_fast(raw):
    """Run bounds are saved at tokenization and bracket spans restore in one
    pass; per-token run rescans and per-span replaces were quadratic (#3205)."""
    import time
    from services.text_normalization import _strip_markdown_emphasis
    start = time.perf_counter()
    _strip_markdown_emphasis(raw)
    assert time.perf_counter() - start < 10


# ── Toggle: default ON + env override ────────────────────────────────────────

def test_enabled_by_default(monkeypatch):
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    assert normalization_enabled() is True
    assert normalize_for_tts("I have 2 cats", "English") == "I have two cats"


def test_never_written_pref_does_not_toggle_normalization(monkeypatch):
    """Only the env var switches it off; no Settings surface writes a pref."""
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    import core.prefs as prefs_mod
    monkeypatch.setattr(prefs_mod, "get", lambda key, default=None: False)
    assert normalization_enabled() is True


def test_env_off_bypasses(monkeypatch):
    monkeypatch.setenv(text_normalization.ENV_VAR, "0")
    raw = "I have 2 cats"
    assert normalize_for_tts(raw, "English") == raw


def test_env_on_beats_pref_off(monkeypatch):
    monkeypatch.setenv(text_normalization.ENV_VAR, "1")
    import core.prefs as prefs_mod
    monkeypatch.setattr(prefs_mod, "get", lambda key, default=None: False)
    assert normalize_for_tts("I have 2 cats", "English") == "I have two cats"


def test_never_raises(monkeypatch):
    # A crash inside the normalizer must degrade to raw text, never break synth.
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    monkeypatch.setattr(
        text_normalization, "normalize_text",
        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert normalize_for_tts("I have 2 cats", "English") == "I have 2 cats"


def test_empty_and_none_text():
    assert normalize_for_tts("", "English") == ""
    assert normalize_for_tts(None, "English") == ""
    assert normalize_text("", "English") == ""


# ── Ordering vs. the pronunciation dictionary ────────────────────────────────

def test_dictionary_operates_on_normalized_text():
    """Normalization runs FIRST: dictionary entries keyed on normalized words
    fire, and respellings are the final say (never re-normalized)."""
    from services.pronunciation import apply_pronunciation

    entries = [
        # Fires only if "2" was already normalized to "two".
        {"term": "two", "replacement": "TWO-OH", "type": "respelling",
         "language": "*", "enabled": 1},
        # A respelling that deliberately contains a digit must reach the
        # engine verbatim (normalization must NOT run after the dictionary).
        {"term": "cats", "replacement": "c4ts", "type": "respelling",
         "language": "*", "enabled": 1},
    ]
    normalized = normalize_text("I have 2 cats", "English")
    assert normalized == "I have two cats"
    out = apply_pronunciation(normalized, entries, "English")
    assert out == "I have TWO-OH c4ts"  # entry fired + digit respelling intact


# ── /generate integration: applied exactly once, before the dictionary ──────

def _tts_mod():
    """Resolve services.tts_backend at RUN time (same rationale as
    test_generate_engine.py — collection-time bindings can go stale)."""
    return importlib.import_module("services.tts_backend")


def _norm_mod():
    """Resolve services.text_normalization at RUN time so monkeypatches land
    on the same module object the routes import per-request."""
    return importlib.import_module("services.text_normalization")


def _make_fake_engine():
    class _FakeEngine(_tts_mod().TTSBackend):
        id = "fake-norm-engine"
        display_name = "Fake Norm Engine (test)"
        gpu_compat = ("cpu",)
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((text, kw))
            return torch.zeros(1, 24000)

    return _FakeEngine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app
    return TestClient(app, client=("127.0.0.1", 50000))


@pytest.fixture()
def fake_engine(monkeypatch):
    fake = _make_fake_engine()
    monkeypatch.setitem(_tts_mod()._REGISTRY, "fake-norm-engine", fake)
    monkeypatch.delenv("OMNIVOICE_TTS_BACKEND", raising=False)
    return fake


def test_generate_applies_normalization_exactly_once(client, monkeypatch, fake_engine):
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    norm_mod = _norm_mod()
    calls = []
    real = norm_mod.normalize_for_tts

    def spy(text, language=None):
        calls.append(text)
        return real(text, language)

    monkeypatch.setattr(norm_mod, "normalize_for_tts", spy)

    res = client.post("/generate", data={
        "text": "Dr. Smith has 2 cats", "language": "English",
        "engine": "fake-norm-engine", "seed": "42",
    })

    assert res.status_code == 200, res.text
    assert len(calls) == 1  # exactly once, at the choke point
    assert len(fake_engine.calls) == 1
    assert fake_engine.calls[0][0] == "Doctor Smith has two cats"


def test_generate_normalizes_before_dictionary(client, monkeypatch, fake_engine):
    """Route-level pin of the ordering: the DB dictionary sees normalized
    text, and its respellings are not re-normalized."""
    monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    monkeypatch.delenv("OMNIVOICE_PRONUNCIATION", raising=False)
    pron_mod = importlib.import_module("services.pronunciation")
    monkeypatch.setattr(pron_mod, "load_entries_from_db", lambda: [
        {"term": "two", "replacement": "TWO-OH", "type": "respelling",
         "language": "*", "enabled": 1},
        {"term": "cats", "replacement": "c4ts", "type": "respelling",
         "language": "*", "enabled": 1},
    ])

    res = client.post("/generate", data={
        "text": "I have 2 cats", "language": "English",
        "engine": "fake-norm-engine", "seed": "42",
    })

    assert res.status_code == 200, res.text
    assert fake_engine.calls[-1][0] == "I have TWO-OH c4ts"


def test_generate_toggle_off_sends_raw_text(client, monkeypatch, fake_engine):
    monkeypatch.setenv(text_normalization.ENV_VAR, "0")
    res = client.post("/generate", data={
        "text": "Dr. Smith has 2 cats", "language": "English",
        "engine": "fake-norm-engine", "seed": "42",
    })
    assert res.status_code == 200, res.text
    assert fake_engine.calls[-1][0] == "Dr. Smith has 2 cats"


# ── Longform: chapter render normalizes spans (and keys the cache on it) ────

def _render_chapter(tmp_path, text, language, monkeypatch, env=None):
    if env is None:
        monkeypatch.delenv(text_normalization.ENV_VAR, raising=False)
    else:
        monkeypatch.setenv(text_normalization.ENV_VAR, env)
    from api.routers.audiobook import _render_chapter_cached
    from services.audiobook import Chapter, Span

    seen = []

    def synth(t, voice_id, speed=None):
        seen.append(t)
        return torch.zeros(1, 2400)

    chapter = Chapter(title="One", spans=[Span(voice_id=None, text=text)])
    wav_path, dur, was_cached, _seg_stats = _render_chapter_cached(
        chapter, synth, 24000, "stub-engine",
        lambda vid: {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None},
        str(tmp_path), None, language,
    )
    return seen, wav_path, was_cached


def test_longform_chapter_normalizes_spans(tmp_path, monkeypatch):
    seen, _, was_cached = _render_chapter(
        tmp_path, "Dr. Smith has 2 cats", "English", monkeypatch)
    assert not was_cached
    assert seen == ["Doctor Smith has two cats"]


def test_longform_cache_key_tracks_normalization_toggle(tmp_path, monkeypatch):
    # Normalization ON and OFF must not share a cached WAV: the key is built
    # over the normalized span text, so toggling re-renders.
    _, path_on, _ = _render_chapter(
        tmp_path, "Dr. Smith has 2 cats", "English", monkeypatch)
    seen_off, path_off, was_cached_off = _render_chapter(
        tmp_path, "Dr. Smith has 2 cats", "English", monkeypatch, env="0")
    assert path_on != path_off
    assert not was_cached_off
    assert seen_off == ["Dr. Smith has 2 cats"]  # raw text with the toggle off


@pytest.mark.parametrize("raw,expected", [
    ("-20~-10°C", "-20에서 -10°C"),
    ("0.5~1.0초", "0.5에서 1.0초"),
    ("1.20~30.5", "1.20에서 30.5"),
    ("+.5 ~ +1.0", "+.5에서 +1.0"),
    ("-0.5～+1.25", "-0.5에서 +1.25"),
])
def test_complete_signed_decimal_ranges(raw, expected):
    assert normalize_text(raw, "ko") == expected
    assert normalize_text(expected, "ko") == expected


@pytest.mark.parametrize("raw", [
    "20~30~40", "20 ~ 30 ~ 40", "20〜30～40", "20 ~ 30～40",
    "1.2.3~4.5", "1,000~2,000", "--20~-10", "20~++30",
    "AB-20~30CD", "1234567~20", "20~1234567", "1.1234567~2",
    "[20~30]", "[0.5~1.0]",
])
def test_malformed_or_protected_ranges_remain_unchanged(raw):
    assert normalize_text(raw, "ko") == raw


@pytest.mark.parametrize(("env", "expected"), [(None, True), ("1", True), ("off", False), ("0", False)])
def test_pronunciation_switch_is_env_only(monkeypatch, env, expected):
    from api.routers.generation import pronunciation_enabled
    import core.prefs as prefs_mod

    if env is None:
        monkeypatch.delenv("OMNIVOICE_PRONUNCIATION", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_PRONUNCIATION", env)
    # A never-written pref must not change the outcome.
    monkeypatch.setattr(prefs_mod, "get", lambda key, default=None: False)
    assert pronunciation_enabled() is expected


# ── Plain language-code resolver & range chain guard ────────────────────────

from services.text_normalization import _plain_lang_code, _speak_number_ranges  # noqa: E402


@pytest.mark.parametrize("language,expected", [
    ("Korean", "ko"),
    ("Mandarin Chinese", "zh"),
    ("ja", "ja"),
    ("ko-KR", "ko"),
    ("vi", "vi"),
    ("kk", "kz"),
    ("auto", None),
    ("AUTO", None),
    ("", None),
    ("   ", None),
    (None, None),
    ("!!", None),
])
def test_plain_lang_code(language, expected):
    assert _plain_lang_code(language) == expected


@pytest.mark.parametrize("raw", [
    "1~2~3",
    "1 ~ 2 ~ 3",
    "1~2 ~3",
    "1~2~ 3",
    "~1~2",
])
def test_range_chain_is_never_partially_rewritten(raw):
    assert _speak_number_ranges(raw, "ko") == raw


def test_range_rewrite_is_idempotent():
    once = normalize_text("3~5개", "Korean")
    assert normalize_text(once, "Korean") == once


def test_range_unknown_form_language_untouched():
    assert _speak_number_ranges("3~5", "vi") == "3~5"
    assert _speak_number_ranges("3~5", None) == "3~5"
