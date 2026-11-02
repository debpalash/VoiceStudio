"""CosyVoice 3 from its own venv: the sidecar, its requirements, and the switch.

The sidecar runs against a fake of upstream's `cosyvoice.cli.cosyvoice`, so
these tests pin how each request maps onto upstream's inference calls,
including the v3 system-prompt prefix upstream's own examples use.
"""
import importlib.util
import io
import json
import re
import struct
import sys
import types
from pathlib import Path

import pytest
import torch

_ROOT = Path(__file__).resolve().parents[1]
_MAIN = _ROOT / "backend/engines/cosyvoice_subprocess/main.py"
_REQUIREMENTS = _ROOT / "backend/engines/cosyvoice_subprocess/requirements.txt"
PREFIX = "You are a helpful assistant.<|endofprompt|>"


def _fake_model_class(name, calls, sample_rate=24000):
    def method(kind):
        def call(self, *args, **kwargs):
            calls.append((kind, args, kwargs))
            return iter([{"tts_speech": torch.full((1, 2400), 0.25)}])
        return call

    return type(name, (), {
        "sample_rate": sample_rate,
        "inference_instruct2": method("instruct2"),
        "inference_zero_shot": method("zero_shot"),
        "inference_cross_lingual": method("cross_lingual"),
        "inference_sft": method("sft"),
        "inference_vc": method("vc"),
        "list_available_spks": lambda self: ["spk-a"],
    })


