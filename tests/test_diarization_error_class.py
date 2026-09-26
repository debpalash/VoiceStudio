"""Regression test for issue #78 — Speaker detection fails.

When pyannote diarization can't load (no token, gated-model license not
accepted, version mismatch, …) the dub pipeline silently falls back to a
silence-gap heuristic that mis-assigns speakers — the original bug
report's "person A speaks like person B" symptom. This test pins:

  1. `get_diarization_pipeline(return_error=True)` returns a structured
     sentinel that distinguishes "no token", "gated license", and
     "generic load failure".
  2. `_classify_diarization_error()` correctly maps a 401/gated-repo
     exception to the LICENSE bucket.
  3. The 5-class error_docs_map includes `PYANNOTE_LICENSE_REQUIRED` and
     deeplinks to the `License acceptance flow` section of the
     diarization docs.
  4. Backward compatibility: the bare-`None` return shape that the
     legacy `_transcribe` path (dub_core.py:781) calls is unchanged.

The actual pyannote model is never loaded — these are pure unit tests of
the classification + error-routing surface.
"""
from __future__ import annotations

import sys
from types import SimpleNamespace

import pytest


@pytest.fixture
def model_manager(monkeypatch):
    """Fresh import of services.model_manager with the diar pipeline cache
    cleared. We also reset `_torch` so `_lazy_torch()` is hermetic.

    Unconditional sys.modules purge — running this test after another that
    monkey-patched `services.token_resolver.resolve` (e.g. the smoke test)
    leaves a stale resolver bound inside `model_manager`'s local imports,
    so we force a fresh load. Same defensive pattern as `tests/smoke/`
    after PR #95.
    """
    # Don't pop services.token_resolver — the test body's `from services
    # import token_resolver` and the function body's `from services import
    # token_resolver` must resolve to the SAME module object, otherwise
    # monkeypatch.setattr binds on a different identity than the function
    # reads. Popping forces re-import which can create a fresh ID.
    for mod_name in ("core.config", "services.model_manager"):
        sys.modules.pop(mod_name, None)

    import services.model_manager as mm

    monkeypatch.setattr(mm, "_diar_pipeline", None)
    monkeypatch.setattr(mm, "_torch", None)
    return mm


# ---------------------------------------------------------------------------
# _classify_diarization_error — string heuristic that picks the bucket
# ---------------------------------------------------------------------------


class TestClassifyDiarizationError:
    def test_401_unauthorized_classified_as_license(self, model_manager):
        err = RuntimeError("HfHubHTTPError: 401 Client Error: Unauthorized")
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_403_classified_as_license(self, model_manager):
        err = RuntimeError("403 Forbidden: access blocked")
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_gated_repo_message_classified_as_license(self, model_manager):
        err = RuntimeError(
            "Cannot access gated repo for url https://huggingface.co/pyannote/speaker-diarization-3.1"
        )
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_accept_license_phrase_classified_as_license(self, model_manager):
        err = RuntimeError("You must accept the license to access this model")
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_accept_user_conditions_phrase_classified_as_license(self, model_manager):
        err = RuntimeError(
            "You need to share contact information to access this model. Please accept the user conditions."
        )
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_named_exception_class_classified_as_license(self, model_manager):
        # Replicates the actual class name shipped by recent huggingface_hub
        # without importing it (it's not stable across major versions).
        class GatedRepoError(Exception):
            pass

        err = GatedRepoError("repo is gated; permission denied")
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LICENSE

    def test_generic_torch_version_error_classified_as_load(self, model_manager):
        err = RuntimeError("CUDA out of memory: tried to allocate 2 GiB")
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LOAD

    def test_pickle_safety_error_classified_as_load(self, model_manager):
        err = RuntimeError(
            "Weights only load failed: Unsupported global: omegaconf.listconfig.ListConfig"
        )
        assert model_manager._classify_diarization_error(err) == model_manager.DIARIZATION_ERR_LOAD


# ---------------------------------------------------------------------------
# get_diarization_pipeline — public surface
# ---------------------------------------------------------------------------


