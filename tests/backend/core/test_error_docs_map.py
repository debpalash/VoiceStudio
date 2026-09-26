"""Tests for backend/core/error_docs_map.py — error → docs URL taxonomy.

The locked taxonomy is the contract Phase 5's bug reporter consumes and
the TS-side `electron/src/shared/utils/errorDocsMap.ts` mirrors. These tests pin
both the keys and that every URL points back to the project repo.
"""
from __future__ import annotations


def test_known_class_returns_url():
    from core import error_docs_map
    url = error_docs_map.lookup("GATEKEEPER_QUARANTINE")
    assert "docs/install/macos.md#gatekeeper-quarantine" in url


def test_unknown_class_returns_default():
    from core import error_docs_map
    assert error_docs_map.lookup("NEVER_HEARD_OF_IT") == error_docs_map.DEFAULT_DOCS


def test_none_returns_default():
    from core import error_docs_map
    assert error_docs_map.lookup(None) == error_docs_map.DEFAULT_DOCS


def test_all_urls_resolve_to_repo():
    from core import error_docs_map
    from core import links
    base = links.PROJECT_REPO_BLOB_MAIN
    for cls, url in error_docs_map.ERROR_DOCS.items():
        assert url.startswith(base), f"{cls} URL not in repo blob: {url}"
    assert error_docs_map.DEFAULT_DOCS.startswith(base)


def test_all_keys_match_taxonomy():
    """The taxonomy is locked here. Adding a class is a contract
    change — bump this set + the TS mirror's keys-sync test in lockstep.

    PYANNOTE_LICENSE_REQUIRED was added for issue #78 (speaker detection
    fails when the pyannote model license has not been accepted on
    huggingface.co — distinct from a missing/invalid token, which is what
    HF_AUTH_FAILED covers).
    """
    from core import error_docs_map
    expected = {
        "GPU_ARCH_UNSUPPORTED",
        "WINDOWS_APP_CONTROL_BLOCKED",
        "AUDIO_IO_FAILED",

        "DIARIZATION_MODEL_MISSING",
        "DIARIZATION_LOAD_FAILED",
        "GATEKEEPER_QUARANTINE",
        "APPIMAGE_WEBKIT_WHITESCREEN",
        "PKG_RESOURCES_MISSING",
        "HF_AUTH_FAILED",
        "PYANNOTE_LICENSE_REQUIRED",
        "POCKETTTS_GATED_WEIGHTS",
    }
    assert set(error_docs_map.ERROR_DOCS.keys()) == expected


def test_pyannote_license_class_points_at_diarization_docs():
    """Issue #78: the diarization warning toast deeplinks to the
    `License acceptance flow` section of `docs/features/diarization.md`,
    NOT to the generic token-setup doc — that section is the one with
    the click-by-click instructions for accepting the gated-model
    license on huggingface.co."""
    from core import error_docs_map
    url = error_docs_map.lookup("PYANNOTE_LICENSE_REQUIRED")
    assert "docs/features/diarization.md" in url
    assert "license-acceptance-flow" in url


def test_pockettts_gate_points_at_pockettts_instructions():
    from core import error_docs_map
    url = error_docs_map.lookup("POCKETTTS_GATED_WEIGHTS")
    assert "docs/install/troubleshooting.md" in url
    assert "pockettts-gated-weights" in url
