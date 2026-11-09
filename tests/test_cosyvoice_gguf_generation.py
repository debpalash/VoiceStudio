"""Generation API seed-forwarding coverage for CosyVoice GGUF."""

from __future__ import annotations

import torch
from api.routers.generation import _backend_supports_seed, _run_backend_inference
from engines.cosyvoice_gguf import CosyVoiceGGUFBackend


def test_cosyvoice_seed_capability_is_shared_by_streaming_and_batch_paths():
    backend = CosyVoiceGGUFBackend()
    assert _backend_supports_seed(backend)


def test_nonstream_generation_forwards_seed_per_chunk():
    backend = CosyVoiceGGUFBackend()
    calls = []
    backend.generate = lambda text, **kwargs: calls.append(kwargs) or torch.ones(1, 24000) * 0.1
    _run_backend_inference(
        backend,
        "One sentence is spoken here. Another sentence follows it.",
        "English", None, None, None, None,
        32, 2, 1, True, True, 42, "raw",
        max_chunk_chars=30, crossfade_ms=0,
    )
    assert [call["seed"] for call in calls] == [42, 42]  # one take, one voice (#3730)
