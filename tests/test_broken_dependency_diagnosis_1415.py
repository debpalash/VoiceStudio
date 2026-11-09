"""A broken dependency is not a missing install, and must not be silent (#1415).

The reporter's backend logged this at startup::

    ModuleNotFoundError: Could not import module 'AutoFeatureExtractor'.
        Are this object's requirements defined correctly?
      … in _lazy_omnivoice
        from omnivoice.models.omnivoice import OmniVoice as _OV   # line 40

Line 40 is the *second* import — the one after the #564 source-tree fallback —
which proves the fallback fired. It should never have: nothing about
``sys.path`` was wrong. transformers' lazy module raises
``ModuleNotFoundError`` for any attribute whose backing import failed, so a
broken torch/torchaudio/transformers environment arrives wearing the exact
exception type that means "the editable install of omnivoice is missing". The
handler re-imported from the same broken environment, failed identically, and
logged that the *editable install* was broken — sending the reporter, and us,
after the wrong component.

The second half is that the failure was invisible. Preload is deliberately
non-fatal, and nothing else touches the model until the user generates — so
the app starts clean, reports itself healthy, and simply produces nothing.
"""
from __future__ import annotations

import pytest


@pytest.fixture
def mm():
    """Resolved at run time, not import time: a module-level import of an app
    module keeps mutable state in sys.modules across test boundaries."""
    import services.model_manager as _mm

    return _mm


# ── which failures are actually "omnivoice is missing"? ───────────────────

def _mnfe(msg: str, name: str | None = None) -> ModuleNotFoundError:
    exc = ModuleNotFoundError(msg)
    if name is not None:
        exc.name = name
    return exc


def test_a_missing_omnivoice_is_recognised(mm):
    assert mm._missing_module_is_omnivoice(_mnfe("No module named 'omnivoice'", "omnivoice"))


def test_a_missing_omnivoice_submodule_is_recognised(mm):
    assert mm._missing_module_is_omnivoice(
        _mnfe("No module named 'omnivoice.models'", "omnivoice.models")
    )


def test_the_transformers_lazy_attribute_error_is_not(mm):
    """The reported failure. It carries no `name` at all, because transformers
    raises it by hand rather than through the import machinery — which is
    precisely what distinguishes it from a real missing module."""
    exc = _mnfe(
        "Could not import module 'AutoFeatureExtractor'. "
        "Are this object's requirements defined correctly?"
    )
    assert exc.name is None
    assert not mm._missing_module_is_omnivoice(exc)


@pytest.mark.parametrize("name", ["torchaudio", "torchvision", "transformers", "torch"])
def test_a_missing_dependency_of_omnivoice_is_not(mm, name):
    """A torch/torchvision mismatch fails by name — a real module, just not
    ours. Putting the omnivoice source on sys.path cannot help."""
    assert not mm._missing_module_is_omnivoice(
        _mnfe(f"No module named '{name}'", name)
    )


def test_a_lookalike_package_name_is_not_ours(mm):
    assert not mm._missing_module_is_omnivoice(
        _mnfe("No module named 'omnivoiceX'", "omnivoiceX")
    )


# ── the fallback fires only when it can help ──────────────────────────────

def test_a_broken_dependency_does_not_trigger_the_source_fallback(mm, monkeypatch):
    """Fail-before: this called ensure_omnivoice_importable, re-imported from
    the same broken environment, and re-raised having logged the wrong cause."""
    monkeypatch.setattr(mm, "_OmniVoice", None, raising=False)
    called = []

    import core.omnivoice_path as omnivoice_path

    monkeypatch.setattr(
        omnivoice_path, "ensure_omnivoice_importable",
        lambda *a, **kw: called.append(a),
    )

    boom = _mnfe("Could not import module 'AutoFeatureExtractor'.")
    import builtins

    real_import = builtins.__import__

    def _fake_import(name, *a, **kw):
        if name == "omnivoice.models.omnivoice":
            raise boom
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ModuleNotFoundError) as caught:
        mm._lazy_omnivoice()
    assert caught.value is boom, "the original cause must survive untouched"
    assert called == [], (
        "the source-tree fallback ran for a failure it cannot fix — that is "
        "what blamed the editable install for a broken transformers (#1415)"
    )


def test_a_genuinely_missing_omnivoice_still_triggers_the_fallback(mm, monkeypatch):
    """No weakening of the #564 repair this guard sits in front of."""
    monkeypatch.setattr(mm, "_OmniVoice", None, raising=False)
    called = []

    import core.omnivoice_path as omnivoice_path

    monkeypatch.setattr(
        omnivoice_path, "ensure_omnivoice_importable",
        lambda *a, **kw: called.append(a),
    )

    import builtins

    real_import = builtins.__import__
    attempts = {"n": 0}

    def _fake_import(name, *a, **kw):
        if name == "omnivoice.models.omnivoice":
            attempts["n"] += 1
            raise _mnfe("No module named 'omnivoice'", "omnivoice")
        return real_import(name, *a, **kw)

    monkeypatch.setattr(builtins, "__import__", _fake_import)

    with pytest.raises(ModuleNotFoundError):
        mm._lazy_omnivoice()
    assert called, "the #564 source-tree fallback must still run for its own case"
    assert attempts["n"] == 2, "the import must be retried after the fallback"


