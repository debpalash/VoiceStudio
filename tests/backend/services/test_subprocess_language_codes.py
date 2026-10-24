"""Sidecars receive the language code the engine validated, not a picker name.

Voice profiles and the language picker carry names such as "Portuguese".
``_check_language`` resolves those to "pt" for engines with a finite language
set, but ``SubprocessBackend.generate`` discarded the result and forwarded the
raw name. Sidecars that only understand codes then guessed from the first two
letters: Confucius4 received "po" (Portuguese), "sp" (Spanish), "ge" (German);
a CosyVoice 2 model lost its cross-lingual language tag for the same names.
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

from services.subprocess_backend import SubprocessBackend

REPO_ROOT = Path(__file__).resolve().parents[3]
ECHO_SCRIPT = REPO_ROOT / "backend" / "engines" / "_echo" / "main.py"
CONFUCIUS_SIDECAR = REPO_ROOT / "backend" / "engines" / "confucius4" / "main.py"


class FiniteEchoBackend(SubprocessBackend):
    """Echo sidecar behind Confucius4's declared language set."""

    id = "_echo_finite"
    display_name = "Echo (finite languages)"
    languages = [
        "zh", "en", "ja", "ko", "de", "fr", "es", "id", "it",
        "th", "pt", "ru", "ms", "vi",
    ]

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return self.languages

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return ECHO_SCRIPT


class MultiEchoBackend(FiniteEchoBackend):
    id = "_echo_multi"
    languages = ["multi"]


def _sent_languages(backend, monkeypatch, language):
    sent = []
    original = backend._send

    def record(msg):
        if msg.get("op") == "synthesize":
            sent.append(msg.get("language", "<absent>"))
        return original(msg)

    monkeypatch.setattr(backend, "_send", record)
    try:
        if language is None:
            backend.generate("hello")
        else:
            backend.generate("hello", language=language)
    finally:
        backend.shutdown()
    return sent


@pytest.mark.parametrize(
    ("language", "expected"),
    [
        ("Portuguese", "pt"),
        ("Spanish", "es"),
        ("German", "de"),
        ("Chinese", "zh"),
        ("Malay", "ms"),
        ("pt-BR", "pt"),
        ("es", "es"),
    ],
)
def test_finite_engine_sidecar_gets_the_validated_code(monkeypatch, language, expected):
    assert _sent_languages(FiniteEchoBackend(), monkeypatch, language) == [expected]


@pytest.mark.parametrize("language", [None, "Auto"])
def test_no_preference_still_reaches_the_sidecar_unchanged(monkeypatch, language):
    expected = "<absent>" if language is None else language
    assert _sent_languages(FiniteEchoBackend(), monkeypatch, language) == [expected]


def test_multi_engines_keep_the_callers_value(monkeypatch):
    # Open-ended engines translate names themselves (Supertonic-3, MOSS, dots).
    assert _sent_languages(MultiEchoBackend(), monkeypatch, "Portuguese") == ["Portuguese"]


def test_unsupported_language_is_still_refused_before_spawn(monkeypatch):
    backend = FiniteEchoBackend()
    monkeypatch.setattr(backend, "_spawn", lambda: pytest.fail("spawned"))
    with pytest.raises(ValueError, match="doesn't support language='Polish'"):
        backend.generate("hello", language="Polish")


def test_confucius4_sidecar_speaks_the_forwarded_code():
    spec = importlib.util.spec_from_file_location("_confucius4_sidecar", CONFUCIUS_SIDECAR)
    sidecar = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sidecar)
    # What the sidecar used to receive, and what it receives now.
    assert sidecar._normalize_language("Portuguese") == "po"
    assert sidecar._normalize_language("pt") == "pt"
