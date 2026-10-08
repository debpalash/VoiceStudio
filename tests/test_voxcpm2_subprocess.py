"""VoxCPM2 from its own venv: the sidecar, and the switch to it.

The sidecar runs in a venv that holds only voxcpm and its dependencies, so it
must import nothing from the app, and it must drive the model exactly as the
in-process VoxCPM2Backend does. These tests run it with a fake `voxcpm`.
"""
import importlib.util
import io
import json
import re
import struct
import sys
import threading
import types
from pathlib import Path

import numpy as np
import pytest

_MAIN = Path(__file__).resolve().parents[1] / "backend/engines/voxcpm2_subprocess/main.py"


def _load_sidecar(monkeypatch, calls):
    class FakeModel:
        sample_rate = 48000

        def generate(self, **kw):
            calls.append(kw)
            return np.zeros(480, dtype=np.float32)

    class VoxCPM:
        @classmethod
        def from_pretrained(cls, checkpoint, **kw):
            calls.append({"from_pretrained": checkpoint, **kw})
            return FakeModel()

    fake = types.ModuleType("voxcpm")
    fake.VoxCPM = VoxCPM
    monkeypatch.setitem(sys.modules, "voxcpm", fake)
    spec = importlib.util.spec_from_file_location("_voxcpm2_sidecar_under_test", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _frames(buf):
    data, out, i = buf.getvalue(), [], 0
    while i < len(data):
        (n,) = struct.unpack("!I", data[i:i + 4])
        out.append(json.loads(data[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def test_voice_design_uses_native_control_prefix(monkeypatch):
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    out = io.BytesIO()

    sidecar._handle_synthesize({"op": "synthesize", "text": "hello", "description": "warm, low"}, out)

    assert calls[-1] == {
        "text": "(warm, low)hello",
        "reference_wav_path": None,
        "prompt_wav_path": None,
        "prompt_text": None,
        "cfg_value": 2.0,
        "max_len": 256,
        "inference_timesteps": 10,
        "retry_badcase": False,
        "retry_badcase_max_times": 1,
    }
    audio = _frames(out)[-1]
    assert audio["op"] == "audio"
    assert audio["sample_rate"] == 48000 and audio["n_samples"] == 480


def test_style_clone_does_not_use_continuation_prompt(monkeypatch, tmp_path):
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")

    sidecar._handle_synthesize(
        {"text": "hi", "ref_audio": str(ref), "ref_text": "hello there", "instruct": "calm",
         "guidance_scale": 3.0, "num_step": 12},
        io.BytesIO(),
    )

    assert calls[-1] == {
        "text": "(calm)hi",
        "cfg_value": 3.0,
        "max_len": 256,
        "inference_timesteps": 12,
        "reference_wav_path": str(ref),
        "prompt_wav_path": None,
        "prompt_text": None,
        "retry_badcase": False,
        "retry_badcase_max_times": 1,
    }


def test_generation_work_is_bounded_for_short_and_long_text(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [])

    short = sidecar.generation_kwargs("hello", num_step="invalid")
    assert short["max_len"] == 256
    assert short["inference_timesteps"] == 10
    assert short["retry_badcase"] is False
    assert short["retry_badcase_max_times"] == 1

    long = sidecar.generation_kwargs("x" * 2000, num_step=100)
    assert long["max_len"] == 4096
    # The requested quality preset is honoured up to the engine's 64-step range.
    assert sidecar.generation_kwargs("hi", num_step=32)["inference_timesteps"] == 32
    assert long["inference_timesteps"] == 64

    negative = sidecar.generation_kwargs("hello", num_step=-10)
    assert negative["inference_timesteps"] == 1


def test_a_reference_without_its_transcript_is_not_a_prompt(monkeypatch, tmp_path):
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"x")
    sidecar._handle_synthesize({"text": "hi", "ref_audio": str(ref)}, io.BytesIO())
    assert calls[-1]["reference_wav_path"] == str(ref)
    assert calls[-1]["prompt_wav_path"] is None


def test_loads_the_configured_checkpoint_without_the_denoiser(monkeypatch):
    calls = []
    monkeypatch.setenv("OMNIVOICE_VOXCPM_MODEL", "local/ckpt")
    sidecar = _load_sidecar(monkeypatch, calls)
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert calls[0] == {"from_pretrained": "local/ckpt", "load_denoiser": False, "optimize": False}


@pytest.mark.parametrize(
    ("value", "expected"),
    [(None, 900.0), ("bad", 900.0), ("nan", 900.0), ("inf", 900.0), ("1", 30.0), ("240", 240.0)],
)
def test_sidecar_receive_timeout_is_bounded(monkeypatch, value, expected):
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    if value is None:
        monkeypatch.delenv("OMNIVOICE_VOXCPM2_RECV_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_VOXCPM2_RECV_TIMEOUT_S", value)
    assert VoxCPM2SubprocessBackend().recv_timeout_s == expected


def test_rejects_a_url_reference(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [])
    with pytest.raises(ValueError, match="local file path"):
        sidecar._handle_synthesize({"text": "hi", "ref_audio": "https://x.test/y.wav"}, io.BytesIO())


def test_retries_a_transient_download_failure_only(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [])
    monkeypatch.setattr(sidecar.time, "sleep", lambda s: None)
    attempts = []

    def flaky():
        attempts.append(1)
        if len(attempts) < 3:
            raise OSError("peer closed connection without sending complete message body")
        return "model"

    assert sidecar._with_retries(flaky) == "model"
    assert len(attempts) == 3

    broken_calls = []

    def broken():
        broken_calls.append(1)
        raise ValueError("bad config")

    with pytest.raises(ValueError):
        sidecar._with_retries(broken)
    assert len(broken_calls) == 1  # a permanent error is not retried


def test_the_sidecar_imports_nothing_from_the_app():
    src = _MAIN.read_text(encoding="utf-8")
    for name in ("services", "core", "engines", "api", "backend", "utils"):
        assert not re.search(rf"^\s*(from|import) {name}\b", src, re.M), name


def test_the_class_switches_to_the_sidecar_once_its_venv_exists(monkeypatch, tmp_path):
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend
    from services import tts_backend
    from services.sidecar_install import _INSTALL_COMPLETE_MARKER, _venv_python

    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", "")
    monkeypatch.delenv("OMNIVOICE_VOXCPM2_DIR")
    assert tts_backend.get_backend_class("voxcpm2") is tts_backend.VoxCPM2Backend

    py = _venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (tmp_path / _INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(tmp_path))

    cls = tts_backend.get_backend_class("voxcpm2")
    assert cls is VoxCPM2SubprocessBackend
    assert cls.venv_python() == py
    assert cls.is_available() == (True, "ready")
    # The same engine to the rest of the app.
    for attr in ("id", "display_name", "supports_voice_design", "applies_own_mastering", "gpu_compat"):
        assert getattr(cls, attr) == getattr(tts_backend.VoxCPM2Backend, attr), attr
    assert cls().supported_languages == tts_backend.VoxCPM2Backend().supported_languages


def test_the_sidecar_class_prepares_the_reference_and_trims_the_tail(monkeypatch):
    import torch

    import services.audio_dsp as dsp
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend
    from services import tts_backend

    sent = {}

    def fake_generate(self, text, **kw):
        sent.update(kw)
        return torch.zeros(1, 4800)

    trimmed = {}

    def fake_trim(wav, sr):
        trimmed["sr"] = sr
        return wav[:, :10]

    # Patch the class the engine actually inherits from: other suites purge
    # `services` from sys.modules, so a fresh import of subprocess_backend
    # can be a different module than the one the engine subclassed.
    monkeypatch.setattr(VoxCPM2SubprocessBackend.__bases__[0], "generate", fake_generate)
    monkeypatch.setattr(tts_backend, "_prepare_voxcpm_ref", lambda p: p + ".prepared.wav")
    monkeypatch.setattr(dsp, "trim_trailing_silence", fake_trim)

    out = VoxCPM2SubprocessBackend().generate("hi", ref_audio="/clip.wav")

    assert sent["ref_audio"] == "/clip.wav.prepared.wav"
    assert trimmed["sr"] == 48000
    assert tuple(out.shape) == (1, 10)


def test_output_is_resampled_to_the_48_khz_the_parent_assumes(monkeypatch):
    """The parent trims and labels the PCM at a fixed 48 kHz, so a model that
    reports another rate must be resampled, not passed through."""
    torch = types.ModuleType("torch")
    torch.float32 = np.float32
    torch.as_tensor = lambda value, dtype=None: np.asarray(value, dtype=dtype)
    torchaudio = types.ModuleType("torchaudio")
    torchaudio.functional = types.SimpleNamespace(
        resample=lambda value, _source, _target: np.zeros(960, dtype=np.float32)
    )
    monkeypatch.setitem(sys.modules, "torch", torch)
    monkeypatch.setitem(sys.modules, "torchaudio", torchaudio)
    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._handle_synthesize({"text": "warm up"}, io.BytesIO())
    type(sidecar._MODEL).sample_rate = 24000
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hi"}, out)
    audio = _frames(out)[-1]
    assert audio["sample_rate"] == 48000
    assert audio["n_samples"] == 960  # 480 samples at 24 kHz


def test_a_failed_install_leaves_voxcpm2_in_process(monkeypatch, tmp_path):
    """A venv interpreter without the completion marker (a reinstall that
    failed partway) must not hide the working in-process engine."""
    from services import tts_backend
    from services.sidecar_install import _venv_python

    py = _venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(tmp_path))
    assert tts_backend.get_backend_class("voxcpm2") is tts_backend.VoxCPM2Backend


@pytest.mark.parametrize(('text', 'options', 'expected_text', 'continuation'), [
    ('hello', {'description': 'warm, low'}, '(warm, low)hello', False),
    ('hello', {'description': 'warm', 'instruct': 'calm'}, '(warm, calm)hello', False),
    ('hello', {'ref_audio': 'voice.wav', 'ref_text': 'reference', 'instruct': 'calm'}, '(calm)hello', False),
    ('(calm)hello', {'ref_audio': 'voice.wav', 'ref_text': 'reference'}, '(calm)hello', False),
    ('hello', {'ref_audio': 'voice.wav', 'ref_text': 'reference'}, 'hello', True),
    ('hello', {'ref_audio': 'voice.wav', 'ref_text': 'reference', 'instruct': '  '}, 'hello', True),
    ('hello', {'ref_text': 'orphan transcript'}, 'hello', False),
])
def test_both_adapters_follow_native_modes(monkeypatch, text, options, expected_text, continuation):
    from services import tts_backend
    calls = []
    sidecar = _load_sidecar(monkeypatch, calls)
    # A strict signature catches unsupported API keywords, unlike **kwargs
    # fakes which previously accepted the nonexistent voice_description.
    def generate(text, cfg_value, max_len, inference_timesteps, reference_wav_path=None,
                 prompt_wav_path=None, prompt_text=None, retry_badcase=True,
                 retry_badcase_max_times=3):
        # VoxCPM requires one initial generation even when retries are disabled.
        assert retry_badcase is False and retry_badcase_max_times == 1
        calls.append(dict(text=text, cfg_value=cfg_value,
                          max_len=max_len,
                          inference_timesteps=inference_timesteps,
                          reference_wav_path=reference_wav_path,
                          prompt_wav_path=prompt_wav_path, prompt_text=prompt_text))
        return np.zeros(480, dtype=np.float32)
    model = types.SimpleNamespace(generate=generate, sample_rate=48000)
    monkeypatch.setattr(sidecar, '_load_model', lambda _: model)
    sidecar._handle_synthesize({'text': text, **options}, io.BytesIO())
    sidecar_call = calls[-1]
    engine = tts_backend.VoxCPM2Backend()
    engine._model = model
    monkeypatch.setattr(tts_backend, '_prepare_voxcpm_ref', lambda path: path)
    engine.generate(text, **options)
    assert calls[-1] == sidecar_call
    assert sidecar_call['text'] == expected_text
    assert sidecar_call['reference_wav_path'] == options.get('ref_audio')
    assert sidecar_call['prompt_wav_path'] == (options['ref_audio'] if continuation else None)
    assert sidecar_call['prompt_text'] == (options['ref_text'] if continuation else None)
    assert engine.supports_voice_design


def test_generation_emits_heartbeats_so_a_slow_render_is_not_killed(monkeypatch):
    """Review (Greptile P1): a CPU / small-GPU render can outlast any fixed recv
    deadline, so the sidecar must keep sending progress frames while generating
    (each one re-arms the parent's watchdog) instead of going silent."""
    import json as _json
    import struct

    sidecar = _load_sidecar(monkeypatch, [])
    monkeypatch.setattr(sidecar, "_HEARTBEAT_S", 0.01)
    model = sidecar._load_model(io.BytesIO())
    real_generate = type(model).generate
    real_send = sidecar._send
    two_heartbeats = threading.Event()
    seen = []

    def observing_send(stream, obj):
        real_send(stream, obj)
        if obj.get("stage") == "generating":
            seen.append(obj)
            if len(seen) >= 2:
                two_heartbeats.set()

    monkeypatch.setattr(sidecar, "_send", observing_send)

    def generate(self, **kw):
        # Held until the heartbeat thread has demonstrably emitted two frames;
        # the timeout only bounds a failure, it is never the synchronisation.
        assert two_heartbeats.wait(30), "no heartbeats while generating"
        return real_generate(self, **kw)

    monkeypatch.setattr(type(model), "generate", generate)
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hello"}, out)

    data, ops = out.getvalue(), []
    while data:
        (n,) = struct.unpack("!I", data[:4])
        ops.append(_json.loads(data[4 : 4 + n])["op"])
        data = data[4 + n :]
    assert ops.count("progress") >= 2, ops
    assert ops[-1] == "audio"
