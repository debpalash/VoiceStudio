"""One take is one voice: every chunk and span renders with the take's seed (#3730).

A Voice Design profile's seed identifies the designed speaker (#3254, #3413).
/generate used to advance it per engine call (``used_seed + i``), so a script
long enough to split switched speakers mid-take; inline ``[pause]`` spans
either advanced the native RNG or lost the seed after the first span for
out-of-process engines.
"""
import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import importlib

import pytest
import torch

SEED = 7
LONG_TEXT = ("This is a complete sentence for the chunker. " * 10).strip()
PAUSE_TEXT = "First sentence. [pause 100ms] Second sentence. [pause 100ms] Third sentence."


def _tts():
    return importlib.import_module("services.tts_backend")


def _make_engine(engine_id):
    class _Engine(_tts().TTSBackend):
        id = engine_id
        display_name = "Seed Probe (test)"
        # Out-of-process adapters get the seed per request; this probe declares
        # support so the forwarded kwarg is asserted too.
        supports_generation_seed = True
        calls: list = []

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["multi"]

        @classmethod
        def is_available(cls):
            return True, "ready"

        def generate(self, text, **kw) -> torch.Tensor:
            type(self).calls.append((torch.initial_seed(), dict(kw)))
            torch.rand(4)  # advance the stream like real sampling does
            return torch.zeros(1, 480)

    return _Engine


@pytest.fixture()
def client():
    from fastapi.testclient import TestClient
    from main import app

    return TestClient(app, client=("127.0.0.1", 50000))


def test_every_chunk_uses_the_take_seed(client, monkeypatch):
    fake = _make_engine("seed-probe-chunks")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)

    res = client.post("/generate", data={
        "text": LONG_TEXT, "engine": fake.id, "seed": str(SEED),
        "max_chunk_chars": "80", "effect_preset": "raw",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) >= 3, "long text should fan out into several chunks"
    assert {rng_seed for rng_seed, _kw in fake.calls} == {SEED}
    assert [kw.get("seed") for _seed, kw in fake.calls] == [SEED] * len(fake.calls)


def test_every_pause_span_uses_the_take_seed(client, monkeypatch):
    fake = _make_engine("seed-probe-pauses")
    monkeypatch.setitem(_tts()._REGISTRY, fake.id, fake)

    res = client.post("/generate", data={
        "text": PAUSE_TEXT, "engine": fake.id, "seed": str(SEED),
        "effect_preset": "raw",
    })

    assert res.status_code == 200, res.text
    assert len(fake.calls) == 3
    assert {rng_seed for rng_seed, _kw in fake.calls} == {SEED}
    assert [kw.get("seed") for _seed, kw in fake.calls] == [SEED] * 3


class _NativeModel:
    sampling_rate = 24000


def test_native_chunks_restart_from_the_take_seed(monkeypatch):
    from api.routers import generation

    seeds: list[int] = []

    def fake_generate(model, **kw):
        seeds.append(torch.initial_seed())
        torch.rand(4)
        return torch.zeros(1, 480), None

    monkeypatch.setattr(_tts(), "generate_with_cached_ref", fake_generate)

    generation._run_inference(
        _NativeModel(), LONG_TEXT, "English", None, None, "male", None,
        16, 2.0, 1.0, 0.1, True, True, None, None, None, SEED, "raw",
        max_chunk_chars=80, crossfade_ms=0,
    )

    assert len(seeds) >= 3, "long text should fan out into several chunks"
    assert set(seeds) == {SEED}


def test_native_pause_spans_restart_from_the_take_seed(monkeypatch):
    from api.routers import generation

    states: list[torch.Tensor] = []

    def fake_generate(model, **kw):
        states.append(torch.get_rng_state().clone())
        torch.rand(4)
        return torch.zeros(1, 480), None

    monkeypatch.setattr(_tts(), "generate_with_cached_ref", fake_generate)

    generation._run_inference(
        _NativeModel(), PAUSE_TEXT, "English", None, None, "male", None,
        16, 2.0, 1.0, 0.1, True, True, None, None, None, SEED, "raw",
    )

    assert len(states) == 3
    assert torch.equal(states[0], states[1])
    assert torch.equal(states[0], states[2])
