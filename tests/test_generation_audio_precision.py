import io

import numpy as np
import pytest
import soundfile as sf
import torch



@pytest.mark.parametrize("bits,subtype", [(16, "PCM_16"), (24, "PCM_24"), (32, "FLOAT")])
def test_wav_precision_preserves_samples_and_metadata(bits, subtype):
    from services.generation_audio import save_generation_wav
    # Deliberately below a 16-bit quantization step; float must not be upcast PCM.
    audio = torch.tensor([[0.000001, -0.000002, 0.25, -0.5]]).repeat(2, 50)
    original = audio.clone()
    output = io.BytesIO()
    save_generation_wav(output, audio, 48000, bits)
    output.seek(0)
    assert sf.info(output).subtype == subtype
    output.seek(0)
    decoded, rate = sf.read(output, dtype="float32", always_2d=True)
    assert rate == 48000
    assert decoded.shape == (200, 2)
    np.testing.assert_allclose(decoded, audio.numpy().T, atol=0 if bits == 32 else 2 ** -(bits - 1), rtol=0)
    assert torch.equal(audio, original)


def test_invalid_precision_rejected_before_writing():
    from services.generation_audio import save_generation_wav
    output = io.BytesIO()
    with pytest.raises(ValueError, match="precision"):
        save_generation_wav(output, torch.zeros(10), 24000, 8)
    assert output.getvalue() == b""


def test_integer_audio_scales_and_clipping_does_not_modify_input():
    from services.generation_audio import save_generation_wav
    output = io.BytesIO()
    save_generation_wav(output, torch.tensor([16384, -16384], dtype=torch.int16), 24000, 32)
    output.seek(0)
    np.testing.assert_array_equal(sf.read(output)[0], [0.5, -0.5])
    audio = torch.tensor([2.0, -2.0])
    save_generation_wav(io.BytesIO(), audio, 24000, 32)
    assert audio.tolist() == [2.0, -2.0]


def test_invalid_api_precision_rejected_without_model_work():
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from api.routers.generation import router
    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.post('/generate', data={'text': 'hello', 'wav_bits': '8'}).status_code == 422


@pytest.mark.parametrize('engine', ['omnivoice', 'voxcpm2'])
@pytest.mark.parametrize('audio_format', ['f32le', 's16le'])
def test_sidecar_roundtrip_preserves_float_and_accepts_legacy_pcm(monkeypatch, engine, audio_format):
    from engines.omnivoice_subprocess import OmniVoiceSubprocessBackend
    from engines.omnivoice_subprocess.main import _tensor_to_pcm_b64
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend
    from engines.voxcpm2_subprocess.main import _to_pcm_b64
    from services import model_manager

    samples = torch.tensor([[0.000001, -0.000002, .125, -.25]])
    if engine == 'omnivoice':
        backend = OmniVoiceSubprocessBackend()
        encoded = _tensor_to_pcm_b64(samples, 24000, audio_format)[0]
    else:
        backend = VoxCPM2SubprocessBackend()
        encoded = _to_pcm_b64(samples, audio_format)[0]
    sent = []
    reply = {'op': 'audio', 'audio_pcm_b64': encoded}
    if audio_format == 'f32le':
        reply['audio_format'] = audio_format
    monkeypatch.setattr(model_manager, 'running_on_gpu_pool', lambda: True)
    monkeypatch.setattr(backend, '_validate_generate_authorization', lambda: None)
    monkeypatch.setattr(backend, '_spawn', lambda: None)
    monkeypatch.setattr(backend, '_send', sent.append)
    monkeypatch.setattr(backend, '_recv_with_timeout', lambda _timeout: reply)
    try:
        actual = backend.generate('test')
        assert sent[0]['audio_format'] == 'f32le'
        np.testing.assert_allclose(actual.numpy(), samples.numpy(),
                                   atol=0 if audio_format == 'f32le' else 2/32768, rtol=0)
    finally:
        backend.unload()


@pytest.mark.parametrize('native_controls', [True, False])
def test_sidecar_receives_seed_for_each_chunk(native_controls):
    from api.routers.generation import _run_backend_inference
    calls = []
    class Backend:
        sample_rate = 24000
        supports_native_omnivoice_controls = native_controls
        supports_generation_seed = True
        def generate(self, text, **kwargs):
            calls.append(kwargs)
            return torch.ones(1, 24000) * .1
    _run_backend_inference(Backend(), 'One sentence is spoken here. Another sentence follows it.',
                           'English', None, None, None, None, 32, 2, 1, True, True,
                           42, 'raw', max_chunk_chars=30, crossfade_ms=0)
    assert len(calls) == 2
    assert [call['seed'] for call in calls] == [42, 42]  # one take, one voice (#3730)
    assert [call['num_step'] for call in calls] == [32, 32]


def test_voxcpm_child_applies_seed_and_reports_float_format(monkeypatch):
    from engines.voxcpm2_subprocess import main
    seen = []
    class Model:
        sample_rate = 48000
        def generate(self, **kwargs):
            seen.append(kwargs['inference_timesteps'])
            return np.array([.000001, -.000002, .25], dtype=np.float32)
    monkeypatch.setattr(main, '_load_model', lambda _: Model())
    monkeypatch.setattr(torch, 'manual_seed', lambda seed: seen.append(seed))
    frames = []
    monkeypatch.setattr(main, '_send', lambda _, frame: frames.append(frame))
    main._handle_synthesize({'text': 'hello', 'seed': 42, 'num_step': 32, 'audio_format': 'f32le'}, None)
    assert seen == [42, 32]
    assert frames[0]['audio_format'] == 'f32le'
    assert frames[0]['sample_rate'] == 48000


def test_output_rate_metadata_never_loads_a_model(monkeypatch):
    from services import tts_backend as tts
    from types import SimpleNamespace
    monkeypatch.setattr(tts, "_active_instance", None)
    assert tts.output_sample_rate("voxcpm2") == 48000
    assert tts.output_sample_rate("omnivoice") == 24000
    assert tts.output_sample_rate("kittentts") == 24000
    monkeypatch.setattr(tts, "get_backend_class", lambda _: pytest.fail("Must use live metadata"))
    monkeypatch.setattr(tts, "_active_instance", SimpleNamespace(sample_rate=44100))
    monkeypatch.setattr(tts, "_active_instance_id", "custom")
    assert tts.output_sample_rate("custom") == 44100
    monkeypatch.setattr(tts, "_active_instance", None)
    monkeypatch.setattr(tts, "get_backend_class", lambda _: SimpleNamespace())
    assert tts.output_sample_rate("unknown") is None
