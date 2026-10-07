"""Longform cache keys carry the resolved synthesis language (#2524)."""
import os

import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

from api.routers.audiobook import _render_chapter_cached  # noqa: E402
from services.audiobook import Chapter, Span  # noqa: E402

_NO_VOICE = lambda _vid: {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}  # noqa: E731


def _chapter():
    return Chapter(title="C", spans=[Span(voice_id=None, text="Hello.", pause_ms_after=0)])


def _render(cache_dir, calls, tag, resolve=_NO_VOICE, language=None):
    def synth(text, voice_id, speed=None):
        calls.append(tag)
        return torch.full((2400,), 0.1)

    return _render_chapter_cached(
        _chapter(), synth, 24000, "eng", resolve, str(cache_dir), None, language,
    )


def test_language_change_misses_both_cache_layers(tmp_path):
    calls: list = []
    en_path, _, cached, _ = _render(tmp_path, calls, "en", language="English")
    assert not cached and calls == ["en"]

    fr_path, _, cached, _ = _render(tmp_path, calls, "fr", language="French")
    assert not cached and calls == ["en", "fr"]
    assert fr_path != en_path

    # Same language still reuses the chapter.
    _, _, cached, _ = _render(tmp_path, calls, "fr2", language="French")
    assert cached and calls == ["en", "fr"]

    # Chapter WAV gone: the inner segment cache must not replay English either.
    os.remove(fr_path)
    _render(tmp_path, calls, "fr3", language="French")
    assert calls == ["en", "fr"], "the French segment itself is cached"
    os.remove(fr_path)
    _render(tmp_path, calls, "de", language="German")
    assert calls[-1] == "de"


def test_autodetect_key_is_unchanged_by_the_language_fold(tmp_path):
    a = _render(tmp_path, [], "a", language=None)[0]
    b = _render(tmp_path, [], "b", language=None)[0]
    assert a == b
