"""The GGUF engine prefers installed weights and fetches only what is missing (#3042).

Two halves of one contract: a host that already installed a complete
pair keeps using it (the engine never fetches a second quant because the
conservative automatic tier changed), and a fresh host still works on
first run — the selected pair is downloaded at the pinned revision on
first generate, and readiness does not wait for it.

The baseline cache fixture replaces the download entry points with a
tripwire, so any test that does not opt into a download fails loudly if
the engine reaches for the network.
"""
from __future__ import annotations

import pytest
from gguf_cache_fixtures import (
    BF16_PAIR,
    CPU_PAIR,
    QUANT_MAP,
    REPO,
    REPO_DIR,
    REVISION,
    gguf_binary,
    gguf_cache,
    gguf_engine,
    install_pair,
)

__all__ = ["gguf_binary", "gguf_cache", "gguf_engine"]


@pytest.fixture
def downloads(monkeypatch, gguf_cache):
    """Record first-use downloads and serve them from the test cache."""
    import huggingface_hub

    calls = []

    def _download(*, repo_id, filename, revision):
        calls.append((repo_id, filename, revision))
        path = gguf_cache / "downloaded" / filename
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"gguf-weights")
        return str(path)

    monkeypatch.setattr(huggingface_hub, "hf_hub_download", _download)
    return calls


def test_offline_install_command_matches_the_pinned_pair():
    """The pre-install command in the guide names the pinned pair and cache."""
    doc = (REPO / "docs" / "engines" / "omnivoice-gguf.md").read_text(encoding="utf-8")
    assert "hf download" in doc
    assert REVISION in doc
    for name in CPU_PAIR:
        assert name in doc
    assert "--cache-dir" in doc
    # The Windows default the app redirects to, spelled out for PowerShell.
    assert r"$env:LOCALAPPDATA\OmniVoice\hf_cache" in doc


def test_install_hint_does_not_duplicate_the_pin():
    """The revision lives in quant_map.json only; a copied pin would drift."""
    from services.tts_backend import _INSTALL_HINTS

    assert REVISION not in _INSTALL_HINTS["omnivoice-gguf"]


def test_installed_pair_is_reused_without_a_second_download(
    gguf_cache, gguf_binary, gguf_engine
):
    """A host that already installed BF16 keeps it: no fresh quant is fetched."""
    install_pair(gguf_cache, BF16_PAIR)
    engine = gguf_engine.OmniVoiceGGUFBackend()
    base, tokenizer, entry = engine._resolve_quant_paths()
    assert base.name == BF16_PAIR[0]
    assert tokenizer.name == BF16_PAIR[1]
    assert entry["base"] == BF16_PAIR[0]
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason


def test_fresh_install_is_ready_before_its_weights_arrive(
    gguf_cache, gguf_binary, gguf_engine
):
    """Readiness is the runtime's: a fresh host is not reported unavailable."""
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is True, reason


def test_first_generate_downloads_the_selected_pair_at_the_pin(
    gguf_cache, gguf_binary, gguf_engine, downloads
):
    """Nothing installed: the selected pair is fetched at the pinned revision."""
    base, tokenizer, entry = gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert (base.name, tokenizer.name) == CPU_PAIR
    assert entry["base"] == CPU_PAIR[0]
    assert downloads == [
        ("Serveurperso/OmniVoice-GGUF", CPU_PAIR[0], REVISION),
        ("Serveurperso/OmniVoice-GGUF", CPU_PAIR[1], REVISION),
    ]


def test_failed_download_names_the_offline_install_command(
    gguf_cache, gguf_binary, gguf_engine
):
    """An unreachable hub is reported with the pinned pre-install command."""
    with pytest.raises(RuntimeError) as excinfo:
        gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    reason = str(excinfo.value)
    assert "hf download" in reason
    assert REVISION in reason
    for name in CPU_PAIR:
        assert name in reason


def test_partial_pair_is_not_installed(gguf_cache, gguf_binary, gguf_engine, downloads):
    """Base without tokenizer is not an installation: the pair is fetched."""
    install_pair(gguf_cache, [CPU_PAIR[0]])
    gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert [call[1] for call in downloads] == list(CPU_PAIR)


