"""cuDNN 8 side-load, and — the point of this module — a *probe* for it.

NVIDIA CUDA CTranslate2 builds (the engine under WhisperX and faster-whisper)
link against **cuDNN 8**, while newer PyTorch ships cuDNN 9. The two coexist:
the Rust bootstrap side-loads ``nvidia-cudnn-cu12==8.9.7.29`` into a
``cudnn8_compat/`` directory beside the venv's real site-packages (#827/#869),
and we ``ctypes``-preload those libraries at startup so CTranslate2's own
``LoadLibrary``/``dlopen`` finds them already resident in the process.

When that side-load is missing, CTranslate2 does not raise. It prints

    Could not locate cudnn_ops_infer64_8.dll. Please make sure it is in your
    library path!

and calls ``__fastfail`` — killing the **whole backend process** with
``0xC0000409`` (STATUS_STACK_BUFFER_OVERRUN, surfaced as exit code
``-1073740791``). Python never gets a frame, so there is no traceback, no
fallback, and nothing for the crash notice to classify. The desktop shell
restarts the backend, the user retries, and it dies again — #1371.

The bug was never that the preload could fail. It is that we **computed the
answer and threw it away**: the old inline preload in ``main.py`` silently
``pass``-ed on a missing directory and on every ``OSError``, then handed
control to a native library that treats the same condition as fatal. So this
module keeps the outcome and exposes :func:`ctranslate2_cudnn_status`, letting
``asr_backend`` route around a doomed engine *before* calling into it —
exactly how the CTranslate2 exec-stack failure (#692) is already handled.

Deliberately stdlib-only: ``engines/_asr_sidecar/main.py`` runs in a child
process with a clean import path and must not drag in the heavy ``services``
package to get this.
"""
from __future__ import annotations

import glob
import logging
import os
import sys

logger = logging.getLogger(__name__)

# The library CTranslate2 names in its own failure message. Probing the exact
# one the error cites keeps the diagnosis honest — and because the probe runs
# in the same process, through the same OS loader, a failure here is the same
# failure CTranslate2 is about to hit.
_SENTINEL_LIB = (
    "cudnn_ops_infer64_8.dll" if sys.platform == "win32" else "libcudnn_ops_infer.so.8"
)
_LIB_GLOB = "cudnn*64_8.dll" if sys.platform == "win32" else "libcudnn*.so.8"

_preloaded: bool = False
_status: tuple[bool, str] | None = None
# Handles from os.add_dll_directory (Windows). Held for the process lifetime —
# dropping one un-registers its directory. See preload().
_dll_dir_cookies: list = []


def _project_root() -> str:
    # backend/core/cudnn8.py → backend/core → backend → <project root>
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def compat_dirs() -> list[str]:
    """Every plausible ``cudnn8_compat`` location, most specific first.

    The old inline preload hardcoded ``<project root>/.venv``. That is right
    for the managed desktop install and wrong everywhere else — a differently
    named venv, ``uv run`` from a checkout, Docker, or a system Python all
    resolve to a directory that does not exist, so the preload silently did
    nothing and the process died later anyway. ``sys.prefix`` is where the
    *running* interpreter actually lives, so it is checked too.
    """
    pyver = f"python{sys.version_info.major}.{sys.version_info.minor}"
    if sys.platform == "win32":
        tail = (("Lib", "site-packages"), ("nvidia", "cudnn", "bin"))
    else:
        tail = (("lib", pyver, "site-packages"), ("nvidia", "cudnn", "lib"))
    site_tail, lib_tail = tail

    roots = [os.path.join(_project_root(), ".venv"), sys.prefix]
    out: list[str] = []
    for root in roots:
        path = os.path.join(root, *site_tail, "cudnn8_compat", *lib_tail)
        if path not in out:
            out.append(path)
    return out


def preload() -> None:
    """Load the side-loaded cuDNN 8 libraries into this process. Idempotent.

    Best-effort by design — a host with no side-load is not an error here, it
    is a fact :func:`ctranslate2_cudnn_status` reports later.
    """
    global _preloaded
    if _preloaded:
        return
    _preloaded = True
    if sys.platform == "darwin":  # no CUDA on macOS
        return

    import ctypes

    mode = 0 if sys.platform == "win32" else ctypes.RTLD_GLOBAL
    for lib_dir in compat_dirs():
        if not os.path.isdir(lib_dir):
            continue
        if sys.platform == "win32":
            # Make the directory searchable by *name* as well, so a CTranslate2
            # LoadLibrary for a dependent library we did not preload explicitly
            # still resolves instead of aborting the process.
            #
            # The returned cookie must be KEPT: it is a context manager whose
            # close/__del__ removes the directory again, so discarding it makes
            # the call a silent no-op — the exact failure this module exists to
            # prevent, reintroduced one line lower (CodeRabbit, #1401). Hold it
            # for the life of the process.
            try:
                _dll_dir_cookies.append(os.add_dll_directory(lib_dir))
            except (OSError, AttributeError) as e:
                logger.debug("cuDNN 8 DLL directory not added (%s): %s", lib_dir, e)
        for so in sorted(glob.glob(os.path.join(lib_dir, _LIB_GLOB))):
            try:
                ctypes.CDLL(so, mode=mode)
            except OSError as e:
                logger.debug("cuDNN 8 preload skipped %s: %s", so, e)
        return  # first directory that exists wins


