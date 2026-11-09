"""Intel Arc on Windows: ``torch.xpu.mem_get_info()`` raises.

The B580 Windows driver answers the free-memory query with "doesn't support
querying the available free memory". transformers' ``caching_allocator_warmup``
calls it on every ``from_pretrained`` onto XPU, so every model load returned a
500. The fallback estimates free memory as ``total - reserved``.

It must be in place before ANY in-process load (so ``main.py`` installs it at
startup, not only ``get_best_device()``), and the isolated sidecars that select
XPU through ``torch.accelerator`` carry a copy — pinned here to behave the same.
"""
from __future__ import annotations

import ast
import importlib
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

_ROOT = Path(__file__).resolve().parent.parent
_DRIVER_ERROR = (
    "The device (Intel(R) Arc(TM) B580 Graphics) doesn't support querying the "
    "available free memory. You can file an issue at "
    "https://github.com/pytorch/pytorch/issues to help us prioritize its implementation."
)
_GIB = 1024**3


class _Device:
    def __init__(self, spec):
        self.type, _, idx = str(spec).partition(":")
        self.index = int(idx) if idx else None


def _fake_torch(*, supported: bool, total=12 * _GIB, reserved=0):
    calls = []

    def mem_get_info(device=None):
        calls.append(device)
        if not supported:
            raise RuntimeError(_DRIVER_ERROR)
        return 3 * _GIB, total

    xpu = SimpleNamespace(
        mem_get_info=mem_get_info,
        current_device=lambda: 0,
        get_device_properties=lambda idx: SimpleNamespace(total_memory=total),
        memory_reserved=lambda idx: reserved[idx] if isinstance(reserved, dict) else reserved,
    )
    return SimpleNamespace(xpu=xpu, device=_Device), calls


def _core_patch(torch):
    importlib.import_module("core.xpu_compat").patch_xpu_mem_get_info(torch)


def _sidecar_patch(engine):
    path = _ROOT / "backend" / "engines" / engine / "main.py"
    spec = importlib.util.spec_from_file_location(f"{engine}_xpu_shim_test", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod._patch_xpu_mem_get_info


@pytest.fixture(params=["core", "confucius4", "moss_tts_v15"])
def patch(request, monkeypatch):
    if request.param == "core":
        mod = importlib.import_module("core.xpu_compat")
        monkeypatch.setattr(mod, "_PATCHED", False)
        monkeypatch.setattr(mod, "_WARNED", False)
        return _core_patch
    return _sidecar_patch(request.param)


def test_unsupported_query_falls_back_to_total_minus_reserved(patch):
    torch, _ = _fake_torch(supported=False, reserved=4 * _GIB)
    patch(torch)
    assert torch.xpu.mem_get_info() == (8 * _GIB, 12 * _GIB)
    # transformers passes a device index; accelerate may pass "xpu:N".
    assert torch.xpu.mem_get_info(0) == (8 * _GIB, 12 * _GIB)
    assert torch.xpu.mem_get_info("xpu:0") == (8 * _GIB, 12 * _GIB)


def test_the_estimate_is_per_device_and_never_negative(patch):
    torch, _ = _fake_torch(supported=False, reserved={0: 0, 1: 20 * _GIB})
    patch(torch)
    assert torch.xpu.mem_get_info(1) == (0, 12 * _GIB)
    assert torch.xpu.mem_get_info("xpu:0") == (12 * _GIB, 12 * _GIB)


def test_a_working_query_is_passed_through(patch):
    torch, calls = _fake_torch(supported=True)
    patch(torch)
    assert torch.xpu.mem_get_info(0) == (3 * _GIB, 12 * _GIB)
    assert calls == [0]


def test_patching_twice_wraps_once(patch):
    torch, calls = _fake_torch(supported=True)
    patch(torch)
    patch(torch)
    torch.xpu.mem_get_info()
    assert calls == [None]


def test_no_xpu_backend_is_a_no_op(patch):
    torch = SimpleNamespace(device=_Device)
    patch(torch)
    assert not hasattr(torch, "xpu")


def test_the_backend_installs_it_before_any_model_can_load():
    """Startup, not first device pick: a model can load (ASR, diarization,
    preload) before anything calls get_best_device(). Installed after the
    first torch import and before model_manager is imported."""
    src = (_ROOT / "backend" / "main.py").read_text(encoding="utf-8")
    fn = next(
        node for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.FunctionDef) and node.name == "_phase_a_build_inner"
    )
    body = ast.get_source_segment(src, fn)
    torch_import = body.index("import torchaudio")
    install = body.index("xpu_compat.install()")
    model_manager = body.index("from services.model_manager import")
    assert torch_import < install < model_manager


@pytest.mark.parametrize("engine", ["confucius4", "moss_tts_v15"])
def test_sidecars_that_pick_xpu_apply_the_fallback(engine):
    """Both select XPU via torch.accelerator in an isolated venv; the copy
    must actually be applied on that branch."""
    src = (_ROOT / "backend" / "engines" / engine / "main.py").read_text(encoding="utf-8")
    load = src[src.index("def _load_model("):]
    assert 'if device == "xpu":\n        _patch_xpu_mem_get_info(torch)' in load