def test_pair_at_another_revision_is_not_installed(
    gguf_cache, gguf_binary, gguf_engine
):
    """The pin is exact: a pair cached at another revision does not count."""
    install_pair(gguf_cache, CPU_PAIR, revision="0" * 40)
    with pytest.raises(RuntimeError):
        gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths(download=False)


def test_preferred_installed_quant_wins(gguf_cache, gguf_binary, gguf_engine):
    """With several pairs installed, the selected tier is used."""
    install_pair(gguf_cache, CPU_PAIR)
    install_pair(gguf_cache, BF16_PAIR)
    base, _tokenizer, _entry = gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert base.name == CPU_PAIR[0]


def test_override_only_f32_is_never_auto_selected(
    gguf_cache, gguf_binary, gguf_engine, downloads
):
    """F32 needs an explicit override: the automatic path fetches the CPU pair."""
    f32 = QUANT_MAP["_extras"]["f32"]
    install_pair(gguf_cache, (f32["base"], f32["tokenizer"]))
    base, _tokenizer, _entry = gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert base.name == CPU_PAIR[0]


def test_explicit_override_does_not_fall_back_to_another_quant(
    gguf_cache, gguf_binary, gguf_engine, downloads
):
    """An explicit override is honoured exactly: its own pair is fetched."""
    install_pair(gguf_cache, CPU_PAIR)
    f32 = QUANT_MAP["_extras"]["f32"]
    engine = gguf_engine.OmniVoiceGGUFBackend(quant_override=f32["base"])
    base, _tokenizer, _entry = engine._resolve_quant_paths()
    assert base.name == f32["base"]
    assert [call[1] for call in downloads] == [f32["base"], f32["tokenizer"]]


def test_explicit_override_uses_its_installed_pair(
    gguf_cache, gguf_binary, gguf_engine
):
    """The override's own pair is used when it is installed."""
    f32 = QUANT_MAP["_extras"]["f32"]
    install_pair(gguf_cache, (f32["base"], f32["tokenizer"]))
    engine = gguf_engine.OmniVoiceGGUFBackend(quant_override=f32["base"])
    base, tokenizer, _entry = engine._resolve_quant_paths()
    assert base.name == f32["base"]
    assert tokenizer.name == f32["tokenizer"]


def test_explicit_cpu_choice_keeps_the_cpu_tier(
    gguf_cache, gguf_binary, gguf_engine, monkeypatch
):
    """A CPU pin keeps the CPU tier as the target."""
    monkeypatch.setenv("OMNIVOICE_DEVICE", "cpu")
    install_pair(gguf_cache, CPU_PAIR)
    base, _tokenizer, _entry = gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert base.name == CPU_PAIR[0]


@pytest.mark.parametrize(
    "sabotage", ["directory", "empty_file", "broken_symlink", "missing_marker"]
)
def test_unusable_cache_entries_are_not_installed(
    gguf_cache, gguf_binary, gguf_engine, sabotage, symlink_or_skip
):
    """A directory, a zero-byte file, a dangling link and the hub's "known
    absent" marker are all unusable: none of them is an installed weight."""
    folder = gguf_cache / REPO_DIR / "snapshots" / REVISION
    folder.mkdir(parents=True)
    for name in CPU_PAIR:
        if sabotage == "directory":
            (folder / name).mkdir()
        elif sabotage == "empty_file":
            (folder / name).write_bytes(b"")
        elif sabotage == "broken_symlink":
            symlink_or_skip(str(folder / name), str(folder / "absent-blob"))
        else:
            marker = gguf_cache / REPO_DIR / ".no_exist" / REVISION
            marker.mkdir(parents=True, exist_ok=True)
            (marker / name).write_text("", encoding="utf-8")
    with pytest.raises(RuntimeError):
        gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths(download=False)


def test_resolving_installed_weights_writes_nothing_to_the_cache(
    gguf_cache, gguf_binary, gguf_engine
):
    """Resolving an installed pair is a read: it creates no cache entries."""
    install_pair(gguf_cache, CPU_PAIR)
    before = sorted(p.relative_to(gguf_cache).as_posix() for p in gguf_cache.rglob("*"))
    gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    after = sorted(p.relative_to(gguf_cache).as_posix() for p in gguf_cache.rglob("*"))
    assert after == before
