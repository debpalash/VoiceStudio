"""Cold-start trims in the OmniVoice sidecar: skip imports synthesis never uses
(~1.5 s of the omnivoice import) and read the installed weights while the
imports run, so a cold disk is not read serially afterwards."""
import os
import sys
import threading

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

from engines.omnivoice_subprocess import main as sidecar  # noqa: E402


def test_unused_heavy_imports_are_blocked_then_restored(monkeypatch):
    monkeypatch.delitem(sys.modules, "transformers", raising=False)
    for name in ("sklearn", "torchvision"):
        monkeypatch.delitem(sys.modules, name, raising=False)
    with sidecar._skip_unused_heavy_imports():
        assert sys.modules["sklearn"] is None and sys.modules["torchvision"] is None
        try:
            import sklearn  # noqa: F401
        except ImportError:
            pass
        else:
            raise AssertionError("sklearn imported while blocked")
    assert "sklearn" not in sys.modules and "torchvision" not in sys.modules


def test_an_already_imported_module_is_left_alone(monkeypatch):
    monkeypatch.delitem(sys.modules, "transformers", raising=False)
    marker = object()
    monkeypatch.setitem(sys.modules, "sklearn", marker)
    monkeypatch.delitem(sys.modules, "torchvision", raising=False)
    with sidecar._skip_unused_heavy_imports():
        assert sys.modules["sklearn"] is marker
    assert sys.modules["sklearn"] is marker


def test_blocked_modules_are_restored_when_the_import_fails(monkeypatch):
    monkeypatch.delitem(sys.modules, "transformers", raising=False)
    monkeypatch.delitem(sys.modules, "sklearn", raising=False)
    monkeypatch.delitem(sys.modules, "torchvision", raising=False)
    try:
        with sidecar._skip_unused_heavy_imports():
            raise RuntimeError("import failed")
    except RuntimeError:
        pass
    assert "sklearn" not in sys.modules and "torchvision" not in sys.modules


def test_nothing_is_blocked_once_transformers_is_imported(monkeypatch):
    # transformers caches which optional packages exist on first import; a stub
    # added afterwards breaks its torchvision import for the whole sidecar (#3551).
    monkeypatch.setitem(sys.modules, "transformers", object())
    monkeypatch.delitem(sys.modules, "sklearn", raising=False)
    monkeypatch.delitem(sys.modules, "torchvision", raising=False)
    with sidecar._skip_unused_heavy_imports():
        assert "sklearn" not in sys.modules and "torchvision" not in sys.modules


def test_transformers_imported_first_still_loads_omnivoice_dependencies():
    """End to end in a fresh interpreter: import transformers, then its
    torchvision-gated image utils inside the guard, as the omnivoice import does."""
    import importlib.util
    import subprocess

    if importlib.util.find_spec("transformers") is None or importlib.util.find_spec("torchvision") is None:
        import pytest

        pytest.skip("needs transformers and torchvision installed")
    backend = os.path.join(os.path.dirname(__file__), "..", "backend")
    code = (
        "import sys; sys.path.insert(0, sys.argv[1])\n"
        "import transformers\n"
        "from transformers.utils import is_torchvision_available\n"
        "if not is_torchvision_available():\n"
        "    sys.exit(3)\n"
        "from engines.omnivoice_subprocess import main as sidecar\n"
        "with sidecar._skip_unused_heavy_imports():\n"
        "    import transformers.image_utils\n"
    )
    result = subprocess.run([sys.executable, "-c", code, backend], capture_output=True, text=True, timeout=300)
    if result.returncode == 3:
        import pytest

        pytest.skip("transformers does not consider torchvision usable here")
    assert result.returncode == 0, result.stderr[-2000:]


def test_prefetch_reads_every_safetensors_file_and_nothing_else(tmp_path, monkeypatch):
    opened = []
    real_open = open

    def tracking_open(path, *args, **kwargs):
        opened.append(os.path.basename(str(path)))
        return real_open(path, *args, **kwargs)

    (tmp_path / "audio_tokenizer").mkdir()
    for rel in ("model.safetensors", "audio_tokenizer/model.safetensors", "config.json"):
        (tmp_path / rel).write_bytes(b"x" * 16)
    monkeypatch.setattr("builtins.open", tracking_open)
    sidecar._prefetch_weights(str(tmp_path), threading.Event())
    assert sorted(opened) == ["model.safetensors", "model.safetensors"]


