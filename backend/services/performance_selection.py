"""Compare preset plans with current user selections before applying them."""
from contextlib import contextmanager
from copy import deepcopy
import os

from core import prefs

_PREFS = {
    "asr": ("asr_backend", "asr_model_faster"),
    "tts": ("tts_backend",),
    "dictation": ("dictation.model_id", "dictation.enabled", "dictation.silent_models"),
    "translation": ("translation_backend",),
    "diarisation": ("diarization_backend",),
}
_ENV = {
    "asr": ("OMNIVOICE_ASR_BACKEND", "ASR_MODEL_FASTER", "ASR_MODEL_FW", "ASR_MODEL"),
    "tts": ("OMNIVOICE_TTS_BACKEND", "OMNIVOICE_MODEL", "OMNIVOICE_KITTENTTS_MODEL"),
    "dictation": ("OMNIVOICE_SHERPA_ASR_MODEL", "OMNIVOICE_SHERPA_ASR_PROVIDER"),
    "diarisation": ("OMNIVOICE_DIARIZATION_BACKEND", "OMNIVOICE_DIARIZATION_MODEL"),
}


def _read(family):
    """Copy only the selections that determine this family's planned result."""
    keys = (*_PREFS.get(family, ()), "compute_device", "performance_profile")
    environment = (*_ENV.get(family, ()), "OMNIVOICE_DEVICE", "OMNIVOICE_TORCH_VARIANT")
    return (deepcopy({key: prefs.get(key) for key in keys}),
            {key: os.environ.get(key) for key in environment})


def selection_snapshot(family):
    """Capture a coherent choice before inventory or child readiness can wait."""
    with prefs._MUTATE_LOCK:
        return family, _read(family)


@contextmanager
def unchanged_selection(snapshot):
    """Hold the preferences writer lock only for revalidation and application."""
    family, previous = snapshot
    with prefs._MUTATE_LOCK:
        yield _read(family) == previous