def _load_sidecar(monkeypatch, tmp_path, calls, *, model_class="CosyVoice3", sample_rate=24000):
    checkout = tmp_path / "CosyVoice"
    (checkout / "pretrained_models" / "Fun-CosyVoice3-0.5B").mkdir(parents=True)
    (checkout / "asset").mkdir()
    (checkout / "asset" / "zero_shot_prompt.wav").write_bytes(b"RIFF")
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(checkout))
    monkeypatch.delenv("OMNIVOICE_COSYVOICE_MODEL", raising=False)

    cls = _fake_model_class(model_class, calls, sample_rate)

    def AutoModel(**kwargs):
        calls.append(("load", (), kwargs))
        return cls()

    cli = types.ModuleType("cosyvoice.cli.cosyvoice")
    cli.AutoModel = AutoModel
    monkeypatch.setitem(sys.modules, "cosyvoice", types.ModuleType("cosyvoice"))
    monkeypatch.setitem(sys.modules, "cosyvoice.cli", types.ModuleType("cosyvoice.cli"))
    monkeypatch.setitem(sys.modules, "cosyvoice.cli.cosyvoice", cli)
    llm = types.ModuleType("cosyvoice.llm.llm")
    llm.Qwen2Encoder = type("Qwen2Encoder", (), {})
    monkeypatch.setitem(sys.modules, "cosyvoice.llm", types.ModuleType("cosyvoice.llm"))
    monkeypatch.setitem(sys.modules, "cosyvoice.llm.llm", llm)
    monkeypatch.setattr(sys, "path", list(sys.path))
    spec = importlib.util.spec_from_file_location("_cosy_sidecar_under_test", _MAIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module, checkout


def _frames(buf):
    data, out, i = buf.getvalue(), [], 0
    while i < len(data):
        (n,) = struct.unpack("!I", data[i:i + 4])
        out.append(json.loads(data[i + 4:i + 4 + n]))
        i += 4 + n
    return out


def _last_call(calls):
    return next(c for c in reversed(calls) if c[0] != "load")


def test_loads_the_installed_weights_with_matcha_on_the_path(monkeypatch, tmp_path):
    calls = []
    sidecar, checkout = _load_sidecar(monkeypatch, tmp_path, calls)
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hello", "ref_audio": str(tmp_path / "r.wav")}, out)
    load = next(c for c in calls if c[0] == "load")
    assert load[2]["model_dir"] == str(checkout / "pretrained_models" / "Fun-CosyVoice3-0.5B")
    assert str(checkout / "third_party" / "Matcha-TTS") in sys.path
    audio = _frames(out)[-1]
    assert audio["op"] == "audio" and audio["sample_rate"] == 24000 and audio["n_samples"] == 2400


def test_a_missing_model_folder_never_reaches_a_modelscope_download(monkeypatch, tmp_path):
    calls = []
    sidecar, checkout = _load_sidecar(monkeypatch, tmp_path, calls)
    import shutil
    shutil.rmtree(checkout / "pretrained_models")
    with pytest.raises(RuntimeError, match="model folder is missing"):
        sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert not any(c[0] == "load" for c in calls)


def test_v3_zero_shot_prefixes_the_prompt_transcript(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav", "ref_text": "hello there"}, io.BytesIO())
    kind, args, _ = _last_call(calls)
    assert kind == "zero_shot"
    assert args == ("hi", PREFIX + "hello there", "/r.wav")


def test_v3_instruct_uses_upstreams_system_prompt(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav", "instruct": "Speak calmly."}, io.BytesIO())
    kind, args, _ = _last_call(calls)
    assert kind == "instruct2"
    assert args == ("hi", "You are a helpful assistant. Speak calmly.<|endofprompt|>", "/r.wav")


def test_v3_cross_lingual_prefixes_the_text_without_v2_language_tags(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav", "language": "en"}, io.BytesIO())
    kind, args, _ = _last_call(calls)
    assert kind == "cross_lingual"
    assert args == (PREFIX + "hi", "/r.wav")


def test_v3_without_a_clip_speaks_in_upstreams_sample_voice(monkeypatch, tmp_path):
    """CosyVoice 3 ships no built-in speakers."""
    calls = []
    sidecar, checkout = _load_sidecar(monkeypatch, tmp_path, calls)
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    kind, args, _ = _last_call(calls)
    assert kind == "cross_lingual"
    assert args == (PREFIX + "hi", str(checkout / "asset" / "zero_shot_prompt.wav"))


def test_a_v2_model_keeps_the_in_process_mapping(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls, model_class="CosyVoice2")
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav", "language": "en"}, io.BytesIO())
    assert _last_call(calls)[1] == ("<|en|>hi", "/r.wav")
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav", "ref_text": "hello"}, io.BytesIO())
    assert _last_call(calls)[1] == ("hi", "hello", "/r.wav")
    sidecar._handle_synthesize({"text": "hi"}, io.BytesIO())
    assert _last_call(calls)[:2] == ("sft", ("hi", "spk-a"))


def test_a_model_folder_override_is_honoured(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    other = tmp_path / "my-model"
    other.mkdir()
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_MODEL", str(other))
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav"}, io.BytesIO())
    assert next(c for c in calls if c[0] == "load")[2]["model_dir"] == str(other)


def test_output_is_resampled_to_the_reported_rate(monkeypatch, tmp_path):
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [], sample_rate=48000)
    out = io.BytesIO()
    sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav"}, out)
    assert _frames(out)[-1]["n_samples"] == 1200


def test_rejects_a_url_reference(monkeypatch, tmp_path):
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [])
    with pytest.raises(ValueError, match="local file path"):
        sidecar._handle_synthesize({"text": "hi", "ref_audio": "https://x.test/a.wav"}, io.BytesIO())


def test_the_sidecar_imports_nothing_from_the_app():
    src = _MAIN.read_text(encoding="utf-8")
    for name in ("services", "core", "engines", "api", "backend", "utils"):
        assert not re.search(rf"^\s*(from|import) {name}\b", src, re.M), name


def test_requirements_drop_what_the_one_click_install_must_not_pull():
    """No third-party index, no Linux-only acceleration, no web UI stack, and
    torch left to the installer's per-host pins."""
    lines = [
        line.split("#", 1)[0].strip()
        for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines()
    ]
    reqs = [line for line in lines if line]
    names = {re.split(r"[=<>!~ ;\[]", r, maxsplit=1)[0].lower() for r in reqs}
    assert not any(r.startswith("-") for r in reqs), "no index or option lines"
    for dropped in ("torch", "torchaudio", "deepspeed", "tensorrt-cu12", "onnxruntime-gpu",
                    "fastapi", "gradio", "uvicorn", "grpcio", "tensorboard",
                    "wetext", "pyworld"):
        assert dropped not in names, dropped
    assert "openai-whisper==20250625" in reqs  # 20231117 cannot build
    assert all("==" in r for r in reqs), "every requirement stays pinned"


def test_the_class_switches_to_the_sidecar_once_its_venv_exists(monkeypatch, tmp_path):
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services import tts_backend
    from services.sidecar_install import _INSTALL_COMPLETE_MARKER, _venv_python

    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "")
    monkeypatch.delenv("OMNIVOICE_COSYVOICE_DIR")
    monkeypatch.delenv("OMNIVOICE_COSYVOICE_MODEL", raising=False)
    assert tts_backend.get_backend_class("cosyvoice") is tts_backend.CosyVoiceBackend

    py = _venv_python(tmp_path / ".venv")
    py.parent.mkdir(parents=True)
    py.write_text("#!fake\n")
    (tmp_path / _INSTALL_COMPLETE_MARKER).write_text("x\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(tmp_path))

    cls = tts_backend.get_backend_class("cosyvoice")
    assert cls is CosyVoiceSubprocessBackend
    assert cls.is_available() == (True, "ready")
    for attr in ("id", "display_name", "gpu_compat"):
        assert getattr(cls, attr) == getattr(tts_backend.CosyVoiceBackend, attr), attr
    backend = cls()
    assert backend.supported_languages == tts_backend.CosyVoiceBackend().supported_languages
    assert backend.model_identity() == "Fun-CosyVoice3-0.5B"


def test_a_missing_model_override_is_an_error_not_a_silent_swap(monkeypatch, tmp_path):
    """Loading the installed model instead would speak with a model and voice
    the user did not choose, while model_identity() still named theirs."""
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_MODEL", str(tmp_path / "moved-away"))
    with pytest.raises(RuntimeError, match="OMNIVOICE_COSYVOICE_MODEL"):
        sidecar._handle_synthesize({"text": "hi", "ref_audio": "/r.wav"}, io.BytesIO())
    assert not any(c[0] == "load" for c in calls)


# The first release of each package that fixes the advisories upstream's pins
# fall under, per OSV and GitHub's advisory database (checked 2026-09-10, hydra-core 2026-10-12;
# only GitHub listed the protobuf and transformers ones). Raising a pin is
# fine; going below one of these reintroduces a known vulnerability.
_ADVISORY_FLOORS = {
    "diffusers": "0.38.0",
    "hydra-core": "1.3.7",
    "lightning": "2.6.6",
    "modelscope": "1.27.0",
    "onnx": "1.21.0",
    "protobuf": "5.29.6",
    "transformers": "5.10.0",
}


def test_requirements_stay_above_the_advisory_fixes():
    from packaging.version import Version

    pins = {}
    for line in _REQUIREMENTS.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if "==" in line:
            name, version = line.split("==", 1)
            pins[name.strip().lower()] = version.strip()
    for name, floor in _ADVISORY_FLOORS.items():
        assert name in pins, f"{name} is no longer pinned"
        assert Version(pins[name]) >= Version(floor), f"{name}=={pins[name]} is below {floor}"


@pytest.mark.parametrize("cuda", [False, True])
def test_load_uses_full_precision_without_cuda(monkeypatch, tmp_path, cuda):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: cuda)
    parts = {name: torch.nn.Linear(2, 2).to(dtype=torch.bfloat16) for name in ("llm", "flow", "hift")}
    model = types.SimpleNamespace(model=types.SimpleNamespace(**parts))
    monkeypatch.setattr(sys.modules["cosyvoice.cli.cosyvoice"], "AutoModel", lambda **kw: model)
    assert sidecar._load_model(io.BytesIO()) is model
    for part in parts.values():
        assert part.weight.dtype == (torch.bfloat16 if cuda else torch.float32)
        if not cuda:
            assert torch.isfinite(part(torch.ones(1, 2))).all()


def test_managed_install_probes_late_imports_and_restores_dependencies():
    from services import sidecar_install
    spec = sidecar_install.SPECS['cosyvoice']
    reqs = _REQUIREMENTS.read_text(encoding="utf-8")
    for dependency in ('gdown==6.4.0', 'wget==3.2', 'pyarrow==25.0.1'):
        assert dependency in reqs
    assert 'cosyvoice.dataset.processor' in spec.probe_code
    assert 'matcha.utils' in spec.probe_code
    assert '{checkout}/third_party/PyWorld' in spec.install_args
    assert spec.install_revision == 'security-floors-v4'
    sources = {source.path: source for source in spec.extra_sources}
    assert sources['third_party/PyWorld'].revision == 'f31ad88d543fdaebbda2d0c9a5e4d4f991ae0b6c'
    assert sources['third_party/PyWorld/lib/World'].revision == 'd625e7608ca23a870018f01e7c562ac683d9847f'


@pytest.mark.parametrize("legacy_cache", [False, True])
def test_cached_qwen_mask_includes_prompt_without_unmasking_padding(monkeypatch, tmp_path, legacy_cache):
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [])
    xs = torch.zeros(1, 1, 4)
    cache = ((torch.zeros(1, 2, 7, 4), torch.zeros(1, 2, 7, 4)),) if legacy_cache else types.SimpleNamespace(get_seq_length=lambda: 7)
    current = torch.ones(1, 1, 1, dtype=torch.bool)
    completed = sidecar._qwen_attention_mask(xs, current, cache)
    assert completed.shape == (1, 1, 8)
    assert completed.all()
    padded = torch.tensor([[[False, True, True, True, True, True, True, True]]])
    assert sidecar._qwen_attention_mask(xs, padded, cache) is padded
    assert sidecar._qwen_attention_mask(xs, current, None) is current


def test_qwen_initialization_preserves_checkpoint_precision_and_restores_loader(monkeypatch, tmp_path):
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [])
    class Loader:
        @classmethod
        def from_pretrained(cls, path, **kwargs):
            layer = torch.nn.Linear(1, 1, bias=False).to(kwargs.get("torch_dtype", torch.bfloat16))
            layer.load_state_dict({"weight": torch.tensor([[1.003]])})
            return layer
    class Qwen(Loader):
        pass
    monkeypatch.setitem(sys.modules, "transformers", types.SimpleNamespace(Qwen2ForCausalLM=Qwen))
    with sidecar._qwen_full_precision_load():
        layer = Qwen.from_pretrained("local")
        assert torch.equal(layer.weight, torch.tensor([[1.003]]))
    assert "from_pretrained" not in Qwen.__dict__
    with pytest.raises(ValueError), sidecar._qwen_full_precision_load():
        raise ValueError("load failed")
    assert "from_pretrained" not in Qwen.__dict__


