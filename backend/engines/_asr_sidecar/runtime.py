"""Lazy model loading; ROCm 10 inference never imports Torch here."""
import os
import sys

_model = None
_identity = None
_fallback_reason = _fallback_stage = None


def _ct_candidates(device):
    """Preserve explicit precision or use the established device fallback order."""
    override = os.environ.get("ASR_COMPUTE_TYPE")
    if override:
        return [override]
    return ["float16", "int8_float16", "int8"] if device == "cuda" else ["int8", "float32"]


def _is_ct_error(message):
    """Recognize precision failures that permit trying the next compute type."""
    low = message.lower()
    return "compute type" in low or "efficient float16" in low


def get_model(configuration=None):
    """Reuse the selected model or load it with verified device and CPU fallback."""
    global _model, _identity, _fallback_reason, _fallback_stage
    from core.windows_rocm.asr_isolation import worker_device, isolation_required

    configured = configuration is not None
    name = configuration["model"] if configured else (
        os.environ.get("ASR_MODEL_FW") or os.environ.get("ASR_MODEL_FASTER") or "large-v3")
    # Preserve the old worker cache/decision outside the affected runtime.
    safe_runtime = isolation_required()
    device = worker_device(configuration["device"] if configured else None) if safe_runtime else None
    identity = (name, device) if safe_runtime else (name,)
    if _model is not None and _identity == identity:
        return _model
    _model = None
    _fallback_reason = _fallback_stage = None
    from core.execstack import ensure_ctranslate2_loadable

    loadable, detail = ensure_ctranslate2_loadable()
    if not loadable:
        raise ImportError(f"CTranslate2 is unavailable to faster-whisper: {detail}")
    from core.windows_rocm.ct2 import prepare_import

    prepare_import()
    from faster_whisper import WhisperModel

    if device is None:
        try:
            from core.windows_rocm.ct2 import gpu_device_available
            device = "cuda" if gpu_device_available() else "cpu"
        except Exception:
            print("asr-sidecar: device probe failed — using cpu", file=sys.stderr, flush=True)
            device = "cpu"
    candidates = _ct_candidates(device)
    while True:
        last_error = None
        for compute in candidates:
            try:
                _model = WhisperModel(name, device=device, compute_type=compute)
                _identity = identity
                return _model
            except (ValueError, RuntimeError) as error:
                last_error = error
                if _is_ct_error(str(error)):
                    continue
                if safe_runtime and device == "cuda" and "out of memory" in str(error).lower():
                    break
                raise
        if safe_runtime and device == "cuda" and last_error and "out of memory" in str(last_error).lower():
            device, candidates = "cpu", _ct_candidates("cpu")
            _fallback_reason = "GPU memory was exhausted while loading the engine"
            _fallback_stage = "model_load"
            continue
        raise last_error


def execution_evidence():
    """Return the loaded native model's device, precision and fallback facts."""
    native = getattr(_model, "model", None)
    return {"device": getattr(native, "device", None),
            "compute_type": getattr(native, "compute_type", None),
            "fallback_reason": _fallback_reason, "fallback_stage": _fallback_stage}
