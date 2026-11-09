"""The optional speech smoke may use only caller-provided local assets."""
import importlib
import json
import subprocess
import wave

import pytest


def smoke():
    return importlib.import_module("core.windows_rocm.smoke")


def assets(tmp_path):
    model = tmp_path / "model"
    model.mkdir()
    for name in ("model.bin", "config.json", "tokenizer.json", "preprocessor_config.json"):
        (model / name).touch()
    wav = tmp_path / "speech.wav"
    with wave.open(str(wav), "wb") as stream:
        stream.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        stream.writeframes(b"\0\0" * 160)
    return model, wav


@pytest.mark.parametrize("model,wav", [("org/model", None), (None, "https://audio.wav"),
                                       ("https://model", "https://audio.wav")])
def test_remote_or_incomplete_inputs_refused(model, wav):
    with pytest.raises((ValueError, FileNotFoundError)):
        smoke().validate_inputs(model, wav)


def test_missing_tokenizer_refused_to_prevent_library_network_fallback(tmp_path):
    model, wav = assets(tmp_path)
    (model / "tokenizer.json").unlink()
    with pytest.raises(FileNotFoundError, match="tokenizer"):
        smoke().validate_inputs(model, wav)


def test_no_inputs_are_honestly_model_free(monkeypatch):
    module = smoke()
    monkeypatch.setattr(module, "verify_environment", lambda: {"ready": True, "scope": "model-free"})
    assert module.run_smoke()["scope"] == "model-free"


def test_local_inference_child_is_offline_bounded_and_report_has_no_transcript(monkeypatch, tmp_path):
    module = smoke()
    model, wav = assets(tmp_path)

    def run(command, **kwargs):
        assert command[-4:] == ["--model", str(model.resolve()), "--wav", str(wav.resolve())]
        assert kwargs["timeout"] == 180
        assert kwargs["env"]["HF_HUB_OFFLINE"] == "1"
        assert kwargs["env"]["TRANSFORMERS_OFFLINE"] == "1"
        report = {"ready": True, "scope": "local-ct2-asr", "segments": 1}
        return subprocess.CompletedProcess(command, 0, json.dumps(report), "")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module.run_smoke(model, wav) == {"ready": True, "scope": "local-ct2-asr", "segments": 1}


def test_ct2_segment_generator_is_consumed(monkeypatch, tmp_path):
    import sys
    from types import SimpleNamespace

    module = smoke()
    model, wav = assets(tmp_path)
    consumed = []
    monkeypatch.setattr(module, "probe", lambda: {"ready": True, "scope": "model-free"})

    class WhisperModel:
        def __init__(self, path, **kwargs):
            assert path == str(model)
            assert kwargs == {"device": "cuda", "device_index": 0, "compute_type": "float16", "local_files_only": True}
            self.model = SimpleNamespace(device="cuda", device_index=[0], compute_type="float16")

        def transcribe(self, path, **kwargs):
            assert path == str(wav)

            def segments():
                consumed.append(True)
                yield SimpleNamespace(text="private speech", start=0.0, end=1.0)

            return segments(), SimpleNamespace(duration=1.0)

    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=WhisperModel))
    report = module.infer_local(model, wav)
    assert consumed == [True]
    assert report == {"ready": True, "scope": "local-ct2-asr", "segments": 1}


def test_zero_segment_child_report_is_not_speech_proof(monkeypatch, tmp_path):
    module = smoke()
    model, wav = assets(tmp_path)
    report = {"ready": True, "scope": "local-ct2-asr", "segments": 0}
    monkeypatch.setattr(module.subprocess, "run", lambda *a, **kw:
                        subprocess.CompletedProcess(a, 0, json.dumps(report), ""))
    with pytest.raises(RuntimeError):
        module.run_smoke(model, wav)


@pytest.mark.parametrize("device,compute_type,text,duration", [
    ("cpu", "float16", "speech", 1.0), ("cuda", "float32", "speech", 1.0),
    ("cuda", "float16", "", 1.0), ("cuda", "float16", "speech", float("nan")),
])
def test_inference_refuses_fallback_empty_speech_or_invalid_results(monkeypatch, device, compute_type, text, duration):
    import sys
    from types import SimpleNamespace

    module = smoke()
    monkeypatch.setattr(module, "probe", dict)
    engine = SimpleNamespace(
        model=SimpleNamespace(device=device, device_index=[0], compute_type=compute_type),
        transcribe=lambda *a, **kw: (iter([SimpleNamespace(text=text, start=0, end=1)]),
                                     SimpleNamespace(duration=duration)),
    )
    monkeypatch.setitem(sys.modules, "faster_whisper", SimpleNamespace(WhisperModel=lambda *a, **kw: engine))
    with pytest.raises(RuntimeError):
        module.infer_local("local-model", "local.wav")
