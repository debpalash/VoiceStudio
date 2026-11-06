"""Keep ROCm 10 Torch and CT2's incompatible OpenMP runtimes apart."""
import weakref
from importlib import util
from threading import Lock

from .isolation_policy import isolation_required

_PROXY_LOCK = Lock()


def worker_device(parent_device=None):
    """Resolve Windows ROCm 10 without importing Torch in the inference child."""
    if not isolation_required():
        return None
    from core.device_caps import requested_device_override
    from .ct2 import hip_ct2_available

    if parent_device == "cpu" or requested_device_override() == "cpu":
        return "cpu"  # CPU selection must not run GPU verification.
    return "cuda" if hip_ct2_available() else "cpu"


def availability_status():
    """CPU availability must not import CT2 into the Torch parent either."""
    if not isolation_required():
        return None
    if not util.find_spec("faster_whisper"):
        return False, "faster-whisper is not installed"
    from services.performance_probe import isolated_asr_import_ready

    if not isolated_asr_import_ready():
        return False, "faster-whisper native import could not be verified in its isolated process"
    from core.device_caps import requested_device_override
    from .ct2 import availability_status as hip_status

    if requested_device_override() != "cpu" and (status := hip_status()) is not None:
        return status
    return True, "Windows ROCm 10 CTranslate2 runs in a separate inference process"


def _proxy(owner):
    """Concurrent first use must publish exactly one owned worker."""
    with _owner_lock(owner):
        return _proxy_locked(owner)


def _owner_lock(owner):
    """Allocate one lifecycle lock without serializing unrelated engine loads."""
    with _PROXY_LOCK:
        lock = getattr(owner, "_rocm_asr_lifecycle_lock", None)
        if lock is None:
            lock = Lock()
            owner._rocm_asr_lifecycle_lock = lock
        return lock


def _proxy_locked(owner):
    """Bind one model source and cleanup callback while creation is serialized."""
    proxy = getattr(owner, "_rocm_asr_proxy", None)
    if proxy is None:
        from services.asr_backend import _local_model_source, _ctranslate2_cuda_ok
        from .asr_proxy import RocmASRProxy

        device = "cuda" if _ctranslate2_cuda_ok() else "cpu"
        proxy = RocmASRProxy(_local_model_source(owner._model_name), device)
        owner._rocm_asr_proxy = proxy
        owner._rocm_asr_finalizer = weakref.finalize(owner, proxy.close, True)
        owner.runs_out_of_process = True
    return proxy


def ensure_isolated(owner):
    """Initialize owned transport only for the affected native runtime."""
    if not isolation_required():
        return False
    _proxy(owner).ensure_loaded()
    return True


def transcribe_isolated(owner, audio_path, **options):
    """Preserve the normal backend contract and record actual worker execution."""
    if not isolation_required():
        return None
    proxy = _proxy(owner)
    result = proxy.transcribe(audio_path, **options)
    with _owner_lock(owner):
        if getattr(owner, "_rocm_asr_proxy", None) is proxy:
            owner._device = proxy.execution_device
            owner._compute_type = proxy.execution_precision
            owner._fallback_reason = proxy.fallback_reason
            owner._fallback_stage = proxy.fallback_stage
    return result


def unload_isolated(owner):
    """Retire idle delegates without interrupting a job holding the worker."""
    lock = _owner_lock(owner)
    if not lock.acquire(blocking=False):
        return True  # Initialization is still publishing a coherent owner.
    try:
        return _unload_locked(owner)
    finally:
        lock.release()


def _unload_locked(owner):
    """Release the published proxy and its exact finalizer as one operation."""
    proxy = getattr(owner, "_rocm_asr_proxy", None)
    if proxy is not None:
        if not proxy.close():
            return True  # An active job still owns the sidecar.
        owner._rocm_asr_finalizer.detach()
        owner._rocm_asr_proxy = None
        owner._device = owner._compute_type = None
    return proxy is not None or isolation_required()


def evidence_loaded(owner):
    """Report live model ownership rather than an intended device choice."""
    proxy = getattr(owner, "_rocm_asr_proxy", None)
    return proxy is not None and proxy.execution_evidence_loaded()
