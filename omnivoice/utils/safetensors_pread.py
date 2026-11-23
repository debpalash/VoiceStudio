"""Windows: read safetensors with ``pread``, not the faulting ``mmap`` (#4142).

transformers 5.15.1 (the lock) opens every ``.safetensors`` weight file with
the ``mmap`` backend on Windows, and the first read through that copy-on-write
mapping can fault *below* Python — ``0xC0000005`` inside
``torch/storage.py``'s ``__getitem__``, reached from
``core_model_loading._materialize_copy`` — killing the process with no
traceback anything can classify (#4142, #3767, #3772, #4024). The weights on
disk are fine; the mapping is the problem: Windows charges a copy-on-write
view its whole size at map time (huggingface/transformers#48285), and
safetensors#693 reproduces the same fault with ``backend="mmap"`` and shows
``pread`` reading identical bytes without it.

Upstream fixed this in huggingface/transformers#48341 (released in 5.17.0):
Windows reads use the ``pread`` backend. This lock predates that release, so
``install_pread_backend()`` backports the same choice: it wraps
``safetensors.safe_open`` so cpu/mps reads request ``pread``, and rebinds the
name in every module that already imported it (transformers does
``from safetensors import safe_open`` at import time, so patching only the
package attribute would miss it).

Properties the tests pin:

- **Windows-only.** macOS/Linux keep ``mmap`` — exactly what upstream ships;
  the fault class does not exist there.
- **Idempotent.** ``backend/main.py`` and ``omnivoice/models/omnivoice.py``
  both call it in the same process.
- **Opt-out.** ``OMNIVOICE_SAFETENSORS_PREAD=0`` restores memory-mapped reads,
  mirroring the ``HF_DEACTIVATE_ASYNC_LOAD=0`` escape hatch.
- **Device-conservative.** Only cpu/mps reads are rewritten; a caller asking
  for a CUDA device keeps the backend it chose (nothing we have verified).

Call sites: ``backend/main.py`` (before transformers is imported, so every
parent-process weight read is covered) and ``omnivoice/models/omnivoice.py``
(every process that loads OmniVoice — the engine subprocess, scripts,
training — even when it never touches ``backend/main.py``).
"""

from __future__ import annotations

import os
import sys

ENV_OPT_OUT = "OMNIVOICE_SAFETENSORS_PREAD"
_OPT_OUT_VALUES = {"0", "false", "no", "off"}
# Marks our wrapper so a second install() sees the patch already in effect.
_MARKER = "_voicestudio_pread"


def _make_pread_safe_open(original):
    """Wrap a ``safe_open`` so cpu/mps reads go through ``pread``.

    Everything else — filename, framework, positional/keyword device, any
    explicitly chosen backend for a non-host device — passes through exactly
    as the caller wrote it.
    """

    def safe_open(filename, framework, *args, **kwargs):
        # device is the third parameter: passed by keyword everywhere in
        # transformers, but positionally legal (`safe_open(f, "pt", "cpu")`).
        device = kwargs.get("device", args[0] if args else None)
        if device is None or str(device) in ("cpu", "mps"):
            chosen = kwargs.pop("backend", None)
            try:
                return original(filename, framework, *args, backend="pread", **kwargs)
            except TypeError as exc:
                # A safetensors older than the lock has no ``backend``
                # parameter: read the way it always did rather than fail.
                if "backend" not in str(exc):
                    raise
            if chosen is not None:
                kwargs["backend"] = chosen
        return original(filename, framework, *args, **kwargs)

    safe_open.__name__ = getattr(original, "__name__", "safe_open")
    safe_open.__doc__ = getattr(original, "__doc__", None)
    setattr(safe_open, _MARKER, True)
    return safe_open


def install_pread_backend() -> bool:
    """Make Windows weight reads use ``pread`` instead of ``mmap``.

    Returns True when pread reads are in effect after the call (installed now
    or by an earlier call), False when skipped: not Windows, the
    ``OMNIVOICE_SAFETENSORS_PREAD=0`` opt-out, or safetensors itself is
    missing (transformers will then fail with its own classified error).
    Never raises — a stability shim must not kill the process that loads it.
    """
    if sys.platform != "win32":
        return False
    if os.environ.get(ENV_OPT_OUT, "").strip().lower() in _OPT_OUT_VALUES:
        return False
    try:
        import safetensors
    except (ImportError, OSError):  # noqa: PERF001 - a broken native ext must not kill startup
        return False
    current = safetensors.safe_open
    if getattr(current, _MARKER, False):
        return True  # already ours: idempotent, never double-wrap
    wrapper = _make_pread_safe_open(current)
    safetensors.safe_open = wrapper
    # Modules bind `from safetensors import safe_open` at import time;
    # rebind every name still pointing at the original (transformers.modeling_utils,
    # transformers.modeling_layers, safetensors.torch, …) so code imported
    # BEFORE this patch reads via pread too, and modules imported AFTER get
    # the wrapper from the package attribute above.
    for module in list(sys.modules.values()):
        namespace = getattr(module, "__dict__", None)
        if isinstance(namespace, dict) and namespace.get("safe_open") is current:
            try:
                module.safe_open = wrapper
            except (AttributeError, TypeError):  # exotic sys.modules entries
                pass
    return True