def test_qwen_cached_decode_matches_full_context_without_model_download(monkeypatch, tmp_path):
    from transformers import Qwen2Config, Qwen2ForCausalLM

    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [])
    torch.manual_seed(19)
    qwen = Qwen2ForCausalLM(Qwen2Config(
        vocab_size=32, hidden_size=16, intermediate_size=32,
        num_hidden_layers=2, num_attention_heads=2, num_key_value_heads=1,
    )).eval()
    base = sys.modules["cosyvoice.llm.llm"].Qwen2Encoder

    class Encoder(base):
        def forward_one_step(self, xs, masks, cache=None):
            out = qwen(inputs_embeds=xs, attention_mask=masks[:, -1, :],
                       past_key_values=cache, use_cache=True, output_hidden_states=True)
            return out.hidden_states[-1], out.past_key_values

    encoder = Encoder()
    sidecar._repair_qwen_cache(types.SimpleNamespace(model=types.SimpleNamespace(
        llm=types.SimpleNamespace(llm=encoder),
    )))
    xs = torch.randn(1, 8, 16)
    with torch.no_grad():
        expected, _ = encoder.forward_one_step(xs, torch.ones(1, 8, 8, dtype=torch.bool))
        _, cache = encoder.forward_one_step(xs[:, :7], torch.ones(1, 7, 7, dtype=torch.bool))
        actual, _ = encoder.forward_one_step(xs[:, 7:], torch.ones(1, 1, 1, dtype=torch.bool), cache)
    torch.testing.assert_close(actual[:, -1], expected[:, -1], rtol=1e-4, atol=1e-5)


