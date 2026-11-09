"""#1266: a torch lib path with whitespace can never survive inductor's linker.

Inductor passes the torch library directory to clang++/g++ as an unquoted
``-L`` flag, so a path containing a space splits into two arguments and the
compile dies with ``no such file or directory: 'Support/...'``. The bug is
inside PyTorch; what we control is not paying for a compile attempt that cannot
succeed and not flooding the log tail with its failure — which is how it turned
up in #1259, consuming a chunk of the captured crash output while not being the
actual fault.

Not hypothetical anywhere: macOS keeps app data under
``~/Library/Application Support/`` and a Windows profile is routinely
``C:/Users/First Last``.
"""
from __future__ import annotations

import types

import pytest


def _env(monkeypatch, *, torch_file, triton=True):
    """Bind should_torch_compile's dependencies to a controlled fake."""
    from services import engine_env

    monkeypatch.setattr(
        engine_env.importlib.util,
        "find_spec",
        lambda name: object() if (triton and name == "triton") else None,
    )
    monkeypatch.setattr(engine_env, "_compile_runtime_failure", None, raising=False)
    for name in ("TORCH_COMPILE_DISABLE", "TORCHDYNAMO_DISABLE", "TORCHINDUCTOR_DISABLE"):
        monkeypatch.delenv(name, raising=False)
    # Isolate the Settings gate: should_torch_compile() otherwise reads the real
    # settings_store, so a persisted perf.torch_compile_disabled=1 (or a missing
    # settings table) would decide these tests instead of the path logic.
    monkeypatch.setattr(
        engine_env, "settings_store", None, raising=False
    )
    # monkeypatch.setitem, never a raw assignment: a bare object dropped into
    # sys.modules leaks process-wide out of collection and breaks every later
    # import of the real module in a mixed run. backend/tests/
    # test_no_module_stubs.py exists to catch exactly that, and caught this.
    import sys as _sys
    import types as _types

    monkeypatch.setitem(
        _sys.modules,
        "services.settings_store",
        _types.SimpleNamespace(get_text=lambda *a, **k: "0"),
    )
    monkeypatch.setattr(
        engine_env, "_cuda_arch_supported_for_compile", lambda: (True, "")
    )
    monkeypatch.setitem(
        __import__("sys").modules, "torch", types.SimpleNamespace(__file__=torch_file)
    )
    return engine_env


CLEAN = "/opt/venv/lib/python3.11/site-packages/torch/__init__.py"
SPACED = "/Users/x/Library/Application Support/omnivoice/.venv/lib/torch/__init__.py"


def test_compile_allowed_on_a_clean_path(monkeypatch):
    ee = _env(monkeypatch, torch_file=CLEAN)
    assert ee.should_torch_compile("cuda") is True


def test_compile_skipped_when_the_torch_path_has_a_space(monkeypatch, caplog):
    """The regression: this used to attempt, fail in clang++, and log the wreck."""
    ee = _env(monkeypatch, torch_file=SPACED)
    with caplog.at_level("INFO", logger="omnivoice.engine_env"):
        assert ee.should_torch_compile("cuda") is False
    assert any("whitespace" in r.getMessage() for r in caplog.records), (
        "the skip must say WHY, or it is indistinguishable from the other skips"
    )


def test_force_env_still_overrides(monkeypatch, caplog):
    """Consistent with the arch gate: the user can insist — and is told."""
    ee = _env(monkeypatch, torch_file=SPACED)
    monkeypatch.setenv(ee._FORCE_COMPILE_ENV, "1")
    with caplog.at_level("WARNING", logger="omnivoice.engine_env"):
        assert ee.should_torch_compile("cuda") is True
    assert any(
        "forced" in r.getMessage().lower() for r in caplog.records
    ), "forcing past a known-broken path must be recorded, not silent"


def test_skip_message_names_the_escape_hatch(monkeypatch, caplog):
    """A skip the user cannot override is a dead end; the hint must be there."""
    ee = _env(monkeypatch, torch_file=SPACED)
    with caplog.at_level("INFO", logger="omnivoice.engine_env"):
        assert ee.should_torch_compile("cuda") is False
    assert any(ee._FORCE_COMPILE_ENV in r.getMessage() for r in caplog.records)


def test_home_directory_is_not_logged_verbatim(monkeypatch, caplog):
    """The reason string is logged and lands in pasted bug reports, so the path
    goes through the same redaction as every other user-facing failure text."""
    import pathlib as _pl

    home = str(_pl.Path.home())
    ee = _env(monkeypatch, torch_file=f"{home}/Library/Application Support/x/torch/__init__.py")
    with caplog.at_level("INFO", logger="omnivoice.engine_env"):
        ee.should_torch_compile("cuda")
    joined = " ".join(r.getMessage() for r in caplog.records)
    assert home not in joined, f"home directory leaked into the log: {joined}"


def test_unreadable_torch_path_is_not_a_reason_to_skip(monkeypatch):
    """No torch metadata means no evidence of a problem — fail open, matching
    the module's other probes."""
    ee = _env(monkeypatch, torch_file=None)
    assert ee.should_torch_compile("cuda") is True


@pytest.mark.parametrize("device", ["mps", "cpu", "xpu"])
def test_non_cuda_devices_are_unaffected(monkeypatch, device):
    """The device gate runs first and must keep short-circuiting."""
    ee = _env(monkeypatch, torch_file=SPACED)
    assert ee.should_torch_compile(device) is False
