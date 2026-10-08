"""
Dictation router — sherpa-onnx live-dictation engine.

Exposes the seven sherpa-onnx dictation models and the dictation prefs the
frontend dictation UI binds to.

    GET  /dictation/models   → the 7 models + install state (frontend model list)
    GET  /dictation/prefs    → { enabled, mode, model_id, prompt }
    POST /dictation/prefs    → persist any subset of those prefs

Install state reuses the same HF-cache check the model store uses, so a model
shown "installed" here is the same snapshot the backend will load.

Prefs are stored in the shared ``prefs.json`` store under the ``dictation.*``
namespace (``dictation.enabled``, ``dictation.mode``, ``dictation.model_id``,
``dictation.prompt``), mirroring how the ASR/TTS engine picks persist.

``prompt`` is an optional vocabulary hint (names, jargon, preferred script)
handed to the capture engine as Whisper's ``initial_prompt`` on the live
dictation socket and on ``/transcribe`` calls that pass ``dictation=true``.
Engines whose ``transcribe()`` does not declare it (sherpa, WhisperX, PyTorch
Whisper, CTC) never receive it, so an empty or unused prompt leaves dictation
unchanged. The silent-model rescue decodes without it.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Literal, Optional

from api.dependencies import require_local
from api.public_engine_metadata import public_unavailability
from core import prefs
from services import sherpa_dictation as sd

router = APIRouter()
logger = logging.getLogger("omnivoice.dictation")

# Pref keys (the binding contract — the frontend writes exactly these).
PREF_ENABLED = "dictation.enabled"
PREF_MODE = "dictation.mode"
PREF_MODEL_ID = "dictation.model_id"
PREF_PROMPT = "dictation.prompt"

_DEFAULT_ENABLED = True
_DEFAULT_MODE = "toggle"
_VALID_MODES = ("toggle", "hold")
# Whisper keeps only the last ~224 prompt tokens; the cap just bounds the
# stored value, it is not a token budget.
MAX_PROMPT_CHARS = 1000


def dictation_prompt() -> str:
    """The saved vocabulary prompt, or ``""`` when none is set. Bounded on
    read too, so a hand-edited prefs.json cannot push an engine past its
    own prompt limit."""
    value = prefs.get(PREF_PROMPT, "")
    return value.strip()[:MAX_PROMPT_CHARS] if isinstance(value, str) else ""


def dictation_transcribe_kwargs(backend) -> dict:
    """Decode kwargs the vocabulary prompt adds for ``backend`` — empty when
    no prompt is saved or the engine's ``transcribe()`` cannot take one."""
    from services.asr_backend import transcribe_request_kwargs

    return transcribe_request_kwargs(
        backend, {"initial_prompt": dictation_prompt() or None},
    )


def _read_prefs() -> dict:
    mid = prefs.get(PREF_MODEL_ID, sd.DEFAULT_MODEL_ID)
    if not sd.is_sherpa_model(mid):
        mid = sd.DEFAULT_MODEL_ID
    mode = prefs.get(PREF_MODE, _DEFAULT_MODE)
    if mode not in _VALID_MODES:
        mode = _DEFAULT_MODE
    return {
        "enabled": bool(prefs.get(PREF_ENABLED, _DEFAULT_ENABLED)),
        "mode": mode,
        "model_id": mid,
        "prompt": dictation_prompt(),
    }


@router.get("/dictation/models", dependencies=[Depends(require_local)])
def list_dictation_models():
    """The seven sherpa-onnx dictation models + install state.

    Each entry: id, repo_id, label, tag ("offline"|"streaming"), recommended,
    size_gb, languages, kind, and install state (installed/installing). The
    ``installed`` flag is computed from the same HF cache the model store reads,
    so it matches the model-store row state.
    """
    available, reason = sd.sherpa_available()
    out = []
    for spec in sd.list_specs():
        out.append({
            "id": spec.id,
            "repo_id": spec.repo_id,
            "label": spec.label,
            "tag": spec.tag,
            "recommended": spec.recommended,
            "size_gb": spec.size_gb,
            "languages": spec.languages,
            "kind": spec.kind,
            "installed": sd.is_installed(spec),
        })
    return {
        "models": out,
        "engine_available": available,
        "engine_reason": None if available else public_unavailability(reason),
        "default_model_id": sd.DEFAULT_MODEL_ID,
    }


@router.get("/dictation/readiness", dependencies=[Depends(require_local)])
def dictation_readiness(
    model_id: str | None = None,
    purpose: Literal["dictation", "transcribe"] = "dictation",
) -> dict:
    """Check a selected ASR path without loading or downloading weights."""
    from services.asr_backend import asr_model_missing_error

    missing = asr_model_missing_error(
        purpose=purpose,
        sherpa_model_id=(model_id or _read_prefs()["model_id"])
        if purpose == "dictation"
        else None,
    )
    return {"ready": missing is None, "missing": missing}


@router.get("/dictation/prefs", dependencies=[Depends(require_local)])
def get_dictation_prefs():
    return _read_prefs()


class DictationPrefsUpdate(BaseModel):
    enabled: Optional[bool] = None
    mode: Optional[str] = None
    model_id: Optional[str] = None
    prompt: Optional[str] = None


@router.post("/dictation/prefs", dependencies=[Depends(require_local)])
def set_dictation_prefs(req: DictationPrefsUpdate):
    """Persist any subset of the dictation prefs. Validates ``mode``,
    ``model_id`` and ``prompt`` so a bad value can't wedge the capture engine.
    An empty ``prompt`` clears it."""
    canonical = None
    if req.mode is not None:
        if req.mode not in _VALID_MODES:
            raise HTTPException(
                status_code=400,
                detail=f"mode must be one of {_VALID_MODES}",
            )
    prompt = req.prompt.strip() if req.prompt is not None else None
    if prompt is not None and len(prompt) > MAX_PROMPT_CHARS:
        raise HTTPException(
            status_code=400,
            detail=f"prompt must be at most {MAX_PROMPT_CHARS} characters",
        )
    if req.model_id is not None:
        if not sd.is_sherpa_model(req.model_id):
            raise HTTPException(
                status_code=400,
                detail=f"unknown dictation model_id {req.model_id!r}",
            )
        # Normalise to the canonical dictation id (accept repo_id too).
        canonical = sd.get_spec(req.model_id).id

    # Reset before persisting: if the capture service is unavailable, the
    # request fails without claiming that settings which are not active were
    # saved. A reset is safe even when a later preference write fails; the old
    # persisted selection is simply loaded again on next capture. The prompt
    # is read per transcription, so a prompt-only edit keeps the loaded model.
    if any(v is not None for v in (req.enabled, req.mode, req.model_id)):
        try:
            from services import asr_backend

            asr_backend._capture_backend = None
            asr_backend._capture_backend_key = None
        except Exception as exc:
            logger.warning("Dictation capture backend could not be reset")
            raise HTTPException(
                status_code=503,
                detail="Dictation settings could not be applied. Retry after the capture service is ready.",
            ) from exc

    if req.mode is not None:
        prefs.set_(PREF_MODE, req.mode)
    if canonical is not None:
        prefs.set_(PREF_MODEL_ID, canonical)
        # Explicitly choosing a model clears any auto-demotion: the user is in
        # charge, and a sherpa upgrade may well have fixed the decoder that
        # produced no text last time. Without this, a demoted model could never
        # be re-selected from the UI.
        sd.clear_demotion(canonical)
    if req.enabled is not None:
        prefs.set_(PREF_ENABLED, bool(req.enabled))
    if prompt is not None:
        prefs.set_(PREF_PROMPT, prompt)
    return _read_prefs()
