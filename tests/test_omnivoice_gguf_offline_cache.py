"""The GGUF engine resolves weights from the local cache only (#3042).

Two halves of one contract: a host that already installed a complete
pair keeps using it (the engine never fetches a second quant because the
conservative automatic tier changed), and a host that installed nothing
is told exactly how to install the pinned pair instead of being
advertised as ready.

Every test runs against an empty cache with the download entry points
replaced by a tripwire, so a regression that reaches for the network
fails here rather than in a user's face.
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


def test_published_install_command_matches_the_pinned_pair():
    """The install command the user is told to run names the pinned pair."""
    from services.tts_backend import _INSTALL_HINTS

    hint = _INSTALL_HINTS["omnivoice-gguf"]
    doc = (REPO / "docs" / "engines" / "omnivoice-gguf.md").read_text(encoding="utf-8")
    for text in (hint, doc):
        assert "hf download" in text
        assert REVISION in text
        for name in CPU_PAIR:
            assert name in text


def test_published_install_command_targets_the_app_cache():
    """The download must land in the app's cache, so the steps say so."""
    from services.tts_backend import _INSTALL_HINTS

    hint = _INSTALL_HINTS["omnivoice-gguf"]
    doc = (REPO / "docs" / "engines" / "omnivoice-gguf.md").read_text(encoding="utf-8")
    for text in (hint, doc):
        assert "--cache-dir" in text
    # The Windows default the app redirects to, spelled out for PowerShell.
    assert r"$env:LOCALAPPDATA\OmniVoice\hf_cache" in doc


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


def test_missing_weights_are_unavailable_with_an_install_command(
    gguf_cache, gguf_binary, gguf_engine
):
    """Nothing installed: unavailable, and the reason carries the pinned command."""
    ok, reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False
    assert "hf download" in reason
    assert REVISION in reason
    for name in CPU_PAIR:
        assert name in reason


def test_partial_pair_is_not_installed(gguf_cache, gguf_binary, gguf_engine):
    """Base without tokenizer is not an installation."""
    install_pair(gguf_cache, [CPU_PAIR[0]])
    ok, _reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False


def test_pair_at_another_revision_is_not_installed(
    gguf_cache, gguf_binary, gguf_engine
):
    """The pin is exact: a pair cached at another revision does not count."""
    install_pair(gguf_cache, CPU_PAIR, revision="0" * 40)
    ok, _reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False


def test_preferred_installed_quant_wins(gguf_cache, gguf_binary, gguf_engine):
    """With several pairs installed, the selected tier is used."""
    install_pair(gguf_cache, CPU_PAIR)
    install_pair(gguf_cache, BF16_PAIR)
    base, _tokenizer, _entry = gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    assert base.name == CPU_PAIR[0]


def test_override_only_f32_is_never_auto_selected(
    gguf_cache, gguf_binary, gguf_engine
):
    """F32 needs an explicit override: the automatic fallback must skip it."""
    f32 = QUANT_MAP["_extras"]["f32"]
    install_pair(gguf_cache, (f32["base"], f32["tokenizer"]))
    ok, _reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False


def test_explicit_override_does_not_fall_back_to_another_quant(
    gguf_cache, gguf_binary, gguf_engine
):
    """An explicit override is honoured exactly or not at all."""
    install_pair(gguf_cache, CPU_PAIR)
    f32 = QUANT_MAP["_extras"]["f32"]
    engine = gguf_engine.OmniVoiceGGUFBackend(quant_override=f32["base"])
    with pytest.raises(RuntimeError) as excinfo:
        engine._resolve_quant_paths()
    assert "hf download" in str(excinfo.value)


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


def test_generation_reports_missing_weights_instead_of_downloading(
    gguf_cache, gguf_binary, gguf_engine
):
    """Generation never reaches the network: it reports the missing pair."""
    with pytest.raises(RuntimeError) as excinfo:
        gguf_engine.OmniVoiceGGUFBackend().generate("hello")
    assert "hf download" in str(excinfo.value)


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
    ok, _reason = gguf_engine.OmniVoiceGGUFBackend.is_available()
    assert ok is False


def test_resolving_weights_writes_nothing_to_the_cache(
    gguf_cache, gguf_binary, gguf_engine
):
    """Resolution is a read: it creates no cache entries of its own."""
    install_pair(gguf_cache, CPU_PAIR)
    before = sorted(p.relative_to(gguf_cache).as_posix() for p in gguf_cache.rglob("*"))
    gguf_engine.OmniVoiceGGUFBackend()._resolve_quant_paths()
    after = sorted(p.relative_to(gguf_cache).as_posix() for p in gguf_cache.rglob("*"))
    assert after == before
