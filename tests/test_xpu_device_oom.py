"""A device OOM must be recognised everywhere, and must give its memory back.

Two defects, seen on an Intel Arc B580 (Windows) but not specific to it:

1. Intel's driver reports a device OOM as ``UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY``
   (torch's XPU allocator says "XPU out of memory"). The dub OOM retry, the
   ASR batch ladder, the error journal and the failure taxonomy each kept their
   own signature list and none knew these spellings.
2. Every OOM handler flushed the allocator *inside* ``except`` while the
   exception's traceback still pinned the frames holding the failed request's
   activations, so nothing was freed: one OOM left ~11.5 GB held and every
   later generation failed the same way until a restart.
"""
from __future__ import annotations

import gc
import importlib
import weakref

import pytest

_XPU_OOMS = [
    "level_zero backend failed with error: 39 (UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY)",
    "XPU out of memory. Tried to allocate 1.50 GiB (GPU 0; 11.60 GiB total capacity)",
]


@pytest.mark.parametrize("message", _XPU_OOMS)
def test_every_oom_classifier_recognises_xpu(message):
    failure = importlib.import_module("core.failure")
    generation = importlib.import_module("api.routers.generation")
    journal = importlib.import_module("core.error_journal")
    asr = importlib.import_module("services.asr_backend")
    sentinel = importlib.import_module("core.run_sentinel")

    err = RuntimeError(message)
    assert failure.is_gpu_oom(err)
    assert failure.classify(message) == "GPU_OOM"
    assert generation._is_oom_failure(err)
    assert journal.classify_exception(err) == "GPU_OOM"
    assert asr.PyTorchWhisperBackend._is_oom(err)
    assert any(marker in message.lower() for marker in sentinel._ACTIONABLE_LOG_MARKERS)


def test_the_journal_and_the_taxonomy_cannot_drift():
    """The journal keeps literals so it never imports the taxonomy; this keeps
    them equal (the host-OOM twin is pinned in test_host_memory_failure_2462)."""
    failure = importlib.import_module("core.failure")
    journal = importlib.import_module("core.error_journal")
    assert set(dict(journal._CLASS_RULES)["GPU_OOM"]) == set(failure._GPU_OOM_SIGNATURES)


class _Activations:
    """Stands in for the tensors a failed forward pass leaves in its frames."""


def _forward(refs, message="UR_RESULT_ERROR_OUT_OF_DEVICE_MEMORY"):
    activations = _Activations()
    refs.append(weakref.ref(activations))
    raise RuntimeError(message)


def _wrapped_forward(refs):
    try:
        _forward(refs)
    except RuntimeError as inner:
        raise RuntimeError("generation failed") from inner


@pytest.fixture
def mm(monkeypatch):
    mod = importlib.import_module("services.model_manager")
    flushed = []
    monkeypatch.setattr(mod, "free_vram", lambda: flushed.append(True))
    mod.flushed = flushed
    return mod


@pytest.mark.parametrize("forward", [_forward, _wrapped_forward])
def test_release_after_oom_frees_what_the_traceback_pinned(mm, forward):
    refs = []
    try:
        forward(refs)
    except RuntimeError as e:
        gc.collect()  # what every handler did before: frees nothing
        assert refs[0]() is not None, "the traceback pins the activations"
        mm.release_after_oom(e)
        assert refs[0]() is None, "activations must be gone before the flush"
    assert mm.flushed == [True]


def test_release_after_oom_never_raises(mm, monkeypatch):
    def boom():
        raise RuntimeError("driver gone")

    monkeypatch.setattr(mm, "free_vram", boom)
    try:
        _forward([])
    except RuntimeError as e:
        mm.release_after_oom(e)  # must not raise


def test_generate_oom_path_releases_the_failed_request(mm, monkeypatch):
    generation = importlib.import_module("api.routers.generation")
    monkeypatch.setattr(mm, "release_device_cache", lambda **_k: None)
    released = []
    monkeypatch.setattr(mm, "release_after_oom", released.append)
    try:
        _forward([])
    except RuntimeError as e:
        with pytest.raises(RuntimeError, match="ran out of memory"):
            generation._oom_friendly_reraise(e)
        assert released == [e]


def test_dub_retry_recognises_xpu_oom_and_releases_it(mm, monkeypatch):
    dub = importlib.import_module("api.routers.dub_generate")
    released = []
    monkeypatch.setattr(mm, "release_after_oom", released.append)
    err = RuntimeError(_XPU_OOMS[0])
    assert dub._prepare_oom_retry(err) is True
    assert released == [err]
