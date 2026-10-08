"""IndexTTS 2.5 must verify its bf16 claim before enabling bf16 (#2372).

On gfx1030 (RDNA2), ``torch.cuda.is_bf16_supported()`` returns True while
rocBLAS/Tensile segfaults on the first bfloat16 GEMM — below Python, so the
sidecar died with ``closed pipe mid-generate`` on every synthesis. The old
code trusted the claim. Now the claim is checked with a tiny GEMM in a child
process, where a segfault is only a non-zero exit, and a failed check makes
the model load with fp32 (the reporter's verified ``OMNIVOICE_INDEXTTS_FP16=0``
workaround, minus the manual step).
"""
from __future__ import annotations

import importlib
import pathlib
import sys
import types

import pytest


@pytest.fixture
def sidecar():
    """The IndexTTS sidecar script, loaded as a module by path (its imports
    are stdlib-only at load time, so this needs none of the engine's venv)."""
    path = pathlib.Path(__file__).resolve().parents[1] / "backend" / "engines" / "indextts" / "main.py"
    spec = importlib.util.spec_from_file_location("_indextts_bf16_under_test", path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


def _fake_torch(monkeypatch, *, hip, available=True, bf16=True):
    fake = types.ModuleType("torch")
    fake.cuda = types.SimpleNamespace(
        is_available=lambda: available,
        is_bf16_supported=lambda: bf16,
    )
    fake.version = types.SimpleNamespace(hip=hip)
    monkeypatch.setitem(sys.modules, "torch", fake)
    return fake


def _must_not_probe(sidecar):
    return lambda: pytest.fail("the probe must not run in this situation")


# ── the verdict ────────────────────────────────────────────────────────────

def test_a_rocm_claim_is_verified_in_a_child(sidecar, monkeypatch):
    """Fail-before: pre-#2372 the ROCm claim was returned as-is."""
    _fake_torch(monkeypatch, hip="6.4")
    calls = []
    monkeypatch.setattr(sidecar, "_bf16_probe", lambda: calls.append(1) or True)
    assert sidecar._torch_bf16_supported() is True
    assert calls, "a ROCm host must run the probe — trusting the claim is #2372"


def test_a_failed_probe_disables_bf16(sidecar, monkeypatch):
    _fake_torch(monkeypatch, hip="6.4")
    monkeypatch.setattr(sidecar, "_bf16_probe", lambda: False)
    assert sidecar._torch_bf16_supported() is False


def test_cuda_is_trusted_without_a_probe(sidecar, monkeypatch):
    """The probe costs a fresh torch import; CUDA's claim is accurate, so a
    CUDA host must not pay it."""
    _fake_torch(monkeypatch, hip=None)
    monkeypatch.setattr(sidecar, "_bf16_probe", _must_not_probe(sidecar))
    assert sidecar._torch_bf16_supported() is True


def test_no_gpu_or_no_bf16_never_reaches_the_probe(sidecar, monkeypatch):
    _fake_torch(monkeypatch, hip="6.4", available=False)
    monkeypatch.setattr(sidecar, "_bf16_probe", _must_not_probe(sidecar))
    assert sidecar._torch_bf16_supported() is False

    _fake_torch(monkeypatch, hip="6.4", available=True, bf16=False)
    assert sidecar._torch_bf16_supported() is False


# ── the probe ──────────────────────────────────────────────────────────────

def _stub_run(sidecar, monkeypatch, *, returncode=None, raises=None):
    if raises is not None:
        monkeypatch.setattr(sidecar.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(raises))
        return

    class _Proc:
        pass

    proc = _Proc()
    proc.returncode = returncode
    monkeypatch.setattr(sidecar.subprocess, "run", lambda *a, **k: proc)


def test_a_segfaulting_child_counts_as_failure(sidecar, monkeypatch):
    """-11 is SIGSEGV — the reporter's core dump, now contained in the child."""
    _stub_run(sidecar, monkeypatch, returncode=-11)
    assert sidecar._bf16_probe() is False


def test_a_surviving_child_counts_as_success(sidecar, monkeypatch):
    _stub_run(sidecar, monkeypatch, returncode=0)
    assert sidecar._bf16_probe() is True


def test_a_timeout_or_missing_interpreter_is_a_safe_false(sidecar, monkeypatch):
    import subprocess as sp
    _stub_run(sidecar, monkeypatch, raises=sp.TimeoutExpired(cmd="python", timeout=1))
    assert sidecar._bf16_probe() is False
    _stub_run(sidecar, monkeypatch, raises=OSError("no such interpreter"))
    assert sidecar._bf16_probe() is False


def test_the_probe_is_one_bf16_matmul_and_valid_python(sidecar):
    compile(sidecar._BF16_PROBE_CODE, "<probe>", "exec")
    assert "bfloat16" in sidecar._BF16_PROBE_CODE
    assert "matmul" in sidecar._BF16_PROBE_CODE
    # It must synchronize: a fire-and-forget GEMM could fault after exit 0.
    assert ".item()" in sidecar._BF16_PROBE_CODE


def test_the_probe_runs_the_sidecars_own_interpreter_and_is_bounded(sidecar, monkeypatch):
    seen = {}

    def _run(argv, **kwargs):
        seen["argv"] = argv
        seen["timeout"] = kwargs.get("timeout")
        class _Proc:
            returncode = 0
        return _Proc()

    monkeypatch.setattr(sidecar.subprocess, "run", _run)
    assert sidecar._bf16_probe() is True
    assert seen["argv"][:2] == [sidecar.sys.executable, "-c"]
    assert 0 < seen["timeout"] <= sidecar._BF16_PROBE_TIMEOUT_S


def _probe_timeout(sidecar, monkeypatch, value=None) -> float:
    """The timeout _bf16_probe passes to subprocess.run for an env value."""
    if value is None:
        monkeypatch.delenv("OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S", value)
    seen = {}

    def _run(argv, **kwargs):
        seen["timeout"] = kwargs.get("timeout")

        class _Proc:
            returncode = 0
        return _Proc()

    monkeypatch.setattr(sidecar.subprocess, "run", _run)
    sidecar._bf16_probe()
    return seen["timeout"]


@pytest.mark.parametrize("env", [None, "45.5", "30", "abc", "inf", "-5"])
def test_the_probe_budget_never_outlasts_the_parents_silence_deadline(sidecar, monkeypatch, env):
    """Fail-before/pass-after for the #2424 review: the fixed 120 s budget
    ignored OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S, so a tuned-down deadline
    (floor 30 s) could have the parent's watchdog kill the sidecar mid-probe
    — before _heartbeat starts and re-arms it."""
    timeout = _probe_timeout(sidecar, monkeypatch, env)
    deadline = sidecar._parent_recv_deadline_s()
    # The heartbeat waits a full period before its first frame, so the probe
    # must finish one margin inside the deadline, not merely under it.
    assert timeout == min(
        sidecar._BF16_PROBE_TIMEOUT_S,
        deadline - sidecar._PROBE_DEADLINE_MARGIN_S,
    )
    assert 0 < timeout < deadline


def test_the_sidecar_and_the_parent_read_the_same_recv_deadline(sidecar, monkeypatch):
    """The sidecar runs in its own venv and cannot import the parent's
    engine module, so the deadline parsing is duplicated — this pins the
    two to the same env contract (default 900, floor 30, garbage → 900)."""
    from engines.indextts import IndexTTS2Backend

    parent_deadline = IndexTTS2Backend.recv_timeout_s.fget
    for value in (None, "45.5", "30", "abc", "inf", "-5", "1e9"):
        if value is None:
            monkeypatch.delenv("OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S", raising=False)
        else:
            monkeypatch.setenv("OMNIVOICE_INDEXTTS_RECV_TIMEOUT_S", value)
        assert sidecar._parent_recv_deadline_s() == parent_deadline(None), value


# ── the wiring ─────────────────────────────────────────────────────────────

def test_the_verdict_reaches_the_model_constructor(sidecar, monkeypatch, tmp_path):
    """End-to-end: probe failure must land in IndexTTS2(use_bf16=...)."""
    _fake_torch(monkeypatch, hip="6.4")
    monkeypatch.setattr(sidecar, "_bf16_probe", lambda: False)
    kw = sidecar._model_init_kwargs(str(tmp_path), version="2.5", reduced_precision=True)
    assert kw["use_bf16"] is False, "#2372: a failed probe still enabled bf16"

    monkeypatch.setattr(sidecar, "_bf16_probe", lambda: True)
    kw = sidecar._model_init_kwargs(str(tmp_path), version="2.5", reduced_precision=True)
    assert kw["use_bf16"] is True


def test_disabling_reduced_precision_short_circuits_the_probe(sidecar, monkeypatch):
    """OMNIVOICE_INDEXTTS_FP16=0 keeps its meaning: no bf16, and no probe
    subprocess to decide it."""
    monkeypatch.setattr(sidecar, "_bf16_probe", _must_not_probe(sidecar))
    kw = sidecar._model_init_kwargs("/nowhere", version="2.5", reduced_precision=False)
    assert kw["use_bf16"] is False