def test_load_preserves_legacy_non_qwen_checkout(monkeypatch, tmp_path):
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, [])
    monkeypatch.delattr(sys.modules['cosyvoice.llm.llm'], 'Qwen2Encoder')
    monkeypatch.setitem(sys.modules, 'transformers', types.ModuleType('transformers'))
    assert sidecar._load_model(io.BytesIO()) is not None


# The requirements each managed install revision installs. A changed
# requirements.txt needs a new revision, or existing installs keep their old
# completion marker and never receive the change.
_REQUIREMENTS_BY_REVISION = {
    "security-floors-v3": "10d15c52b78ae41cfd040880f97a3b9b71c2fa2dab9893bceb982fc46b72d9dd",
    "security-floors-v4": "607deac39c6ee97e595d05426af10e87070b4b52a3df2ad809d71e20d5058658",
}


def test_requirement_changes_bump_the_managed_install_revision():
    import hashlib
    from services import sidecar_install

    spec = sidecar_install.SPECS["cosyvoice"]
    digest = hashlib.sha256(_REQUIREMENTS.read_text(encoding="utf-8").encode()).hexdigest()
    assert _REQUIREMENTS_BY_REVISION.get(spec.install_revision) == digest, (
        "cosyvoice requirements.txt changed: bump install_revision in "
        "services/sidecar_install.py and record the new digest here"
    )


