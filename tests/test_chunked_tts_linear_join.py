"""concatenate_audio_chunks / join_rendered_chunks: linear-time join."""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))

import pytest

torch = pytest.importorskip("torch")
from services import chunked_tts as ct  # noqa: E402

SR = 24000


def reference(chunks, sr, ms):
    cs = int(sr * ms / 1000)
    result = chunks[0]
    for c in chunks[1:]:
        c = c.to(result.device, result.dtype)
        ov = min(cs, result.shape[-1], c.shape[-1])
        if ov > 0:
            fo = torch.linspace(1, 0, ov, dtype=result.dtype)
            fi = torch.linspace(0, 1, ov, dtype=result.dtype)
            mid = result[..., -ov:] * fo + c[..., :ov] * fi
            result = torch.cat([result[..., :-ov], mid, c[..., ov:]], dim=-1)
        else:
            result = torch.cat([result, c], dim=-1)
    return result


def rnd(*shape):
    return torch.randn(*shape)


CASES = {
    "even": ([rnd(4000) for _ in range(5)], 50),
    "uneven": ([rnd(n) for n in (5, 50000, 7, 3, 120000, 1, 900)], 50),
    "short": ([rnd(100) for _ in range(30)], 50),
    "multi": ([rnd(2, n) for n in (3000, 10, 5000)], 50),
    "hard": ([rnd(3000) for _ in range(4)], 0),
    "negative": ([rnd(3000) for _ in range(4)], -20),
    "two": ([rnd(5000), rnd(6000)], 50),
}


@pytest.mark.parametrize("name", CASES)
def test_matches_reference(name):
    chunks, ms = CASES[name]
    out = ct.concatenate_audio_chunks(list(chunks), SR, crossfade_ms=ms)
    ref = reference(chunks, SR, ms)
    assert out.shape == ref.shape
    assert torch.allclose(out, ref, atol=1e-6)


def test_no_uninitialised_samples():
    chunks = [torch.full((n,), float(v)) for n, v in ((300, 1), (200, 2), (400, 3))]
    out = ct.concatenate_audio_chunks(chunks, SR, crossfade_ms=0)
    assert out.shape[-1] == 900
    assert set(out.unique().tolist()) <= {1.0, 2.0, 3.0}


def test_single_cat_at_most(monkeypatch):
    calls = []
    real = torch.cat
    monkeypatch.setattr(torch, "cat", lambda *a, **k: (calls.append(1), real(*a, **k))[1])
    ct.concatenate_audio_chunks([rnd(2000) for _ in range(64)], SR)
    assert len(calls) <= 1


def test_join_builds_one_set(monkeypatch):
    built = []

    def counting_set(*a):
        built.append(1)
        return set(*a)

    monkeypatch.setattr(ct, "set", counting_set, raising=False)
    items = [None if i % 2 else torch.zeros(SR) for i in range(40)]
    out = ct.join_rendered_chunks(items, SR, crossfade_ms=0)
    assert len(built) == 1
    assert out.shape[-1] == 20 * SR


def test_all_dropped_gives_none():
    assert ct.join_rendered_chunks([None, torch.zeros(0)], SR) is None
