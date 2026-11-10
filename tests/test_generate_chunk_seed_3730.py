"""One take, one voice: every engine call of a take uses the take's seed (#3730).

A take is rendered by several engine calls when the text is long (chunks) or
contains ``[pause]`` markers. The seed identifies a Voice Design speaker
(#3254, #3413), so advancing it per chunk (``seed + i``), letting ``[pause]``
spans run on from one RNG stream, or forwarding it to the first span only,
rendered two or three different speakers in one file.
"""
import os

import pytest
import torch

os.environ.setdefault("OMNIVOICE_MODEL", "test")
os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

SEED = 1234
LONG = " ".join(f"Sentence number {n} is complete." for n in range(12))
PAUSED = "First span here. [pause 200ms] Second span here. [pause 200ms] Third span."


def _probe(calls, *, accepts_seed=True):
    class Probe:
        sample_rate = 24000
        supports_generation_seed = accepts_seed

        def generate(self, text, **kw):
            # The forwarded seed, and the parent RNG state the call starts from.
            calls.append((kw.get("seed"), torch.initial_seed(), torch.rand(1).item()))
            return torch.ones(1, 2400) * 0.1

    return Probe()


def _backend_render(text, calls, **kw):
    from api.routers.generation import _run_backend_inference

    _run_backend_inference(_probe(calls, **kw), text, "English", None, None, None, None,
                           32, 2, 1, True, True, SEED, "raw", 60, 0)


@pytest.mark.parametrize("text", [LONG, PAUSED], ids=["chunks", "pause-spans"])
def test_every_engine_call_gets_the_take_seed(text):
    calls = []
    _backend_render(text, calls)
    assert len(calls) >= 3
    assert {seed for seed, _, _ in calls} == {SEED}
    assert {initial for _, initial, _ in calls} == {SEED}
    # Each call starts from the same RNG state, not a continuing stream.
    assert len({draw for _, _, draw in calls}) == 1


def test_engines_without_seed_support_are_not_sent_one():
    calls = []
    _backend_render(LONG, calls, accepts_seed=False)
    assert {seed for seed, _, _ in calls} == {None}
    assert {initial for _, initial, _ in calls} == {SEED}


@pytest.mark.parametrize("text", [LONG, PAUSED], ids=["chunks", "pause-spans"])
def test_native_chunks_and_spans_restart_from_the_take_seed(text, monkeypatch):
    from api.routers import generation
    from services import tts_backend

    calls = []

    def fake_generate(model, **kw):
        calls.append((torch.initial_seed(), torch.rand(1).item()))
        return [torch.ones(1, 2400) * 0.1]

    monkeypatch.setattr(tts_backend, "generate_with_cached_ref", fake_generate)
    model = type("Model", (), {"sampling_rate": 24000})()
    generation._run_inference(model, text, "English", None, None, None, None,
                              32, 2, 1, None, True, True, None, None, None,
                              SEED, "raw", 60, 0)
    assert len(calls) >= 3
    assert {initial for initial, _ in calls} == {SEED}
    assert len({draw for _, draw in calls}) == 1


def test_seed_engine_call_contract():
    from services.tts_backend import seed_engine_call

    assert seed_engine_call(None, _probe([])) == {}
    assert seed_engine_call(7, _probe([])) == {"seed": 7}
    assert torch.initial_seed() == 7
    assert seed_engine_call(8, _probe([], accepts_seed=False)) == {}
    assert seed_engine_call(9) == {}
    assert torch.initial_seed() == 9


def test_no_render_path_advances_or_drops_the_take_seed():
    """Recurrence guard for the whole class: /generate, streaming, Batch and
    Dub all seed their engine calls through ``seed_engine_call``."""
    import re
    from pathlib import Path

    routers = Path(__file__).resolve().parents[1] / "backend" / "api" / "routers"
    for name in ("generation.py", "batch.py", "dub_generate.py"):
        source = (routers / name).read_text(encoding="utf-8")
        assert not re.search(r"seed[\"'\]]*\s*\+\s*(i|chunk_index|idx)\b", source), name
        assert "seed_engine_call(" in source, name
