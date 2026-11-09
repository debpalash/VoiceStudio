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


def test_generation_kwargs_cap_token_budget_and_default_steps(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [])

    short = sidecar.generation_kwargs("hello", num_step="not a number")
    assert short["max_len"] == 256
    assert short["inference_timesteps"] == 10
    assert short["retry_badcase"] is False
    assert short["retry_badcase_max_times"] == 1

    assert sidecar.generation_kwargs("x" * 2000)["max_len"] == 4096


def test_generation_kwargs_clamp_step_count(monkeypatch):
    sidecar = _load_sidecar(monkeypatch, [])
    steps = {n: sidecar.generation_kwargs("hi", num_step=n)["inference_timesteps"]
             for n in (100, 32, -5)}
    assert steps == {100: 64, 32: 32, -5: 1}


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


# The engine venv's own torch (a cu128 wheel) has no Pascal kernels.
_CU128_ENGINE_ARCHS = ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"]


def _fake_torch(monkeypatch, *, available=True, capability=(6, 1), archs=None):
    cuda = types.SimpleNamespace(
        is_available=lambda: available,
        get_device_capability=lambda _i=0: capability,
        get_arch_list=lambda: list(_CU128_ENGINE_ARCHS if archs is None else archs),
    )
    monkeypatch.setitem(sys.modules, "torch", types.SimpleNamespace(cuda=cuda))


def test_a_gpu_the_venv_torch_cannot_run_loads_on_cpu(monkeypatch):
    """A GTX 10xx (sm_61) under a cu128 wheel: CPU, not "no kernel image"."""
    calls = []
    monkeypatch.delenv("OMNIVOICE_VOXCPM_DEVICE", raising=False)
    _fake_torch(monkeypatch, capability=(6, 1))
    sidecar = _load_sidecar(monkeypatch, calls)
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert calls[0]["device"] == "cpu"


@pytest.mark.parametrize("capability", [(8, 9), (8, 6), (12, 0)])
def test_a_covered_gpu_keeps_voxcpms_own_device_choice(monkeypatch, capability):
    """sm_89 has no tag of its own but sm_86 covers it (#1285): no override."""
    calls = []
    monkeypatch.delenv("OMNIVOICE_VOXCPM_DEVICE", raising=False)
    _fake_torch(monkeypatch, capability=capability)
    sidecar = _load_sidecar(monkeypatch, calls)
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert "device" not in calls[0]


