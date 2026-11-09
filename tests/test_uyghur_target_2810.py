"""#2810: Uyghur as a dub translation target.

NLLB-200 has ``uig_Arab``; the dub picker sends the ISO code ``ug``, which the
alias table did not map (a bare two-letter code only matches FLORES codes by
their three-letter prefix), so NLLB refused Uyghur. LLM prompts must name the
language rather than pass a bare "ug". Whisper cannot transcribe Uyghur, so it
stays out of the dub source allow-list.
"""


def test_nllb_resolves_uyghur():
    from api.routers.dub_translate import _nllb_language
    assert _nllb_language("ug") == "uig_Arab"


def test_llm_prompts_name_uyghur():
    from api.routers.dub_translate import LANG_NAMES
    assert LANG_NAMES["ug"] == "Uyghur"


def test_uyghur_is_not_a_speech_recognition_source():
    from api.routers.dub_core import _DUB_SOURCE_LANG_CODES
    assert "ug" not in _DUB_SOURCE_LANG_CODES