class TestGetDiarizationPipeline:
    def test_no_token_returns_no_token_sentinel(self, model_manager, monkeypatch):
        # Force token_resolver.resolve() to return None.
        # Dotted-path setattr — identity-stable across sys.modules churn.
        monkeypatch.setattr("services.token_resolver.resolve", lambda skip=frozenset(): None)

        pipe, err = model_manager.get_diarization_pipeline(return_error=True)
        assert pipe is None
        assert err == model_manager.DIARIZATION_ERR_NO_TOKEN

    def test_no_token_legacy_shape_still_returns_bare_none(self, model_manager, monkeypatch):
        """The legacy `_transcribe` call site in dub_core.py:781 does
        `if get_diarization_pipeline():` — the new `return_error` kwarg
        must NOT break that. Pin the backward-compatible shape."""
        # Dotted-path setattr — identity-stable across sys.modules churn.
        monkeypatch.setattr("services.token_resolver.resolve", lambda skip=frozenset(): None)

        result = model_manager.get_diarization_pipeline()
        assert result is None  # bare None, not a tuple

    def test_license_failure_returns_license_sentinel(self, model_manager, monkeypatch):
        """Pipeline.from_pretrained raises a 401 → caller learns it's a
        license issue, not a generic load failure."""
        from services.token_resolver import ResolvedToken
        # Use dotted-path setattr so monkeypatch resolves `resolve` against
        # whatever `services.token_resolver` is currently in sys.modules.
        # The Wave 1 `fresh_resolver` fixture purges + re-imports services.*,
        # so binding via a local `from services import token_resolver` ref
        # may target a stale identity. The dotted-path form re-reads
        # sys.modules at setattr time and is identity-stable.
        monkeypatch.setattr(
            "services.token_resolver.resolve",
            lambda skip=frozenset(): ResolvedToken(token="hf_test", source="env", username="testuser"),
        )

        # Stub _lazy_torch so it doesn't try to import the real torch.
        monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(device=lambda d: d))

        from contextlib import contextmanager
        @contextmanager
        def local_config():
            yield "local-config.yaml"
        monkeypatch.setattr("services.diarization_local.local_pipeline_config", local_config)

        # Inject a fake pyannote.audio module whose Pipeline.from_pretrained
        # raises a 401-equivalent. Use sys.modules patching since
        # `from pyannote.audio import Pipeline` is done inside the function.
        class FakePipeline:
            @staticmethod
            def from_pretrained(*args, **kwargs):
                raise RuntimeError("401 Client Error: Unauthorized for gated repo")

        fake_pyannote_audio = SimpleNamespace(Pipeline=FakePipeline)
        monkeypatch.setitem(sys.modules, "pyannote", SimpleNamespace(audio=fake_pyannote_audio))
        monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote_audio)

        pipe, err = model_manager.get_diarization_pipeline(return_error=True)
        assert pipe is None
        assert err == model_manager.DIARIZATION_ERR_LICENSE

    def test_generic_load_failure_returns_load_sentinel(self, model_manager, monkeypatch):
        from services.token_resolver import ResolvedToken
        # Use dotted-path setattr so monkeypatch resolves `resolve` against
        # whatever `services.token_resolver` is currently in sys.modules.
        # The Wave 1 `fresh_resolver` fixture purges + re-imports services.*,
        # so binding via a local `from services import token_resolver` ref
        # may target a stale identity. The dotted-path form re-reads
        # sys.modules at setattr time and is identity-stable.
        monkeypatch.setattr(
            "services.token_resolver.resolve",
            lambda skip=frozenset(): ResolvedToken(token="hf_test", source="env", username="testuser"),
        )
        monkeypatch.setattr(model_manager, "_lazy_torch", lambda: SimpleNamespace(device=lambda d: d))

        from contextlib import contextmanager
        @contextmanager
        def local_config():
            yield "local-config.yaml"
        monkeypatch.setattr("services.diarization_local.local_pipeline_config", local_config)

        class FakePipeline:
            @staticmethod
            def from_pretrained(*args, **kwargs):
                raise RuntimeError("Weights only load failed: pickle global denied")

        fake_pyannote_audio = SimpleNamespace(Pipeline=FakePipeline)
        monkeypatch.setitem(sys.modules, "pyannote", SimpleNamespace(audio=fake_pyannote_audio))
        monkeypatch.setitem(sys.modules, "pyannote.audio", fake_pyannote_audio)

        pipe, err = model_manager.get_diarization_pipeline(return_error=True)
        assert pipe is None
        assert err == model_manager.DIARIZATION_ERR_LOAD


# ---------------------------------------------------------------------------
# error_docs_map → docs deeplink (closes the loop with the SSE warning)
# ---------------------------------------------------------------------------


class TestErrorDocsDeeplink:
    def test_pyannote_license_required_deeplinks_to_diarization_section(self):
        from core import error_docs_map
        url = error_docs_map.lookup("PYANNOTE_LICENSE_REQUIRED")
        assert "docs/features/diarization.md" in url
        assert "license-acceptance-flow" in url

    def test_pyannote_license_required_is_in_locked_taxonomy(self):
        """If this test fails, the 5-class taxonomy was bumped without
        also bumping the TS mirror — see electron/src/shared/utils/errorDocsMap.ts
        and its keys-sync test."""
        from core import error_docs_map
        assert "PYANNOTE_LICENSE_REQUIRED" in error_docs_map.ERROR_DOCS
