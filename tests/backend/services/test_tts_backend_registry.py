"""Tests for backend/services/tts_backend.py::list_backends — Plan 02-01 Task 2.

ENGINE-05 closes when no single backend's `is_available()` exception can
take down the picker. ENGINE-06 data is delivered via the `last_error` and
`isolation_mode` fields on each entry.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Iterator
from unittest.mock import MagicMock

import pytest

# tests/conftest.py prepends ./backend to sys.path so `services.*` resolves.
from services import tts_backend
from services.subprocess_backend import SubprocessBackend
from services.tts_backend import TTSBackend, list_backends


REPO_ROOT = Path(__file__).resolve().parents[3]
ECHO_SCRIPT = REPO_ROOT / "backend" / "engines" / "_echo" / "main.py"


# ── helpers — context-managed registry mutations ───────────────────────────


@pytest.fixture
def registry_sandbox() -> Iterator[dict]:
    """Snapshot _REGISTRY + _LAST_ERRORS, yield, then restore.

    Every test that injects a fake backend uses this so registrations from
    one test never leak into another (the production registry must keep
    the same nine engine shape between runs).
    """
    saved_registry = dict(tts_backend._REGISTRY)
    saved_errors = dict(tts_backend._LAST_ERRORS)
    try:
        yield tts_backend._REGISTRY
    finally:
        tts_backend._REGISTRY.clear()
        tts_backend._REGISTRY.update(saved_registry)
        tts_backend._LAST_ERRORS.clear()
        tts_backend._LAST_ERRORS.update(saved_errors)


# ── synthetic backends used across tests ───────────────────────────────────


class BrokenBackend(TTSBackend):
    id = "broken"
    display_name = "Broken (test)"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        raise RuntimeError("kaboom")

    def generate(self, text: str, **kw):
        raise NotImplementedError


class HealthyInProcessBackend(TTSBackend):
    id = "healthy-inproc"
    display_name = "Healthy (in-process test)"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["en"]

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "ready"

    def generate(self, text: str, **kw):
        raise NotImplementedError


class FakeSubBackend(SubprocessBackend):
    id = "fake-sub"
    display_name = "Fake Subprocess (test)"

    @property
    def sample_rate(self) -> int:
        return 24000

    @property
    def supported_languages(self) -> list[str]:
        return ["multi"]

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "ready"

    @classmethod
    def venv_python(cls) -> Path:
        return Path(sys.executable)

    @classmethod
    def sidecar_script(cls) -> Path:
        return ECHO_SCRIPT


# ── ENGINE-05 — graceful degradation ───────────────────────────────────────


def test_list_backends_resilient(registry_sandbox, caplog):
    """A BrokenBackend.is_available() that raises must NOT take down the list."""
    registry_sandbox["broken"] = BrokenBackend
    out = list_backends()

    by_id = {entry["id"]: entry for entry in out}
    assert "broken" in by_id
    entry = by_id["broken"]
    assert entry["available"] is False
    assert entry["reason"] == "Availability probe failed; check the backend log."
    assert entry["last_error"] == "Availability probe failed; check the backend log."
    assert "kaboom" not in caplog.text
    assert "availability probe failed for registered backend broken" in caplog.text

    # And every production backend still appears.
    expected = {
        "omnivoice", "cosyvoice", "kittentts", "mlx-audio", "voxcpm2",
        "moss-tts-nano", "indextts2", "gpt-sovits", "sherpa-onnx",
    }
    assert expected.issubset(by_id.keys()), (
        f"missing: {expected - by_id.keys()}"
    )


def test_unavailable_backend_can_report_installed_runtime_targets(
    registry_sandbox,
):
    class RuntimeKnownButModelMissing(HealthyInProcessBackend):
        id = "runtime-known"
        display_name = "Runtime known"

        @classmethod
        def is_available(cls):
            return False, "model missing"

        @classmethod
        def runtime_compute_profile(cls, caps):
            return {
                "gpu_compat": ("vulkan", "cpu"),
                "min_vram_gb": 6.0,
                "effective_device": "vulkan",
                "routing_status": "accelerated",
                "routing_reason": None,
                "runtime_backend": "vulkan",
                "runtime_device_index": 1,
                "runtime_device_name": "Test GPU",
            }

    registry_sandbox["runtime-known"] = RuntimeKnownButModelMissing
    entry = next(
        item for item in list_backends() if item["id"] == "runtime-known"
    )

    assert entry["available"] is False
    assert entry["gpu_compat"] == ["vulkan", "cpu"]
    assert entry["effective_device"] == "vulkan"


def test_list_backends_shape(registry_sandbox):
    """Every entry must contain exactly the documented keys — no more, no
    less — EXCEPT mlx-audio, which also carries `curated_models` +
    `active_model_id` (#981): it multiplexes 7+ curated models behind one
    backend id, so the Settings picker needs the roster + current pick."""
    out = list_backends()
    # `gpu_compat` joined the documented shape in Plan 02-04 alongside the
    # Engine Compatibility Matrix UI (ENGINE-06). The three routing keys
    # (effective_device / routing_status / routing_reason) joined in #21 so the
    # matrix can show the device each engine actually uses on THIS host.
    required = {
        "id", "display_name", "available", "reason",
        "install_hint", "last_error", "isolation_mode", "gpu_compat",
        "effective_device", "routing_status", "routing_reason",
        # Copy-paste env-var line for path-gated opt-in engines (None otherwise).
        "setup_snippet",
        # Available-but-has-advice (the "ready — <advice>" convention) — None
        # unless the engine is available AND its message carries advice.
        "hint",
        # Cloning capability: bool from the class attr, None when
        # model-dependent (a property, e.g. mlx-audio).
        "supports_cloning",
        # Graded-emotion capability (#1208): bool from the class attr; drives
        # the Audiobook expressive panel's emotion gate.
        "supports_emotion",
        # Reference-length truth (#2281): seconds of a clone clip the engine
        # uses and how it picks them; None when not verified in-repo.
        "max_ref_seconds", "ref_strategy",
        # True when services.sidecar_install can provision the engine in-app
        # (the Settings Install button keys off this).
        "one_click_install",
        # Approximate VRAM (GB) the engine wants on a dedicated GPU, or None
        # when it declares no measured floor (#1226). Advisory metadata: a host
        # below the floor gets a caveat in `routing_reason` BEFORE it spends
        # the full compute budget finding out its card is too small.
        "min_vram_gb",
        # Structured estimated/measured storage costs for pre-install
        # decisions and post-install accounting (#1718).
        "disk_usage",
        # Sanitized actual-vs-declared provider/device evidence (#1717).
        "execution_evidence",
        # Public URL of the engine's docs page, or None when it has none
        # (#1866). The unavailable-engine row uses it for its Learn more link,
        # so an engine that gains a registry entry without a doc silently
        # loses that link — tests/test_engine_docs.py guards the mapping.
        "docs_url",
    }
    mlx_audio_extra = {"curated_models", "active_model_id"}
    for entry in out:
        expected = required | mlx_audio_extra if entry["id"] == "mlx-audio" else required
        assert set(entry.keys()) == expected, (
            f"entry {entry.get('id')} has wrong keys: "
            f"missing {expected - entry.keys()}, "
            f"extra {entry.keys() - expected}"
        )
        # #1208: supports_emotion is always a concrete bool (never a descriptor).
        assert isinstance(entry["supports_emotion"], bool)


def test_mlx_audio_curated_models_roster(registry_sandbox):
    """#981 — mlx-audio's entry carries the curated-model roster + the
    currently-active pick, so Settings can render a model picker instead of
    always silently defaulting to Kokoro."""
    out = {entry["id"]: entry for entry in list_backends()}
    entry = out["mlx-audio"]
    assert entry["active_model_id"] == "kokoro"  # DEFAULT_MODEL_KEY, no prefs set
    keys = {m["key"] for m in entry["curated_models"]}
    assert keys == set(tts_backend.MLXAudioBackend.CURATED_MODELS)
    for m in entry["curated_models"]:
        assert set(m.keys()) == {"key", "label", "repo_id"}
        assert m["repo_id"] == tts_backend.MLXAudioBackend.CURATED_MODELS[m["key"]]
        assert m["label"]  # non-empty, readable


def test_mlx_audio_active_model_id_reflects_prefs(registry_sandbox, monkeypatch, tmp_path):
    from core import prefs as _prefs
    monkeypatch.setattr(_prefs, "_PREFS_PATH", str(tmp_path / "prefs.json"))
    monkeypatch.delenv("OMNIVOICE_MLX_AUDIO_MODEL", raising=False)
    _prefs.set_("mlx_audio_model_id", "outetts")
    out = {entry["id"]: entry for entry in list_backends()}
    assert out["mlx-audio"]["active_model_id"] == "outetts"


def test_curated_models_not_present_on_other_backends(registry_sandbox):
    """Only mlx-audio multiplexes multiple models behind one backend id — no
    other entry should carry curated_models/active_model_id."""
    out = list_backends()
    for entry in out:
        if entry["id"] == "mlx-audio":
            continue
        assert "curated_models" not in entry
        assert "active_model_id" not in entry


def test_isolation_mode_in_process_vs_subprocess(registry_sandbox):
    """SubprocessBackend subclasses get isolation_mode='subprocess'; others 'in-process'."""
    registry_sandbox["fake-sub"] = FakeSubBackend
    registry_sandbox["healthy-inproc"] = HealthyInProcessBackend
    out = {entry["id"]: entry for entry in list_backends()}

    assert out["fake-sub"]["isolation_mode"] == "subprocess"
    assert out["healthy-inproc"]["isolation_mode"] == "in-process"
    # The pre-existing OmniVoice backend is in-process — sanity check.
    assert out["omnivoice"]["isolation_mode"] == "in-process"


def test_last_error_cleared_after_recovery(registry_sandbox):
    """First call raises → last_error populated. Second call returns ok →
    last_error cleared. The field must reflect MOST-RECENT failure, not stale."""
    state = {"calls": 0}

    class FlakyBackend(TTSBackend):
        id = "flaky"
        display_name = "Flaky (test)"

        @property
        def sample_rate(self) -> int:
            return 24000

        @property
        def supported_languages(self) -> list[str]:
            return ["en"]

        @classmethod
        def is_available(cls) -> tuple[bool, str]:
            state["calls"] += 1
            if state["calls"] == 1:
                raise RuntimeError("first call boom")
            return True, "ready"

        def generate(self, text: str, **kw):
            raise NotImplementedError

    registry_sandbox["flaky"] = FlakyBackend

    # First listing — failure populates last_error.
    first = {e["id"]: e for e in list_backends()}
    assert first["flaky"]["available"] is False
    assert first["flaky"]["last_error"] == (
        "Availability probe failed; check the backend log."
    )

    # Second listing — success clears the cache.
    second = {e["id"]: e for e in list_backends()}
    assert second["flaky"]["available"] is True
    assert second["flaky"]["reason"] is None
    assert second["flaky"]["last_error"] is None


def test_existing_engines_still_listed():
    """Sanity: the wrap must not silently drop entries. We expect all nine
    in-tree engines unchanged."""
    out = list_backends()
    ids = {entry["id"] for entry in out}
    expected = {
        "omnivoice", "cosyvoice", "kittentts", "mlx-audio", "voxcpm2",
        "moss-tts-nano", "indextts2", "gpt-sovits", "sherpa-onnx",
    }
    assert expected.issubset(ids), f"missing entries: {expected - ids}"
    assert len(out) >= 9


def test_install_hint_preserved():
    """install_hint passthrough — Phase 1's tooltips must still render."""
    out = {entry["id"]: entry for entry in list_backends()}
    assert "kittentts" in out
    # The pre-existing _INSTALL_HINTS dict carries this one.
    assert out["kittentts"]["install_hint"] is not None
    assert "kittentts" in out["kittentts"]["install_hint"].lower()


# ── `hint` — available-but-has-advice (the "ready — <advice>" convention) ──
#
# Regression: list_backends() used to drop the whole is_available() message
# for available rows (`reason` is None when ok), so VoxCPM2's ">=2.0.3"
# upgrade hint never reached the UI. The additive `hint` field carries it.


class AdvisedBackend(HealthyInProcessBackend):
    id = "advised"
    display_name = "Advised (test)"

    @classmethod
    def is_available(cls) -> tuple[bool, str]:
        return True, "ready — installed foo 1.0 is older than 2.0; upgrading is recommended"


def test_hint_surfaces_advice_for_available_backend(registry_sandbox):
    registry_sandbox["advised"] = AdvisedBackend
    out = {e["id"]: e for e in list_backends()}
    entry = out["advised"]
    assert entry["available"] is True
    assert entry["reason"] is None  # documented ok-row behavior unchanged
    assert entry["hint"] == (
        "installed foo 1.0 is older than 2.0; upgrading is recommended"
    )


def test_hint_none_for_plain_ready_and_unavailable_rows(registry_sandbox):
    registry_sandbox["healthy-inproc"] = HealthyInProcessBackend
    registry_sandbox["broken"] = BrokenBackend
    out = {e["id"]: e for e in list_backends()}
    assert out["healthy-inproc"]["hint"] is None  # plain "ready"
    assert out["broken"]["hint"] is None          # unavailable → reason, not hint


def test_available_hint_extraction_rules():
    """Unit contract for the parser itself (the whole class of messages)."""
    f = tts_backend._available_hint
    assert f("ready — upgrade recommended") == "upgrade recommended"
    assert f("ready") is None
    assert f("ready (api_v2 server reachable)") is None  # parenthetical ≠ advice
    assert f("ready — ") is None                       # empty advice
    assert f("loaded — from cache") is None            # convention needs "ready"
    assert f(None) is None
    assert f(42) is None


def test_available_hint_masks_hf_tokens():
    # Assemble the fake token at runtime so no HF-token-shaped literal ever
    # lives in this file (GitHub push protection scans file content).
    fake_token = "hf_" + "A" * 34
    masked = tts_backend._available_hint(f"ready — re-auth with {fake_token}")
    assert masked is not None
    assert fake_token not in masked


# ── `supports_cloning` exposure ─────────────────────────────────────────────


def test_supports_cloning_true_false_and_model_dependent(registry_sandbox):
    class NoCloneBackend(HealthyInProcessBackend):
        id = "no-clone"
        display_name = "NoClone (test)"
        supports_cloning = False

    registry_sandbox["no-clone"] = NoCloneBackend
    registry_sandbox["healthy-inproc"] = HealthyInProcessBackend
    out = {e["id"]: e for e in list_backends()}

    # Plain bool class attrs pass through…
    assert out["no-clone"]["supports_cloning"] is False
    assert out["healthy-inproc"]["supports_cloning"] is True  # TTSBackend default
    assert out["omnivoice"]["supports_cloning"] is True
    # …but a property (model-dependent, mlx-audio) must report None — the
    # descriptor object itself is always truthy, so passing it through would
    # be a false "clones" claim (same guard as cloning_capable_engine_ids).
    assert out["mlx-audio"]["supports_cloning"] is None
