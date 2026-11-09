"""An explicit CPU choice keeps the CPU quant policy (#3042).

The automatic selection may reuse an already-installed larger quant, but
a host that explicitly asked for the CPU must not silently run the
bigger cached model instead: the CPU tier's quant is the contract. The
CPU choice itself comes from the existing compute policy (the Settings
pick, the global ``OMNIVOICE_DEVICE`` pin, the engine-level
``OMNIVOICE_GGUF_BACKEND`` override or an ambient native
``GGML_BACKEND``), and it never changes the native compute device.
"""
from __future__ import annotations

import pytest
from gguf_cache_fixtures import (
    BF16_PAIR,
    CPU_PAIR,
    Q8_PAIR,
    REVISION,
    choose_cpu,
    gguf_binary,
    gguf_cache,
    gguf_engine,
    install_pair,
)

__all__ = ["gguf_binary", "gguf_cache", "gguf_engine"]

CPU_SOURCES = ["global", "settings", "engine", "native"]


@pytest.mark.parametrize("source", CPU_SOURCES)
@pytest.mark.parametrize("cached_pair", [BF16_PAIR, Q8_PAIR], ids=["bf16", "q8"])
def test_explicit_cpu_requires_the_cpu_quant(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch, source, cached_pair
):
    """Only the CPU quant counts once the user pinned the CPU."""
    choose_cpu(monkeypatch, source)
    install_pair(gguf_cache, cached_pair)

    with pytest.raises(RuntimeError) as excinfo:
        gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths(download=False)
    reason = str(excinfo.value)
    for name in CPU_PAIR:
        assert name in reason
    assert REVISION in reason


@pytest.mark.parametrize("source", CPU_SOURCES)
def test_explicit_cpu_generation_reports_the_missing_cpu_quant(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch, source
):
    """Generation fetches the CPU quant rather than substituting the larger
    cached one; when that fetch fails it reports the CPU pair to install."""
    choose_cpu(monkeypatch, source)
    install_pair(gguf_cache, BF16_PAIR)

    engine = gguf_engine.OmniVoiceGGUFBackend()
    with pytest.raises(RuntimeError) as excinfo:
        engine._resolve_quant_paths()
    message = str(excinfo.value)
    assert "hf download" in message
    for name in CPU_PAIR:
        assert name in message


@pytest.mark.parametrize("source", CPU_SOURCES)
def test_explicit_cpu_stays_on_the_cpu_device(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch, source
):
    """The CPU choice still reaches the native runtime as CPU."""
    choose_cpu(monkeypatch, source)
    install_pair(gguf_cache, CPU_PAIR)
    assert gguf_engine._spawn_env()["GGML_BACKEND"] == "CPU"


@pytest.mark.parametrize("source", CPU_SOURCES)
def test_explicit_quant_override_wins_over_the_cpu_policy(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch, source
):
    """An explicit override stays authoritative on a CPU-pinned host."""
    choose_cpu(monkeypatch, source)
    install_pair(gguf_cache, BF16_PAIR)

    engine = gguf_engine.OmniVoiceGGUFBackend(quant_override=BF16_PAIR[0])
    base, tokenizer, entry = engine._resolve_quant_paths()
    assert base.name == BF16_PAIR[0]
    assert tokenizer.name == BF16_PAIR[1]
    assert entry["base"] == BF16_PAIR[0]
    assert gguf_engine._spawn_env()["GGML_BACKEND"] == "CPU"


def test_automatic_selection_still_reuses_an_installed_pair(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch
):
    """Without an explicit CPU choice the compatibility fallback stands."""
    choose_cpu(monkeypatch, "auto")
    install_pair(gguf_cache, BF16_PAIR)

    engine = gguf_engine.OmniVoiceGGUFBackend()
    base, _tokenizer, _entry = engine._resolve_quant_paths()
    assert base.name == BF16_PAIR[0]
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason


def test_installed_cpu_quant_is_used_when_nothing_else_is_cached(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch
):
    """The CPU pair itself is always the first choice on a CPU-pinned host."""
    choose_cpu(monkeypatch, "global")
    install_pair(gguf_cache, CPU_PAIR)

    engine = gguf_engine.OmniVoiceGGUFBackend()
    base, _tokenizer, _entry = engine._resolve_quant_paths()
    assert base.name == CPU_PAIR[0]
