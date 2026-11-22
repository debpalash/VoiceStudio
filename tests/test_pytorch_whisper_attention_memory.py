"""PyTorch Whisper must not retain encoder self-attention maps (#4318).

Word timestamps make transformers request ``output_attentions`` model-wide, so
the encoder kept a [batch, heads, 1500, 1500] map per layer (~11 GB at batch 2
for large-v3-turbo) and the CPU fallback was OOM-killed. DTW alignment needs only
decoder cross-attention.
"""
import importlib
import types

import pytest


@pytest.fixture
def ab():
    # Resolved per test: another test may swap services.asr_backend in sys.modules.
    return importlib.import_module("services.asr_backend")


def _tiny_whisper():
    torch = pytest.importorskip("torch")
    tf = pytest.importorskip("transformers")
    cfg = tf.WhisperConfig(
        vocab_size=51865, d_model=16, encoder_layers=2, decoder_layers=2,
        encoder_attention_heads=2, decoder_attention_heads=2,
        encoder_ffn_dim=16, decoder_ffn_dim=16, num_mel_bins=80,
        max_source_positions=1500, max_target_positions=32, attn_implementation="eager",
    )
    return torch, tf.WhisperForConditionalGeneration(cfg).eval()


def test_encoder_attentions_dropped_but_decoder_cross_attention_kept(ab):
    torch, model = _tiny_whisper()
    feats = torch.zeros(1, 80, 3000)
    ids = torch.tensor([[1]])

    def run():
        with torch.no_grad():
            return model(input_features=feats, decoder_input_ids=ids, output_attentions=True)

    before = run()
    assert before.encoder_attentions is not None  # fail-before: maps retained

    be = ab.PyTorchWhisperBackend(asr_pipe=types.SimpleNamespace(model=model))
    be._limit_encoder_attention()
    be._limit_encoder_attention()  # idempotent: one hook only
    assert len(model.model.encoder._forward_pre_hooks) == 1

    after = run()
    assert not after.encoder_attentions
    assert len(after.cross_attentions) == 2  # alignment heads still available


def test_limit_encoder_attention_tolerates_foreign_pipes(ab):
    ab.PyTorchWhisperBackend(asr_pipe=object())._limit_encoder_attention()
    ab.PyTorchWhisperBackend(asr_pipe=types.SimpleNamespace(model=object()))._limit_encoder_attention()


def _backend_on_cpu(ab, monkeypatch, free_gb):
    be = ab.PyTorchWhisperBackend(asr_pipe=object())
    calls = []
    monkeypatch.setattr(be, "_call_pipe", lambda a, r, b, w, g: calls.append((b, w)) or {})
    monkeypatch.setattr(be, "_pipe_on_gpu", lambda: False)
    monkeypatch.setattr(be, "_host_free_gb", lambda: free_gb)
    return be, calls


def test_cpu_runs_batch_one_and_keeps_words_with_headroom(ab, monkeypatch):
    be, calls = _backend_on_cpu(ab, monkeypatch, 16.0)
    be._run_with_oom_fallback(None, 16000, True, None)
    assert calls == [(1, True)]


def test_cpu_low_ram_degrades_to_segment_timestamps_with_logged_reason(ab, monkeypatch, caplog):
    be, calls = _backend_on_cpu(ab, monkeypatch, 1.0)
    with caplog.at_level("WARNING"):
        be._run_with_oom_fallback(None, 16000, True, None)
    assert calls == [(1, False)]
    assert "segment timestamps" in caplog.text


def test_gpu_oom_falls_back_to_cpu_batch_one(ab, monkeypatch):
    be = ab.PyTorchWhisperBackend(asr_pipe=object())
    calls = []

    def call(a, r, b, w, g):
        calls.append(b)
        if len(calls) <= 3:
            raise RuntimeError("CUDA out of memory")
        return {}

    monkeypatch.setattr(be, "_call_pipe", call)
    monkeypatch.setattr(be, "_pipe_on_gpu", lambda: True)
    monkeypatch.setattr(be, "_rebuild_on_cpu", lambda: None)
    monkeypatch.setattr(be, "_host_free_gb", lambda: 16.0)
    import services.model_manager as mm
    monkeypatch.setattr(mm, "release_after_oom", lambda exc: None)
    be._run_with_oom_fallback(None, 16000, True, None)
    assert calls == [8, 2, 1, 1]


def test_mps_keeps_batch_two_and_word_timestamps_on_low_ram(ab, monkeypatch):
    """The CPU memory rule must not touch an MPS pipeline (not on the CUDA ladder)."""
    be = ab.PyTorchWhisperBackend(asr_pipe=types.SimpleNamespace(device="mps"))
    calls = []
    monkeypatch.setattr(be, "_call_pipe", lambda a, r, b, w, g: calls.append((b, w)) or {})
    monkeypatch.setattr(be, "_host_free_gb", lambda: 1.0)
    be._run_with_oom_fallback(None, 16000, True, None)
    assert calls == [(2, True)]
