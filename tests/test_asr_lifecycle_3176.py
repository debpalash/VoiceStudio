"""#3176 — lifecycle: honest loaded-ASR identity and safe independent unload.

The ``asr`` row of ``/model/loaded`` reported the ASR_MODEL env default as the
checkpoint, so the Settings pages matched no catalogue card and rendered no
Unload control while ~1.6 GB of reference-ASR VRAM sat pinned. And even with
``unloadable`` true there was no dispatcher branch, so pressing Unload answered
400 Unknown model id. Both fixed; TTS is never touched.
"""
import asyncio

import pytest

@pytest.fixture(autouse=True)
def app_modules():
    import importlib
    global ml, mm
    ml = importlib.import_module("services.model_lifecycle")
    mm = importlib.import_module("services.model_manager")


class _Pipe:
    def __init__(self, name_or_path=None, config_path=None, device="cuda:0"):
        self.device = device
        if name_or_path is not None:
            self.model = type("_M", (), {"name_or_path": name_or_path})()
        elif config_path is not None:
            self.model = type(
                "_M", (), {"config": type("_C", (), {"_name_or_path": config_path})()}
            )()
        else:
            self.model = object()


def _install_model(monkeypatch, model):
    monkeypatch.setattr(mm, "model", model)
    monkeypatch.setattr(mm, "_diar_pipeline", None)


# ── inventory honesty ────────────────────────────────────────────────────


def test_checkpoint_derived_from_local_snapshot_posix(monkeypatch):
    _install_model(monkeypatch, type("_Model", (), {
        "_asr_pipe": _Pipe(name_or_path=(
            "/cache/models--openai--whisper-large-v3-turbo/snapshots/abc123"),
        ),
    })())
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["asr"]["checkpoint"] == "openai/whisper-large-v3-turbo"


def test_checkpoint_derived_from_windows_snapshot_path(monkeypatch):
    _install_model(monkeypatch, type("_Model", (), {
        "_asr_pipe": _Pipe(config_path=(
            "C:\\Users\\u\\hf\\models--openai--whisper-large-v3-turbo"
            "\\snapshots\\abc123\\"),
        ),
    })())
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["asr"]["checkpoint"] == "openai/whisper-large-v3-turbo"


def test_checkpoint_reports_repo_id_when_pipeline_remembers_it(monkeypatch):
    _install_model(monkeypatch, type("_Model", (), {
        "_asr_pipe": _Pipe(name_or_path="openai/whisper-large-v3-turbo"),
    })())
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["asr"]["checkpoint"] == "openai/whisper-large-v3-turbo"


def test_unknown_model_is_reported_honestly_not_as_env_default(monkeypatch):
    # No name_or_path anywhere → 'unknown', and certainly not the ASR_MODEL
    # env default that used to be fabricated here.
    monkeypatch.setenv("ASR_MODEL", "Systran/faster-whisper-large-v3")
    _install_model(monkeypatch, type("_Model", (), {
        "_asr_pipe": _Pipe(),
    })())
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["asr"]["checkpoint"] == "unknown"


def test_asr_row_is_unloadable(monkeypatch):
    _install_model(monkeypatch, type("_Model", (), {
        "_asr_pipe": _Pipe(name_or_path="openai/whisper-large-v3-turbo"),
    })())
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["asr"]["unloadable"] is True


# ── independent unload ───────────────────────────────────────────────────


def _locked_unload(model_id):
    return asyncio.run(ml.unload(model_id))


def test_unload_asr_releases_pipe_but_not_tts(monkeypatch):
    freed = []

    class _Model:
        def __init__(self):
            self._asr_pipe = _Pipe(name_or_path="openai/whisper-large-v3-turbo")
            self.tts_weights = object()

        def unload_asr_model(self):
            self._asr_pipe = None
            return True

    model = _Model()
    _install_model(monkeypatch, model)
    monkeypatch.setattr(mm, "_model_lock", __import__("asyncio").Lock())
    monkeypatch.setattr(mm, "free_vram", lambda: freed.append(True))

    result = _locked_unload("asr")

    assert result == {"unloaded": "asr", "success": True}
    assert model._asr_pipe is None
    assert model.tts_weights is not None  # TTS model untouched
    assert freed == [True]


def test_unload_asr_idempotent(monkeypatch):
    _install_model(monkeypatch, type("_Model", (), {"_asr_pipe": None})())
    monkeypatch.setattr(mm, "_model_lock", __import__("asyncio").Lock())
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    result = _locked_unload("asr")
    assert result == {"unloaded": "asr", "success": False, "reason": "not loaded"}


def test_unload_asr_when_tts_not_loaded(monkeypatch):
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_model_lock", __import__("asyncio").Lock())
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    result = _locked_unload("asr")
    assert result["success"] is False


def test_unload_all_includes_asr(monkeypatch):
    calls = []

    async def _fake_unload(model_id):
        calls.append(model_id)
        return {"unloaded": model_id, "success": True}

    monkeypatch.setattr(ml, "unload", _fake_unload)
    result = asyncio.run(ml.unload_all())
    assert result["unloaded_all"] is True
    assert calls.index("asr") < calls.index("tts")


def test_unload_asr_preserves_host_transcriber(monkeypatch):
    """Unloading bundled Whisper keeps the independent selected-engine hook."""
    removed = []
    import services.asr_backend as ab_mod
    monkeypatch.setattr(ab_mod, "remove_reference_transcriber", lambda: removed.append(True))

    class _Model:
        def __init__(self):
            self._asr_pipe = _Pipe()
        def unload_asr_model(self):
            self._asr_pipe = None
            return True

    _install_model(monkeypatch, _Model())
    monkeypatch.setattr(mm, "_model_lock", __import__("asyncio").Lock())
    monkeypatch.setattr(mm, "free_vram", lambda: None)
    result = _locked_unload("asr")
    assert result["success"] is True
    assert removed == []


def test_unload_drops_last_pipeline_reference_before_allocator_cleanup(monkeypatch):
    import weakref
    model = type("Model", (), {})()
    model._asr_pipe = _Pipe()
    ref = weakref.ref(model._asr_pipe)
    _install_model(monkeypatch, model)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock())
    def free():
        assert ref() is None, "pipeline still alive during allocator cleanup"
    monkeypatch.setattr(mm, "free_vram", free)
    assert _locked_unload("asr")["success"] is True


def test_busy_asr_unload_keeps_pipeline_and_skips_allocator_cleanup(monkeypatch):
    model = type("Model", (), {"_asr_pipe": _Pipe(), "unload_asr_model": lambda self: False})()
    _install_model(monkeypatch, model)
    monkeypatch.setattr(mm, "_model_lock", asyncio.Lock())
    def free():
        pytest.fail("allocator cleanup must not run for a busy pipeline")
    monkeypatch.setattr(mm, "free_vram", free)
    assert _locked_unload("asr") == {"unloaded": "asr", "success": False, "reason": "not loaded or busy"}
    assert model._asr_pipe is not None
