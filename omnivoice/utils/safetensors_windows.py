"""Windows: read safetensors weights with ``pread``, not through a memory map.

transformers opens every ``.safetensors`` checkpoint with
``safe_open(..., backend="mmap")`` for CPU loads. On Windows the first read
through that copy-on-write mapping can fault below Python (``0xC0000005`` in
``torch/storage.py`` while materializing weights, #4142). safetensors' ``pread``
backend reads the same bytes with ordinary file reads, so on Windows a CPU
``mmap`` request is served by ``pread`` instead.

Install it before transformers loads a model: :func:`install` wraps
``safetensors.safe_open`` and rebinds the name in every module that already
imported it (transformers binds it at import time). It is idempotent, does
nothing off Windows, honours ``OMNIVOICE_SAFETENSORS_PREAD=0``, and falls back
to the original call when an older safetensors has no ``backend`` option.
"""
from __future__ import annotations

import logging
import os
import sys

logger = logging.getLogger("omnivoice.safetensors")

OPT_OUT_ENV = "OMNIVOICE_SAFETENSORS_PREAD"
_OFF = {"0", "false", "no", "off"}
#: The wrapper and the function it replaced, while installed.
_state: dict = {}
# The two ways a safetensors without a pread backend refuses it: no keyword at
# all, or a backend list without "pread". Anything else is a real read error.
_UNSUPPORTED = ("unexpected keyword argument 'backend'", 'backend "pread" is invalid')


def _opted_out() -> bool:
    return os.environ.get(OPT_OUT_ENV, "").strip().lower() in _OFF


def _cpu(device) -> bool:
    # safetensors treats an omitted or None device as CPU.
    return device is None or str(device) == "cpu"


def install(platform: str | None = None) -> bool:
    """Serve CPU ``mmap`` reads with ``pread`` on Windows. True when active."""
    if (platform or sys.platform) != "win32" or _opted_out():
        return False
    try:
        import safetensors
    except ImportError:
        return False
    if _state and safetensors.safe_open is _state["wrapper"]:
        return True
    original = safetensors.safe_open

    def safe_open(filename, framework, device="cpu", *args, **kwargs):
        if kwargs.get("backend", "mmap") == "mmap" and _cpu(device):
            try:
                return original(filename, framework, device, *args, **{**kwargs, "backend": "pread"})
            except Exception as exc:  # noqa: BLE001 - only an unsupported backend falls back
                if not any(marker in str(exc) for marker in _UNSUPPORTED):
                    raise
                # Fixed text: the exception can name the file, and so a home path.
                logger.info("This safetensors has no pread backend; using memory-mapped reads")
                if _UNSUPPORTED[0] in str(exc):
                    # No backend keyword at all: an explicit "mmap" would fail the same way.
                    kwargs = {k: v for k, v in kwargs.items() if k != "backend"}
        return original(filename, framework, device, *args, **kwargs)

    safe_open.__doc__ = original.__doc__
    _rebind(original, safe_open)
    _state.update(original=original, wrapper=safe_open)
    logger.info("Windows reads safetensors weights with pread (%s=0 turns it off)", OPT_OUT_ENV)
    return True


def _rebind(old, new) -> None:
    for module in list(sys.modules.values()):
        try:
            if getattr(module, "safe_open", None) is old:
                module.safe_open = new
        except Exception:  # noqa: BLE001 - a lazy or read-only module: skip it
            continue


def uninstall() -> None:
    """Restore the original ``safe_open`` everywhere (tests)."""
    if _state:
        _rebind(_state["wrapper"], _state["original"])
        _state.clear()