def test_an_install_from_the_previous_revision_is_offered_a_repair(tmp_path):
    from services import sidecar_install

    spec = sidecar_install.SPECS["cosyvoice"]
    marker = tmp_path / sidecar_install._INSTALL_COMPLETE_MARKER
    marker.write_text(f"{spec.probe_module}\ninference-imports-v2\n", encoding="utf-8")
    assert not sidecar_install._install_marker_valid(spec, tmp_path)
    marker.write_text(f"{spec.probe_module}\n{spec.install_revision}\n", encoding="utf-8")
    assert sidecar_install._install_marker_valid(spec, tmp_path)


# -- Keep my delivery (#3421): speech-to-speech through upstream inference_vc --


def test_convert_keeps_the_source_tokens_through_inference_vc(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    source, ref = tmp_path / "source.wav", tmp_path / "ref.wav"
    source.write_bytes(b"RIFF")
    ref.write_bytes(b"RIFF")
    out = io.BytesIO()
    sidecar._handle_convert({"source_audio": str(source), "ref_audio": str(ref)}, out)
    kind, args, kwargs = _last_call(calls)
    assert kind == "vc"
    assert args == (str(source), str(ref)) and kwargs == {"stream": False}
    assert _frames(out)[-1]["op"] == "audio"


def test_convert_refuses_urls_and_missing_files(monkeypatch, tmp_path):
    calls = []
    sidecar, _ = _load_sidecar(monkeypatch, tmp_path, calls)
    ref = tmp_path / "ref.wav"
    ref.write_bytes(b"RIFF")
    with pytest.raises(ValueError, match="local file path"):
        sidecar._handle_convert({"source_audio": "https://x.test/a.wav", "ref_audio": str(ref)}, io.BytesIO())
    with pytest.raises(ValueError, match="not found"):
        sidecar._handle_convert({"source_audio": str(tmp_path / "gone.wav"), "ref_audio": str(ref)}, io.BytesIO())
    assert not any(c[0] == "vc" for c in calls)


def test_the_sidecar_class_sends_a_convert_op(monkeypatch):
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services.tts_backend import CosyVoiceBackend, TTSBackend

    sent = []
    monkeypatch.setattr(
        CosyVoiceSubprocessBackend, "_exchange",
        lambda self, msg, *, deadline_text: sent.append(msg) or torch.zeros(1, 10),
    )
    backend = CosyVoiceSubprocessBackend()
    try:
        backend.convert_voice("/s.wav", ref_audio="/r.wav", seed=7)
    finally:
        backend.shutdown()
    assert sent == [{"op": "convert", "source_audio": "/s.wav", "ref_audio": "/r.wav", "seed": 7}]
    assert CosyVoiceSubprocessBackend.supports_voice_conversion is True
    # The in-process scaffold and every other engine keep re-speaking.
    assert CosyVoiceBackend.supports_voice_conversion is False
    assert TTSBackend.supports_voice_conversion is False
