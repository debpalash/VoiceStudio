"""Free-text dub directions must degrade, not abort the job (#4054)."""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")


def test_valid_tags_are_untouched():
    from services.instruct_safety import tag_safe_instruct

    assert tag_safe_instruct("female, british accent") == "female, british accent"
    assert tag_safe_instruct(None) is None
    assert tag_safe_instruct("") == ""


def test_free_text_is_dropped_and_mixed_keeps_tags():
    from services.instruct_safety import tag_safe_instruct

    assert tag_safe_instruct("slow") is None
    assert tag_safe_instruct("energetic") is None
    assert tag_safe_instruct("female, energetic") == "female"


def test_lists_are_sanitised_per_item():
    from services.instruct_safety import tag_safe_instruct

    from services.instruct_safety import tag_safe_instruct

    assert tag_safe_instruct(["slow", "male", None]) == [None, "male", None]


def test_omnivoice_model_accepts_what_the_guard_returns():
    from omnivoice.models.omnivoice import _resolve_instruct
    from services.instruct_safety import tag_safe_instruct

    for raw in ("slow", "female, energetic", "male, slow", "male, female",
                "male, female, whisper", "henan dialect, british accent",
                "男，女", "河南话, british accent"):
        safe = tag_safe_instruct(raw)
        _resolve_instruct(safe)  # must not raise


def test_conflicting_categories_keep_the_first():
    from services.instruct_safety import tag_safe_instruct

    assert tag_safe_instruct("male, female") == "male"


def test_cached_ref_helper_applies_the_guard():
    import pathlib

    src = (pathlib.Path(__file__).resolve().parents[1] / "backend/services/tts_backend.py").read_text(encoding="utf-8")
    body = src.split("def generate_with_cached_ref", 1)[1].split("\ndef ", 1)[0]
    assert "tag_safe_instruct" in body


def test_every_tags_backend_applies_the_guard():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    for rel in (
        "services/tts_backend.py",
        "engines/omnivoice_subprocess/__init__.py",
        "engines/omnivoice_gguf/backend.py",
    ):
        assert "tag_safe_instruct" in (root / rel).read_text(encoding="utf-8"), rel
