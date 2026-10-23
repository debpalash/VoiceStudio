"""Shared support for the OmniVoice GGUF cache tests (#3042).

Both the offline-cache suite and the CPU-policy suite need the same
things: the pinned quant map, an isolated HuggingFace cache, a
stand-in runtime binary and a way to install a cached pair. They live
here so the two suites cannot drift apart.

    from gguf_cache_fixtures import (
        CPU_PAIR,
        gguf_binary,
        gguf_cache,
        gguf_engine,
        install_pair,
    )

The fixtures carry prefixed names on purpose: they stay module-scoped
to the suites that import them, and they never shadow pytest's own
built-in ``cache`` fixture.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
QUANT_MAP = json.loads(
    (REPO / "backend" / "engines" / "omnivoice_gguf" / "quant_map.json").read_text(
        encoding="utf-8"
    )
)
REVISION = QUANT_MAP["_meta"]["source_commit_sha"]
REPO_DIR = f"models--{QUANT_MAP['_meta']['source_model'].replace('/', '--')}"
CPU_PAIR = (QUANT_MAP["cpu"]["base"], QUANT_MAP["cpu"]["tokenizer"])
BF16_PAIR = (QUANT_MAP["high-vram"]["base"], QUANT_MAP["high-vram"]["tokenizer"])
Q8_PAIR = (QUANT_MAP["mid-vram"]["base"], QUANT_MAP["mid-vram"]["tokenizer"])

#: Environment variables that steer the compute policy. The baseline
#: fixture clears them so a saved Settings choice or an ambient native
#: backend cannot change what these tests observe.
COMPUTE_ENV = (
    "OMNIVOICE_DEVICE",
    "OMNIVOICE_GGUF_BACKEND",
    "OMNIVOICE_GGUF_DEVICE",
    "GGML_BACKEND",
)


@pytest.fixture
def gguf_engine():
    """The engine module under test."""
    import importlib

    return importlib.import_module("engines.omnivoice_gguf.backend")


@pytest.fixture
def gguf_cache(monkeypatch, tmp_path):
    """An empty HF cache, a clean compute environment and a download tripwire."""
    import huggingface_hub
    from huggingface_hub import constants

    root = tmp_path / "hub"
    root.mkdir()
    monkeypatch.setattr(constants, "HF_HUB_CACHE", str(root))
    for variable in COMPUTE_ENV:
        monkeypatch.delenv(variable, raising=False)
    # The env is authoritative over the saved Settings choice, so pinning
    # it to "auto" neutralizes a user's own CPU preference.
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")

    def _no_download(*args, **kwargs):
        raise AssertionError("the GGUF engine must not download weights")

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _no_download)
    monkeypatch.setattr(huggingface_hub, "snapshot_download", _no_download)
    return root


@pytest.fixture
def gguf_binary(monkeypatch, tmp_path, gguf_engine):
    """A stand-in runtime binary, so readiness is judged on the weights."""
    fake = tmp_path / "omnivoice-tts-linux-x86_64"
    fake.write_bytes(b"\x7fELF-real-enough")
    monkeypatch.setattr(gguf_engine, "_binary_path", lambda slug=None: fake)
    monkeypatch.setattr(gguf_engine, "_load_checksum_manifest", dict)
    monkeypatch.setattr(gguf_engine, "_is_macos_quarantined", lambda p: False)
    return fake


def install_pair(root: Path, filenames, *, revision: str = REVISION) -> None:
    """Cache one file per name at ``revision`` (the hub's snapshot layout)."""
    folder = root / REPO_DIR / "snapshots" / revision
    folder.mkdir(parents=True, exist_ok=True)
    for name in filenames:
        (folder / name).write_bytes(b"gguf-weights")


def choose_cpu(monkeypatch, source: str) -> None:
    """Apply one compute choice the way the app's compute policy reads it.

    ``source`` is "global" (``OMNIVOICE_DEVICE``), "settings" (the
    Settings pick), "engine" (``OMNIVOICE_GGUF_BACKEND``), "native" (an
    ambient ``GGML_BACKEND``) or "auto" (nothing pinned).
    """
    from core import prefs

    for variable in COMPUTE_ENV:
        monkeypatch.delenv(variable, raising=False)
    if source == "global":
        monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    elif source == "settings":
        monkeypatch.setattr(prefs, "resolve", lambda *a, **k: "cpu")
    elif source == "engine":
        monkeypatch.setenv("OMNIVOICE_GGUF_BACKEND", "cpu")
    elif source == "native":
        monkeypatch.setenv("GGML_BACKEND", "CPU")
    else:
        monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
