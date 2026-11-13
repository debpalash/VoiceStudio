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


def test_cached_ref_helper_applies_the_guard(monkeypatch):
    """Audiobook and /generate call generate_with_cached_ref directly: the model
    must receive the reduced instruct, not the free text."""
    import contextlib

    import services.model_manager as model_manager
    import services.tts_backend as tts_backend

    monkeypatch.setattr(tts_backend, "engine_in_use", lambda _backend: contextlib.nullcontext())
    monkeypatch.setattr(model_manager, "tts_inference", contextlib.nullcontext)
    monkeypatch.setattr(
        tts_backend, "omnivoice_inline_reference", lambda audio, text: (audio, text, None)
    )
    seen = {}

    class Model:
        def generate(self, **kw):
            seen.update(kw)
            return "audio"

    out = tts_backend.generate_with_cached_ref(
        Model(), ref_audio=None, ref_text=None, text="Hi", instruct="slow, male, energetic"
    )
    assert out == "audio"
    assert seen["instruct"] == "male"


def test_every_tags_backend_applies_the_guard():
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1] / "backend"
    for rel in (
        "services/tts_backend.py",
        "engines/omnivoice_subprocess/__init__.py",
        "engines/omnivoice_gguf/backend.py",
    ):
        assert "tag_safe_instruct" in (root / rel).read_text(encoding="utf-8"), rel


def test_long_whitespace_runs_stay_fast():
    import time

    from services.instruct_safety import tag_safe_instruct

    tag_safe_instruct("slow")  # import OmniVoice's resolver outside the timing
    started = time.perf_counter()
    # A whitespace run that does not touch a comma made the resolver's split
    # backtrack quadratically before the whitespace was collapsed.
    assert tag_safe_instruct("slow" + " " * 100_000 + "slow") is None
    assert time.perf_counter() - started < 2
