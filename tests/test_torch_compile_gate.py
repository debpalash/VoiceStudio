"""plan-02 (#129/#65) — torch.compile must be gated on Triton availability.

`torch.compile(mode="reduce-overhead")` needs Triton at runtime; Triton has no
Windows build, so on Windows+CUDA the compile path failed and surfaced as a
confusing "OOM". The gate skips compile (→ eager) when Triton is absent or the
user disabled it. Tests force find_spec / the setting and assert the decision.

#278 adds two more gates: the GPU's compute capability must be in the torch
build's arch list (new archs like Blackwell sm_120 break Triton/Inductor
before upstream support lands — overridable via OMNIVOICE_FORCE_TORCH_COMPILE),
and a compile failure earlier in the session disables compile for the rest of
the process.
"""
from __future__ import annotations

import importlib.util
import sys
from types import SimpleNamespace

import pytest

from services import engine_env


class _FakeCuda:
    def __init__(self, capability=(9, 0), arch_list=("sm_80", "sm_86", "sm_90")):
        self._cap = tuple(capability)
        self._arch = list(arch_list)

    def is_available(self):
        return True

    def get_device_capability(self, idx=0):
        return self._cap

    def get_arch_list(self):
        return self._arch

    def get_device_name(self, idx=0):
        return "NVIDIA GeForce RTX (fake)"


@pytest.fixture
def compile_friendly_env(monkeypatch):
    """Triton present, setting off, no prior session failure, no force env."""
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: object())
    monkeypatch.setattr("services.settings_store.get_text", lambda key, default="0": "0")
    monkeypatch.setattr(engine_env, "_compile_runtime_failure", None)
    monkeypatch.delenv("OMNIVOICE_FORCE_TORCH_COMPILE", raising=False)
    for name in ("TORCH_COMPILE_DISABLE", "TORCHDYNAMO_DISABLE", "TORCHINDUCTOR_DISABLE"):
        monkeypatch.delenv(name, raising=False)


def _fake_torch(monkeypatch, cuda):
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=cuda))


def test_skips_when_device_not_cuda():
    assert engine_env.should_torch_compile("cpu") is False
    assert engine_env.should_torch_compile("mps") is False


def test_skips_when_triton_missing(monkeypatch):
    monkeypatch.setattr(
        importlib.util, "find_spec",
        lambda name: None if name == "triton" else object(),
    )
    assert engine_env.should_torch_compile("cuda") is False


def test_enabled_when_triton_present_and_not_disabled(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(9, 0)))
    assert engine_env.should_torch_compile("cuda") is True


def test_skips_when_disabled_in_settings(monkeypatch, compile_friendly_env):
    monkeypatch.setattr("services.settings_store.get_text", lambda key, default="0": "1")
    assert engine_env.should_torch_compile("cuda") is False


# ── #278: unsupported / unknown GPU architecture ─────────────────────────────


def test_skips_when_gpu_arch_not_in_torch_build(monkeypatch, compile_friendly_env):
    # RTX 5060 (Blackwell, sm_120) on a torch build that only knows ≤ sm_90.
    _fake_torch(monkeypatch, _FakeCuda(capability=(12, 0)))
    assert engine_env.should_torch_compile("cuda") is False


def test_force_env_overrides_arch_gate(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(12, 0)))
    monkeypatch.setenv("OMNIVOICE_FORCE_TORCH_COMPILE", "1")
    assert engine_env.should_torch_compile("cuda") is True


def test_supported_arch_still_compiles(monkeypatch, compile_friendly_env):
    # Backward compat: users whose torch.compile works keep it.
    _fake_torch(monkeypatch, _FakeCuda(capability=(8, 6)))
    assert engine_env.should_torch_compile("cuda") is True


def test_empty_arch_list_fails_open(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(12, 0), arch_list=()))
    assert engine_env.should_torch_compile("cuda") is True


def test_arch_probe_error_fails_open(monkeypatch, compile_friendly_env):
    class _BrokenCuda(_FakeCuda):
        def get_device_capability(self, idx=0):
            raise RuntimeError("driver error")

    _fake_torch(monkeypatch, _BrokenCuda())
    assert engine_env.should_torch_compile("cuda") is True


# ── #278: session-wide disable after a runtime failure ──────────────────────


def test_skips_after_runtime_failure_marked(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(9, 0)))
    assert engine_env.should_torch_compile("cuda") is True
    engine_env.mark_compile_runtime_failure("AssertionError: cudagraph_trees")
    assert engine_env.should_torch_compile("cuda") is False

@pytest.mark.parametrize(
    ("variable", "setting"),
    [
        ("TORCH_COMPILE_DISABLE", " TRUE "),
        ("TORCHDYNAMO_DISABLE", "on"),
        ("TORCHINDUCTOR_DISABLE", "1"),
    ],
)
def test_any_supported_environment_opt_out_wins(
    monkeypatch, compile_friendly_env, variable, setting
):
    _fake_torch(monkeypatch, _FakeCuda(capability=(8, 9)))
    monkeypatch.setenv(variable, setting)
    assert engine_env.compile_opt_out_name() == variable
    assert not engine_env.should_torch_compile("cuda")


def test_non_opt_out_values_leave_compilation_available(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(9, 0)))
    for variable, setting in zip(
        ("TORCH_COMPILE_DISABLE", "TORCHDYNAMO_DISABLE", "TORCHINDUCTOR_DISABLE"),
        ("off", "0", "not-enabled"),
    ):
        monkeypatch.setenv(variable, setting)
    assert engine_env.compile_opt_out_name() is None
    assert engine_env.should_torch_compile("cuda")


@pytest.mark.parametrize(
    ("setting", "opted_out"),
    [(value, True) for value in ("1", "true", "TRUE", "yes", "on")]
    + [(value, False) for value in ("", "0", "false", "no", "off")],
)
def test_primary_switch_parsing(monkeypatch, compile_friendly_env, setting, opted_out):
    _fake_torch(monkeypatch, _FakeCuda(capability=(9, 0)))
    monkeypatch.setenv("TORCH_COMPILE_DISABLE", setting)
    assert (not engine_env.should_torch_compile("cuda")) is opted_out


def test_compile_disable_env_outranks_force(monkeypatch, compile_friendly_env):
    _fake_torch(monkeypatch, _FakeCuda(capability=(12, 1)))
    monkeypatch.setenv("OMNIVOICE_FORCE_TORCH_COMPILE", "yes")
    monkeypatch.setenv("TORCHDYNAMO_DISABLE", "true")
    assert not engine_env.should_torch_compile("cuda")