def test_prefetch_stops_when_the_real_load_starts(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"x" * (24 * 1024 * 1024))
    stop = threading.Event()
    stop.set()
    sidecar._prefetch_weights(str(tmp_path), stop)  # returns without reading it all


def test_prefetch_never_raises_for_a_missing_checkpoint(tmp_path):
    sidecar._prefetch_weights(str(tmp_path / "does-not-exist"), threading.Event())
    sidecar._prefetch_weights("not-an-installed/repo-id", threading.Event())


def _recording_send(monkeypatch, frames, beats=2):
    """Record frames; `reached` is set once `beats` progress frames arrived."""
    reached = threading.Event()
    monkeypatch.setattr(sidecar, "_LOAD_HEARTBEAT_S", 0.01)

    def send(_stream, obj):
        frames.append(obj)
        if sum(1 for f in frames if f["op"] == "progress") >= beats:
            reached.set()

    monkeypatch.setattr(sidecar, "_send", send)
    return reached


def test_heartbeating_emits_progress_while_the_body_is_silent(monkeypatch):
    # #4228: any silent phase past the parent's 300s recv deadline kills the sidecar.
    sent = []
    reached = _recording_send(monkeypatch, sent)
    with sidecar._heartbeating(None, "generating"):
        assert reached.wait(5), "no heartbeat while the body was silent"
    assert all(f["op"] == "progress" and f["stage"] == "generating" for f in sent)
    # The beater is joined on exit: nothing is sent afterwards.
    count = len(sent)
    assert not [t for t in threading.enumerate() if t.name == "omnivoice-generating-heartbeat"]
    assert len(sent) == count


def test_synthesis_heartbeats_while_model_generate_runs(monkeypatch):
    frames = []
    reached = _recording_send(monkeypatch, frames)

    class Model:
        sampling_rate = 24000

        def generate(self, **_kw):
            assert reached.wait(5), "no heartbeat during generate"
            return [object()]

    monkeypatch.setattr(sidecar, "_tensor_to_pcm_b64", lambda *_a, **_k: ("", 24000, 0))
    monkeypatch.setattr(sidecar, "_load_model", lambda _s: Model())
    sidecar._handle_synthesize({"text": "hello"}, None)
    assert frames[-1]["op"] == "audio"
    assert any(f["op"] == "progress" and f["stage"] == "generating" for f in frames)


def test_cold_load_heartbeats_through_the_imports_before_from_pretrained(monkeypatch):
    # The imports and checkpoint resolution ahead of from_pretrained were silent.
    frames = []
    reached = _recording_send(monkeypatch, frames)
    monkeypatch.setattr(sidecar, "_model", None)

    def slow_construct(_stdout, progress):
        assert reached.wait(5), "no heartbeat during the cold load"
        return "model"

    monkeypatch.setattr(sidecar, "_construct_model", slow_construct)
    assert sidecar._load_model(None) == "model"
    assert any(f["stage"] == "loading_model" for f in frames)


def test_reference_transcription_heartbeats_with_an_uncredited_stage(monkeypatch):
    # The parent credits "loading_model" as cold-load time, so a stalled
    # transcription must use a stage it does not credit (#4228).
    from services import subprocess_backend as sb

    frames = []
    reached = _recording_send(monkeypatch, frames)
    monkeypatch.setattr(sidecar, "_ensure_backend_on_path", lambda: None)
    import services.asr_backend as asr
    import services.tts_backend as tts

    def transcribe(_ref, release_after=True):
        assert reached.wait(5), "no heartbeat during reference transcription"
        return "words"

    monkeypatch.setattr(asr, "transcribe_reference", transcribe)
    monkeypatch.setattr(tts, "reference_duration_s", lambda _r: 1.0)
    monkeypatch.setattr(tts, "has_unresolved_clone_prompt", lambda *_a: False)
    monkeypatch.setattr(sidecar, "_tensor_to_pcm_b64", lambda *_a, **_k: ("", 24000, 0))

    class Model:
        sampling_rate = 24000

        def generate(self, **_kw):
            return [object()]

    monkeypatch.setattr(sidecar, "_load_model", lambda _s: Model())
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "ref.wav"}, None)
    stages = {f["stage"] for f in frames if f["op"] == "progress"}
    assert "reference_asr" in stages
    assert "reference_asr" in sb._UNCREDITED_PROGRESS_STAGES
    assert "loading_model" not in sb._UNCREDITED_PROGRESS_STAGES
