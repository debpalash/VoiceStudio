"""#4142 — Windows weight reads must go through ``pread``, not the faulting ``mmap``.

transformers 5.15.1 (the lock) opens ``.safetensors`` with the ``mmap``
backend on Windows, and the first read through that copy-on-write view can
fault *below* Python — ``0xC0000005`` in ``torch/storage.py`` during
``core_model_loading._materialize_copy`` — killing the backend with no
traceback to classify (#4142, #3767, #3772). The weights on disk are fine;
the mapping is the problem (safetensors#693 reproduces the fault at this
exact frame with ``backend="mmap"`` and shows ``pread`` reading identical
bytes without it; huggingface/transformers#48341 ships that choice for
Windows in 5.17.0 — after our lock).

``omnivoice.utils.safetensors_pread`` backports it. These tests pin the
contract the fix depends on: the wrapper rewrites exactly the host reads
transformers performs, ``install()`` is Windows-only, honors the opt-out,
never double-wraps, and rebinds modules that imported ``safe_open`` before
the patch — plus both call sites (backend startup and ``from_pretrained``)
staying in place, which is what makes the whole thing fail-before.
"""
from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from omnivoice.utils.safetensors_pread import (
    ENV_OPT_OUT,
    _make_pread_safe_open,
    install_pread_backend,
)

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(autouse=True)
def _restore_safe_open_bindings():
    """``install()`` rebinds ``safe_open`` process-wide; put it back.

    Every module holding a ``safe_open`` at test start is restored on
    teardown, so the wrapper installed by one test can never leak into
    another — the suite must keep exercising stock safetensors.
    """
    snapshot = {
        name: mod.safe_open
        for name, mod in list(sys.modules.items())
        if isinstance(mod, types.ModuleType) and "safe_open" in vars(mod)
    }
    yield
    for name, original in snapshot.items():
        mod = sys.modules.get(name)
        if isinstance(mod, types.ModuleType) and "safe_open" in vars(mod):
            mod.safe_open = original


def _spy():
    seen: dict = {}

    def safe_open(filename, framework, *args, **kwargs):
        seen.update(kwargs)
        seen["args"] = args
        seen["filename"] = filename
        return "opened"

    return safe_open, seen


def test_wrapper_forces_pread_for_host_reads():
    """The #4142 path: transformers asks for mmap + cpu — we answer pread."""
    spy, seen = _spy()
    wrapped = _make_pread_safe_open(spy)

    # No device/backend at all (transformers.modeling_utils load_state_dict).
    assert wrapped("w.safetensors", "pt") == "opened"
    assert seen["backend"] == "pread"

    # Explicit mmap + cpu — the exact call from core_model_loading.
    wrapped("w.safetensors", framework="pt", device="cpu", backend="mmap")
    assert seen["backend"] == "pread"

    # Positional device is legal and still steers the backend.
    wrapped("w.safetensors", "pt", "cpu")
    assert seen["args"] == ("cpu",)
    assert seen["backend"] == "pread"


def test_wrapper_leaves_non_host_reads_alone():
    """Only cpu/mps are rewritten — a CUDA request keeps the backend it chose."""
    spy, seen = _spy()
    wrapped = _make_pread_safe_open(spy)

    wrapped("w.safetensors", framework="pt", device="cuda:0", backend="mmap")
    assert seen["backend"] == "mmap"

    # mps matches upstream's pread branch (huggingface/transformers#48341).
    wrapped("w.safetensors", framework="pt", device="mps")
    assert seen["backend"] == "pread"


def test_install_is_a_noop_off_windows(monkeypatch):
    """macOS/Linux keep mmap — the fault class exists only on Windows."""
    import safetensors

    original = safetensors.safe_open
    for platform in ("linux", "darwin"):
        monkeypatch.setattr(sys, "platform", platform)
        assert install_pread_backend() is False
        assert safetensors.safe_open is original


def test_install_honors_the_opt_out(monkeypatch):
    """``OMNIVOICE_SAFETENSORS_PREAD=0`` restores memory-mapped reads."""
    import safetensors

    original = safetensors.safe_open
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv(ENV_OPT_OUT, "0")
    assert install_pread_backend() is False
    assert safetensors.safe_open is original


def test_install_wraps_rebinds_and_never_double_wraps(monkeypatch):
    import safetensors

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(ENV_OPT_OUT, raising=False)
    original = safetensors.safe_open

    # A module that bound safe_open BEFORE the patch — transformers.modeling_utils
    # does exactly this at import time, and patching only safetensors.safe_open
    # would miss it.
    early = types.ModuleType("early_safe_open_binder")
    early.safe_open = original
    monkeypatch.setitem(sys.modules, "early_safe_open_binder", early)

    assert install_pread_backend() is True
    wrapper = safetensors.safe_open
    assert wrapper is not original
    assert getattr(wrapper, "_voicestudio_pread", False) is True
    assert early.safe_open is wrapper

    # Idempotent: backend/main.py and from_pretrained both call it.
    assert install_pread_backend() is True
    assert safetensors.safe_open is wrapper


def test_patched_open_reads_real_bytes(tmp_path, monkeypatch):
    """End-to-end: the wrapper + pread backend read a real file correctly,
    including the ``with``-statement shape transformers uses."""
    import numpy as np
    import safetensors
    import safetensors.numpy as st_np

    path = tmp_path / "tiny.safetensors"
    st_np.save_file({"w": np.array([1.5, -2.5], dtype=np.float32)}, str(path))

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.delenv(ENV_OPT_OUT, raising=False)
    assert install_pread_backend() is True

    with safetensors.safe_open(str(path), "numpy") as f:
        assert f.get_tensor("w").tolist() == [1.5, -2.5]


def test_backend_main_installs_the_guard_after_env_load():
    """The parent process installs at startup: after .env (so the opt-out is
    honored) and before transformers can bind the stock ``safe_open``."""
    main_src = (ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    env_load = main_src.index("_load_startup_env(_discovered_env")
    install = main_src.index("_install_pread_backend()")
    assert env_load < install
    assert install < main_src.index("from services.hf_auth import")


def test_from_pretrained_installs_the_guard_before_the_load():
    """The crash site itself: every OmniVoice load — resident backend, engine
    subprocess, scripts — installs before super().from_pretrained reads."""
    src = (ROOT / "omnivoice" / "models" / "omnivoice.py").read_text(encoding="utf-8")
    install = src.index("install_pread_backend()")
    load = src.index("model = super().from_pretrained(")
    assert install < load