# ── a non-fatal preload failure is still visible ──────────────────────────

@pytest.mark.usefixtures("cpu_host")
def test_a_failed_preload_shows_up_in_the_status(mm, monkeypatch):
    """Fail-before: the status stayed "idle" with no error, so the app looked
    healthy and produced nothing until a generation failed much later."""
    import asyncio

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "org/model")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda *a, **kw: True)

    async def _boom():
        raise _mnfe(
            "Could not import module 'AutoFeatureExtractor'. "
            "Are this object's requirements defined correctly?"
        )

    monkeypatch.setattr(mm, "_load_model_with_timeout", _boom)

    asyncio.run(mm.preload_model())

    status = mm.get_model_status()
    assert status["status"] != "ready"
    assert status.get("error"), "a failed preload left no trace on the status"
    # The classified remedy, not the raw lazy-attribute wording: this class is
    # TRANSFORMERS_IMPORT, whose hint names the reinstall that actually fixes it.
    assert "transformers" in status["error"].lower()


@pytest.mark.usefixtures("cpu_host")
def test_a_successful_preload_clears_a_previous_failure(mm, monkeypatch):
    """Driven through `preload_model()` rather than `_set_loading`, so it can
    only pass if the real success path actually clears the error a previous
    failure left behind (CodeRabbit)."""
    import asyncio

    mm._set_loading("failed", "something broke", error="something broke")
    assert mm.get_model_status().get("error")

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "org/model")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda *a, **kw: True)

    loaded = object()

    async def _ok():
        mm._set_loading("ready", "Model ready", progress=100)
        return loaded

    monkeypatch.setattr(mm, "_load_model_with_timeout", _ok)
    try:
        asyncio.run(mm.preload_model())
        assert mm.get_model_status()["status"] == "ready"
        assert not mm.get_model_status().get("error")
    finally:
        monkeypatch.setattr(mm, "model", None, raising=False)
        mm._set_loading("", "")


def test_unloaded_model_does_not_publish_stale_ready_detail(mm, monkeypatch):
    """An unload changes residency immediately; the previous successful load
    detail must not leave clients with both idle and ready states."""
    monkeypatch.setattr(mm, "model", None, raising=False)
    mm._set_loading("ready", "Model ready", progress=100)
    try:
        status = mm.get_model_status()
        assert status == {"loaded": False, "loading": False, "status": "idle"}
    finally:
        mm._set_loading("", "")


@pytest.mark.usefixtures("cpu_host")
def test_preload_oom_status_is_actionable_and_does_not_publish_allocator_details(
    mm, monkeypatch,
):
    import asyncio

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "org/model")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda *a, **kw: True)
    private = (
        "CUDA out of memory. Tried to allocate 1.14 GiB. "
        "Process 1031664 has 22.02 GiB memory in use. "
        "/home/alice/private/model.safetensors"
    )

    async def _boom():
        raise RuntimeError(private)

    monkeypatch.setattr(mm, "_load_model_with_timeout", _boom)
    asyncio.run(mm.preload_model())

    status = mm.get_model_status()
    assert status["sub_stage"] == "error"
    assert "Close other GPU-heavy apps" in status["error"]
    assert "1031664" not in status["error"]
    assert "/home/alice" not in status["error"]


@pytest.mark.usefixtures("cpu_host")
def test_the_fallback_detail_does_not_leak_a_path(mm, monkeypatch):
    """If building the classified failure itself fails, what lands on the
    status must not be the raw exception — those carry absolute paths, i.e.
    the user's account name, and this string is published through
    /model/status (CodeRabbit)."""
    import asyncio

    monkeypatch.setattr(mm, "model", None, raising=False)
    monkeypatch.setattr(mm, "resolve_omnivoice_checkpoint", lambda: "org/model")
    monkeypatch.setattr(mm, "_checkpoint_in_local_cache", lambda *a, **kw: True)

    secret = "/Users/somebody/models/OmniVoice/.venv/lib/x.py"

    async def _boom():
        raise RuntimeError(f"failed at {secret}")

    monkeypatch.setattr(mm, "_load_model_with_timeout", _boom)

    import core.failure as cf

    monkeypatch.setattr(
        cf, "build_failure",
        lambda *a, **kw: (_ for _ in ()).throw(ValueError("classifier broke")),
    )

    asyncio.run(mm.preload_model())
    error = mm.get_model_status().get("error", "")
    assert error, "the failure still has to be visible"
    assert secret not in error
    assert "somebody" not in error
    assert "Logs" in error
