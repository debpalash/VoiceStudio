"""What each TTS engine can do with expressive direction (#3422).

Clients read :func:`expression_caps` through ``list_backends()`` to show
emotion and reaction controls only where the active engine honours them, and
to explain why they are unavailable elsewhere. Stdlib-only so sidecar engine
modules can import the tag sets without pulling in the registry.
"""
from __future__ import annotations

from typing import Literal

#: OmniVoice's natively tokenized reaction tags, in the order of
#: ``omnivoice.models.omnivoice._NONVERBAL_PATTERN`` (a test keeps them equal).
OMNIVOICE_INLINE_TAGS: tuple[str, ...] = (
    "[laughter]",
    "[sigh]",
    "[confirmation-en]",
    "[question-en]",
    "[question-ah]",
    "[question-oh]",
    "[question-ei]",
    "[question-yi]",
    "[surprise-ah]",
    "[surprise-oh]",
    "[surprise-wa]",
    "[surprise-yo]",
    "[dissatisfaction-hnn]",
)

#: CosyVoice's inline paralinguistic tags (its frontend's special tokens).
COSYVOICE_INLINE_TAGS: tuple[str, ...] = ("[breath]", "[laughter]")

EmotionControl = Literal["graded", "direction", "none"]


def emotion_control(cls) -> EmotionControl:
    """How an engine takes emotion: IndexTTS2's graded controls, a written
    delivery direction through ``instruct``, or not at all."""
    if getattr(cls, "supports_emotion", False):
        return "graded"
    if getattr(cls, "instruct_directs_delivery", False):
        return "direction"
    return "none"


def expression_caps(cls) -> dict:
    """Catalogue entry: ``{"emotion": EmotionControl, "tags": [str, ...]}``."""
    tags = getattr(cls, "inline_tags", ())
    return {
        "emotion": emotion_control(cls),
        "tags": [tag for tag in tags if isinstance(tag, str)] if isinstance(tags, tuple) else [],
    }


#: Strength for a span's text emotion when the request sets none: IndexTTS2's
#: recommended ceiling for text emotion, also Voice Cloning's default.
SPAN_EMOTION_ALPHA = 0.6


def apply_span_emotion(cls, call_extra: dict, instruct, emotion, emo_alpha=None):
    """Apply one span's ``[emotion:]`` the way the engine takes emotion.

    Shared by the Audiobook/Stories render and ``/generate`` (Stories previews
    and stems), and decided by :func:`emotion_control`, the declaration the UI
    gates on, so the editor, the preview and the render cannot disagree.
    Graded engines (IndexTTS2) get it as ``emo_text``, replacing any book-level
    emotion; direction engines (CosyVoice 3, VoxCPM2) get it appended to the
    voice's own ``instruct``; every other engine ignores it. Mutates
    ``call_extra`` and returns the ``instruct`` to send.
    """
    if not emotion:
        return instruct
    control = emotion_control(cls)
    if control == "graded":
        for key in ("emo_vector", "emo_audio"):
            call_extra.pop(key, None)
        call_extra["emo_text"] = emotion
        call_extra["use_emo_text"] = True
        call_extra["emo_alpha"] = emo_alpha if emo_alpha is not None else SPAN_EMOTION_ALPHA
    elif control == "direction":
        return ", ".join(part for part in ((instruct or "").strip(), emotion) if part)
    return instruct