def test_no_cuda_or_unknown_arch_list_keeps_voxcpms_choice(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_VOXCPM_DEVICE", raising=False)
    sidecar = _load_sidecar(monkeypatch, [])
    _fake_torch(monkeypatch, available=False)
    assert sidecar._load_device() is None
    _fake_torch(monkeypatch, archs=[])
    assert sidecar._load_device() is None


def test_the_device_env_overrides_the_probe(monkeypatch):
    calls = []
    monkeypatch.setenv("OMNIVOICE_VOXCPM_DEVICE", "cuda")
    _fake_torch(monkeypatch, capability=(6, 1))
    sidecar = _load_sidecar(monkeypatch, calls)
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert calls[0]["device"] == "cuda"


def test_the_sidecar_arch_rule_matches_device_caps(monkeypatch):
    """The sidecar keeps its own copy of cuda_build_covers; keep them equal."""
    from core.device_caps import cuda_build_covers

    sidecar = _load_sidecar(monkeypatch, [])
    lists = [
        _CU128_ENGINE_ARCHS, ["sm_61", "sm_86"], ["sm_86"], ["sm_89"], ["sm_90a"],
        ["compute_80"], ["compute_100f"], ["compute_90"], [], ["banana", "sm_"],
    ]
    caps = [(6, 1), (7, 5), (8, 6), (8, 9), (9, 0), (9, 1), (10, 0), (12, 0)]
    for archs in lists:
        for major, minor in caps:
            assert sidecar._cuda_build_covers(archs, major, minor) == \
                cuda_build_covers(archs, major, minor), (archs, major, minor)


@pytest.mark.parametrize(
    "raw, expected",
    [
        (None, 900.0),
        ("garbage", 900.0),
        ("nan", 900.0),
        ("inf", 900.0),
        ("5", 30.0),
        ("29.9", 30.0),
        ("240", 240.0),
    ],
)
def test_recv_timeout_env_is_parsed_and_floored(monkeypatch, raw, expected):
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    if raw is None:
        monkeypatch.delenv("OMNIVOICE_VOXCPM2_RECV_TIMEOUT_S", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_VOXCPM2_RECV_TIMEOUT_S", raw)
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


def test_audio_from_a_non_48k_model_is_resampled_before_sending(monkeypatch):
    fake_torch = types.ModuleType("torch")
    fake_torch.float32 = np.float32
    fake_torch.as_tensor = lambda wav, dtype=None: np.asarray(wav, dtype=dtype)
    fake_torchaudio = types.ModuleType("torchaudio")
    fake_functional = types.SimpleNamespace(
        resample=lambda wav, src, dst: np.zeros(int(len(wav) * dst / src), dtype=np.float32)
    )
    fake_torchaudio.functional = fake_functional
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    monkeypatch.setitem(sys.modules, "torchaudio", fake_torchaudio)

    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._handle_synthesize({"text": "warm up"}, io.BytesIO())  # loads the model
    sidecar._MODEL.sample_rate = 24000

    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hello"}, out)

    last = _frames(out)[-1]
    assert last["sample_rate"] == 48000
    assert last["n_samples"] == 960  # 480 samples at 24 kHz -> 960 at 48 kHz


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


_MODE_CASES = [
    ("hello", {"description": "calm voice"}, "(calm voice)hello", False),
    ("hello", {"description": "calm voice", "instruct": "slowly"}, "(calm voice, slowly)hello", False),
    ("hello", {"ref_audio": "/ref.wav", "ref_text": "the clip", "instruct": "whisper"},
     "(whisper)hello", False),
    ("(shout)hello", {"ref_audio": "/ref.wav", "ref_text": "the clip"}, "(shout)hello", False),
    ("hello", {"ref_audio": "/ref.wav", "ref_text": "the clip"}, "hello", True),
    ("hello", {"ref_audio": "/ref.wav", "ref_text": "the clip", "instruct": "  "}, "hello", True),
    ("hello", {"ref_text": "the clip"}, "hello", False),
]


@pytest.mark.parametrize("text, options, final_text, continuing", _MODE_CASES)
def test_sidecar_and_in_process_adapter_drive_voxcpm_identically(
    monkeypatch, text, options, final_text, continuing
):
    from services import tts_backend

    seen = []

    class StrictModel:
        sample_rate = 48000

        def generate(self, text, cfg_value, max_len, inference_timesteps, reference_wav_path,
                     prompt_wav_path, prompt_text, retry_badcase, retry_badcase_max_times):
            assert retry_badcase is False and retry_badcase_max_times == 1
            seen.append({
                "text": text, "cfg_value": cfg_value, "max_len": max_len,
                "inference_timesteps": inference_timesteps,
                "reference_wav_path": reference_wav_path,
                "prompt_wav_path": prompt_wav_path, "prompt_text": prompt_text,
            })
            return np.zeros(480, dtype=np.float32)

    sidecar = _load_sidecar(monkeypatch, [])
    sidecar._MODEL = StrictModel()
    sidecar._handle_synthesize({"text": text, **options}, io.BytesIO())

    monkeypatch.setattr(tts_backend, "_prepare_voxcpm_ref", lambda path: path)
    engine = tts_backend.VoxCPM2Backend()
    engine._model = StrictModel()
    engine.generate(text, **options)

    assert engine.supports_voice_design
    assert len(seen) == 2
    via_sidecar, in_process = seen
    assert via_sidecar == in_process
    assert via_sidecar["text"] == final_text
    assert via_sidecar["reference_wav_path"] == options.get("ref_audio")
    if continuing:
        assert via_sidecar["prompt_wav_path"] == options["ref_audio"]
        assert via_sidecar["prompt_text"] == options["ref_text"]
    else:
        assert via_sidecar["prompt_wav_path"] is None
        assert via_sidecar["prompt_text"] is None


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