def _torch_wants_cudnn8() -> tuple[bool, str]:
    """Whether CTranslate2 will reach for cuDNN 8 *on this host at all*.

    cuDNN is a CUDA library: with no CUDA device, CTranslate2 runs on the CPU
    and never touches it, so a missing side-load is harmless and must not
    disqualify the engine. ROCm is excluded because HIP CTranslate2 uses HIP
    libraries instead of NVIDIA cuDNN, while ordinary CTranslate2 wheels
    run on the CPU there. ``torch.cuda.is_available()`` is True on HIP too;
    checking it alone would silently downgrade ROCm users' ASR engine.
    Mirrors ``bootstrap.rs::classify_cuda_probe``, which likewise declines to
    install cuDNN 8 on HIP hosts (#124).
    """
    try:
        import torch
    except Exception as e:  # noqa: BLE001 — no torch: nothing will run anyway
        return False, f"torch unavailable ({type(e).__name__})"
    if getattr(getattr(torch, "version", None), "hip", None):
        return False, "ROCm build uses HIP, not NVIDIA CUDA"
    try:
        if not torch.cuda.is_available():
            return False, "no CUDA device — CTranslate2 runs on CPU"
    except Exception as e:  # noqa: BLE001
        return False, f"CUDA probe failed ({type(e).__name__})"
    return True, "CUDA device present"


def ctranslate2_cudnn_status() -> tuple[bool, str]:
    """``(usable, reason)`` — can CTranslate2 load cuDNN 8 in this process?

    Cached: the answer cannot change within a process, and it is consulted on
    every ASR engine selection.

    Conservative on purpose. A false negative costs a user WhisperX's forced
    alignment (and so lip-sync accuracy), so this returns True whenever cuDNN 8
    is not actually required, and only reports False for the one condition it
    can prove: the library the failure message names will not load, here, now,
    through the same loader CTranslate2 is about to use.
    """
    global _status
    if _status is not None:
        return _status
    _status = _compute_status()
    if not _status[0]:
        logger.warning("CTranslate2 ASR engines unavailable: %s", _status[1])
    return _status


def _try_load(name: str) -> None:
    """Load a shared library by *bare name*, raising OSError if it will not.

    Deliberately its own seam: this runs in the same process and through the
    same OS loader CTranslate2 is about to use, which is what makes the probe
    faithful — and it gives tests a way to simulate a missing library without
    replacing ``ctypes.CDLL`` process-wide (which would break torch's own load).
    """
    import ctypes

    ctypes.CDLL(name)


def _compute_status() -> tuple[bool, str]:
    # No separate macOS branch: there is no CUDA there, so `_torch_wants_cudnn8`
    # already answers "not required". One gate, testable on any platform.
    needed, why = _torch_wants_cudnn8()
    if not needed:
        return True, f"cuDNN 8 not required ({why})"

    preload()
    try:
        _try_load(_SENTINEL_LIB)
    except OSError as e:
        return False, (
            f"CUDA is active but {_SENTINEL_LIB} cannot be loaded ({e}). "
            f"WhisperX and faster-whisper are CTranslate2, which requires "
            f"cuDNN 8; loading it is not optional and its absence aborts the "
            f"backend process outright rather than raising (#1371). Reinstall "
            f"the compat libraries with "
            f"`uv pip install --target <venv>/{'Lib/site-packages' if sys.platform == 'win32' else 'lib/pythonX.Y/site-packages'}/cudnn8_compat "
            f"nvidia-cudnn-cu12==8.9.7.29`, or pin a non-CTranslate2 engine "
            f"with OMNIVOICE_ASR_BACKEND=pytorch-whisper."
        )
    return True, "cuDNN 8 loadable"


def reset_cache_for_tests() -> None:
    """Clear the memoised probe. Tests only."""
    global _status, _preloaded
    _status = None
    _preloaded = False
