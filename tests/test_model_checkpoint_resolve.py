"""Regression tests for #693 — a leaked engine id ("omnivoice") in
OMNIVOICE_MODEL must not be passed to OmniVoice.from_pretrained() (which 500s
with "omnivoice is not a local folder and is not a valid model identifier").

The resolver self-heals: only a HF repo id (org/repo) or an existing local dir
is honored; anything else falls back to the default.
"""
import pytest

from services.omnivoice_checkpoint import (
    resolve_omnivoice_checkpoint,
    _DEFAULT_OMNIVOICE_CHECKPOINT,
)


def _set(monkeypatch, val):
    if val is None:
        monkeypatch.delenv("OMNIVOICE_MODEL", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_MODEL", val)


def test_default_when_unset(monkeypatch):
    _set(monkeypatch, None)
    assert resolve_omnivoice_checkpoint() == _DEFAULT_OMNIVOICE_CHECKPOINT


@pytest.mark.parametrize("leaked", ["omnivoice", "voxcpm2", "cosyvoice", "kittentts"])
def test_bare_engine_id_falls_back(monkeypatch, leaked):
    """#693: an engine id (no '/', not a path) must self-heal to the default."""
    _set(monkeypatch, leaked)
    assert resolve_omnivoice_checkpoint() == _DEFAULT_OMNIVOICE_CHECKPOINT


@pytest.mark.parametrize("repo", ["k2-fsa/OmniVoice", "some-org/some-model"])
def test_valid_hf_repo_id_is_kept(monkeypatch, repo):
    _set(monkeypatch, repo)
    assert resolve_omnivoice_checkpoint() == repo


def test_existing_local_dir_is_kept(monkeypatch, tmp_path):
    d = tmp_path / "mymodel"
    d.mkdir()
    _set(monkeypatch, str(d))
    assert resolve_omnivoice_checkpoint() == str(d)


def test_blank_or_whitespace_falls_back(monkeypatch):
    _set(monkeypatch, "   ")
    assert resolve_omnivoice_checkpoint() == _DEFAULT_OMNIVOICE_CHECKPOINT


def test_omnivoice_model_is_only_read_through_the_resolver():
    """#693 recurrence guard: the raw OMNIVOICE_MODEL env var may only be read
    inside resolve_omnivoice_checkpoint() (the resolver) and the personas export
    guard (which routes the value through the resolver). Any other raw read
    reintroduces the whole bug class — a bare engine id reaching a HF call, or a
    leaked value mislabeling a UI field / exported persona bundle."""
    import pathlib

    backend = pathlib.Path(__file__).resolve().parents[1] / "backend"
    allowed = {
        "services/model_manager.py",
        "services/omnivoice_checkpoint.py",  # the resolver itself
        "api/routers/personas.py",
        # Suite-wide sentinel SETTER (os.environ.setdefault("OMNIVOICE_MODEL",
        # "test")) — a write, not a raw read; the value is still only ever
        # read through the resolver.
        "tests/conftest.py",
    }
    offenders = []
    for py in backend.rglob("*.py"):
        if "__pycache__" in py.parts:
            continue
        rel = py.relative_to(backend).as_posix()
        if rel in allowed:
            continue
        for i, line in enumerate(py.read_text(encoding="utf-8").splitlines(), 1):
            # note the closing quote — excludes the unrelated OMNIVOICE_MODEL_LOAD_TIMEOUT
            if "environ" in line and ('OMNIVOICE_MODEL"' in line or "OMNIVOICE_MODEL'" in line):
                offenders.append(f"{rel}:{i}")
    assert not offenders, (
        "raw OMNIVOICE_MODEL reads outside the resolver (route them through "
        "resolve_omnivoice_checkpoint()): " + ", ".join(offenders)
    )
