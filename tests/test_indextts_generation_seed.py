"""Requested IndexTTS seeds must cross the process boundary and seed inference."""

from contextlib import nullcontext
import random
from types import SimpleNamespace

import numpy as np
import pytest
import torch


@pytest.mark.parametrize("seed", [None, 0, 42])
def test_generation_route_forwards_the_seed_to_the_indextts_worker(monkeypatch, seed):
    """Keep explicit seeds, including zero, intact through the route and adapter."""
    from api.routers.generation import _run_backend_inference
    from engines.indextts import IndexTTS2Backend
    from services.subprocess_backend import SubprocessBackend

    sent = []

    def capture(self, text, **kwargs):
        """Record the worker payload without starting an IndexTTS process."""
        sent.append(kwargs)
        return torch.full((1, 2400), 0.1)

    monkeypatch.setattr(SubprocessBackend, "generate", capture)
    monkeypatch.setattr(IndexTTS2Backend, "_check_language", lambda *args: None)
    backend = IndexTTS2Backend.__new__(IndexTTS2Backend)
    _run_backend_inference(
        backend,
        "A synthetic sample.",
        "English",
        "reference.wav",
        "Reference words.",
        None,
        None,
        32,
        2,
        1,
        True,
        True,
        seed,
        "raw",
    )

    assert len(sent) == 1
    if seed is None:
        assert "seed" not in sent[0]
    else:
        assert sent[0]["seed"] == seed


@pytest.mark.parametrize("seed", [0, 7, 2**32 + 7])
def test_worker_seeds_python_numpy_and_torch_before_inference(monkeypatch, seed):
    """Repeat all three RNG draws for the same seed and change them for another."""
    from engines.indextts import main

    draws = []

    def infer(**kwargs):
        """Sample each worker RNG in place of running the synthesis model."""
        draws.append((random.random(), float(np.random.random()), torch.rand(1).item()))

    monkeypatch.setattr(main, "_load_model", lambda stdout: SimpleNamespace(infer=infer))
    monkeypatch.setattr(main, "_heartbeat", lambda *args: nullcontext())
    monkeypatch.setattr(main, "_wav_to_pcm_b64", lambda path: ("AA==", 24000, 1))
    monkeypatch.setattr(main, "_send", lambda *args: None)
    message = {"text": "Synthetic words.", "ref_audio": "reference.wav", "seed": seed}
    states = random.getstate(), np.random.get_state(), torch.random.get_rng_state()
    try:
        main._handle_synthesize(message, None)
        main._handle_synthesize(message, None)
        main._handle_synthesize({**message, "seed": seed + 1}, None)
    finally:
        random.setstate(states[0])
        np.random.set_state(states[1])
        torch.random.set_rng_state(states[2])

    assert draws[0] == draws[1]
    assert all(before != after for before, after in zip(draws[0], draws[2]))
