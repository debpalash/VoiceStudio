"""Which OmniVoice checkpoint the backend loads.

Lives apart from ``model_manager`` so the licence gate can read it without a
cyclic import.
"""
import logging
import os

logger = logging.getLogger(__name__)

_DEFAULT_OMNIVOICE_CHECKPOINT = "k2-fsa/OmniVoice"


def resolve_omnivoice_checkpoint() -> str:
    """Resolve the VoiceStudio TTS checkpoint from ``OMNIVOICE_MODEL``, self-healing
    a misconfigured value.

    A valid checkpoint is either a HuggingFace repo id (``org/repo`` — contains a
    ``/``) or an existing local directory. A bare token like ``"omnivoice"`` — a
    TTS *engine id* that leaked into ``OMNIVOICE_MODEL`` (e.g. a stale pref/env) —
    is neither, and would crash model load with *"omnivoice is not a local folder
    and is not a valid model identifier listed on huggingface.co/models"* (#693).
    Fall back to the default rather than 500 on every launch.
    """
    checkpoint = os.environ.get("OMNIVOICE_MODEL", "").strip()
    if not checkpoint:
        from services.local_model_paths import get
        local = get("tts", "omnivoice")
        if local:
            # Inventory must remain accessible to repair missing local folders.
            # The actual load path validates before opening weights.
            return local
        checkpoint = _DEFAULT_OMNIVOICE_CHECKPOINT
    if not checkpoint:
        return _DEFAULT_OMNIVOICE_CHECKPOINT
    if checkpoint == "test":
        # Test-suite sentinel (tests/conftest.py sets OMNIVOICE_MODEL=test):
        # return it verbatim. Self-healing it to the real default — "test"
        # is a bare token like the #693 engine-id leak — would hand every
        # app-booting test the real 2.3 GB k2-fsa/OmniVoice checkpoint,
        # which is exactly the download the sentinel exists to prevent. A
        # real load against "test" fails fast with a clear HF error instead.
        return checkpoint
    # Honor a HF repo id (org/repo) or an EXPLICIT local path (absolute, or with
    # a path separator). A bare token like "omnivoice" must NOT be treated as a
    # local dir even if a cwd-relative folder happens to share its name — that
    # is exactly the engine-id leak (#693), so self-heal to the default.
    if "/" in checkpoint or "\\" in checkpoint or os.path.isabs(checkpoint):
        return checkpoint
    logger.warning(
        "OMNIVOICE_MODEL=%r is not a HuggingFace repo id (org/repo) or a local "
        "path — falling back to %s (#693).",
        checkpoint, _DEFAULT_OMNIVOICE_CHECKPOINT,
    )
    return _DEFAULT_OMNIVOICE_CHECKPOINT
