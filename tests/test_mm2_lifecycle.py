"""MM2 model-management cleanup — unload contract, lifecycle facade, config,
cooldown bounding, per-role weight validation, sidecar VRAM surfacing.

Top-level (not under tests/backend/) on purpose: adding files there reorders
collection and can expose a pre-existing sys.modules-isolation leak in other
backend fixtures (see tests/test_fdl_*).
"""
from __future__ import annotations

import asyncio
import importlib
import os
import sys

import pytest

import services.tts_backend as tb
import services.model_lifecycle as ml
import services.model_manager as mm
import services.subprocess_backend as sb
import api.routers.setup.download as dl


def _run(coro):
    return asyncio.run(coro)


# ── MM2-01 / MM2-02: registry reuse + unload-on-switch ──────────────────────

def _fake_backend(calls):
    class Fake(tb.TTSBackend):
        id = "fake-mm2"
        display_name = "Fake"
        @property
        def sample_rate(self): return 24000
        @property
        def supported_languages(self): return ["multi"]
        @classmethod
        def is_available(cls): return True, "ok"
        def generate(self, *a, **k): ...
        def unload(self): calls["unload"] += 1
    return Fake


def test_active_instance_reused_for_same_id(monkeypatch):
    tb.reset_active_backend()
    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    a = tb.get_active_tts_backend()
    b = tb.get_active_tts_backend()
    assert a is b
    tb.reset_active_backend()


def test_model_status_reports_resident_checkpoint_identity(monkeypatch):
    class _Model:
        _voicestudio_checkpoint = "k2-fsa/resident-model"
        _voicestudio_loaded_at = "2026-09-13T12:34:56Z"

    monkeypatch.setattr(mm, "model", _Model())

    status = mm.get_model_status()

    assert status["status"] == "ready"
    assert status["loaded"] is True
    assert status["checkpoint"] == "k2-fsa/resident-model"
    assert status["loaded_at"] == "2026-09-13T12:34:56Z"


def test_model_status_clears_resident_identity_when_idle(monkeypatch):
    monkeypatch.setattr(mm, "model", None)

    status = mm.get_model_status()

    assert status["status"] == "idle"
    assert status["loaded"] is False
    assert "checkpoint" not in status
    assert "loaded_at" not in status


def test_switch_unloads_previous_engine(monkeypatch):
    calls = {"unload": 0}
    tb._REGISTRY["fake-mm2"] = _fake_backend(calls)
    tb.reset_active_backend()
    monkeypatch.setattr(tb, "active_backend_id", lambda: "fake-mm2")
    tb.get_active_tts_backend()
    monkeypatch.setattr(tb, "active_backend_id", lambda: "omnivoice")
    tb.get_active_tts_backend()
    assert calls["unload"] == 1
    tb.reset_active_backend()
    tb._REGISTRY.pop("fake-mm2", None)


def test_reset_active_backend_is_idempotent_and_unloads():
    calls = {"unload": 0}
    tb._active_instance = _fake_backend(calls)()
    tb._active_instance_id = "fake-mm2"
    tb.reset_active_backend()
    tb.reset_active_backend()  # second call no-ops
    assert calls["unload"] == 1
    assert tb._active_instance is None


def test_omnivoice_unload_idempotent_and_preload_safe():
    b = tb.OmniVoiceBackend()
    b.unload()
    b.unload()  # twice, and before any generate() — must not raise


# ── MM2-04 / MM2-03: lifecycle facade + honest ASR ──────────────────────────

def test_list_loaded_empty(monkeypatch):
    monkeypatch.setattr(mm, "model", None)
    monkeypatch.setattr(mm, "_diar_pipeline", None)
    # list_loaded() also reads these process-wide registries (sidecars, engines,
    # the active backend, the dictation ASR and the offline translator); an earlier test on
    # the same pytest-xdist worker can leave an engine or sidecar behind, which
    # made this test pass serially and fail depending on worker placement.
    monkeypatch.setattr("services.subprocess_backend.list_live_sidecars", lambda: [])
    monkeypatch.setattr("services.asr_backend._capture_backend", None)
    monkeypatch.setattr("api.routers.dub_translate._nllb_model", None)
    monkeypatch.setattr("api.routers.engines._ENGINE_INSTANCES", {})
    monkeypatch.setattr("services.tts_backend._active_instance", None)
    monkeypatch.setattr("services.tts_backend._active_instance_id", None)
    out = ml.list_loaded()
    assert out == {"models": [], "count": 0} or out["count"] == 0


def test_list_loaded_asr_row_is_honest(monkeypatch):
    class _Model:
        _asr_pipe = object()
        def parameters(self): raise StopIteration
    monkeypatch.setattr(mm, "model", _Model())
    monkeypatch.setattr(mm, "_diar_pipeline", None)
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert "asr" in rows
    asr = rows["asr"]
    assert asr["unloadable"] is False
    assert asr.get("note")  # explains the disabled unload button


def test_list_loaded_attributes_resident_tts_to_its_engine(monkeypatch):
    # Field report: OmniVoice stays resident in VRAM after switching to
    # voxcpm2, and the panel offered no hint it wasn't the routed engine.
    class _Model:
        _asr_pipe = object()
        def parameters(self): raise StopIteration
    monkeypatch.setattr(mm, "model", _Model())
    monkeypatch.setattr(mm, "_diar_pipeline", None)

    # String-target setattr: other suites pop+reimport services.* modules
    # mid-run (see module docstring), so the collection-time `tb` alias can go
    # stale — patch the module object _active_tts_id late-imports at call time.
    monkeypatch.setattr("services.tts_backend.active_backend_id", lambda: "voxcpm2")
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["tts"]["engine_id"] == "omnivoice"
    assert rows["tts"]["is_active_engine"] is False
    # ASR isn't competing with the TTS selection — must not be mislabeled.
    assert "is_active_engine" not in rows["asr"]

    monkeypatch.setattr("services.tts_backend.active_backend_id", lambda: "omnivoice")
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["tts"]["is_active_engine"] is True


def test_list_loaded_attribution_failure_degrades(monkeypatch):
    # Attribution is advisory: a raising prefs layer must not break the
    # panel, just leave the active flag unknown.
    class _Model:
        _asr_pipe = None
        def parameters(self): raise StopIteration
    monkeypatch.setattr(mm, "model", _Model())
    monkeypatch.setattr(mm, "_diar_pipeline", None)
    def _boom():
        raise RuntimeError("prefs unavailable")
    monkeypatch.setattr("services.tts_backend.active_backend_id", _boom)
    rows = {m["id"]: m for m in ml.list_loaded()["models"]}
    assert rows["tts"]["is_active_engine"] is None


def test_facade_unload_unknown_raises():
    with pytest.raises(ValueError):
        _run(ml.unload("bogus"))


def test_facade_unload_tts_not_loaded(monkeypatch):
    monkeypatch.setattr(mm, "model", None)
    r = _run(ml.unload("tts"))
    assert r == {"unloaded": "tts", "success": False, "reason": "not loaded"}


def test_facade_unload_sidecars_none_running():
    r = _run(ml.unload("sidecars"))
    assert r["unloaded"] == "sidecars"
    assert r["success"] is False and r["count"] == 0


# ── MM2-05: unified idle config (env wins) ──────────────────────────────────

def test_idle_timeout_env_wins(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_IDLE_TIMEOUT_S", "123")
    assert mm._resolve_idle_timeout() == 123.0


def test_sidecar_idle_timeout_env_wins(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_SIDECAR_IDLE_TIMEOUT_S", "0")
    assert sb._resolve_sidecar_idle_timeout() == 0.0  # <=0 disables reaping


# ── MM2-06: bounded cooldowns ───────────────────────────────────────────────

def test_cooldown_sweep_evicts_stale():
    now = 1_000_000.0
    dl._install_cooldowns.clear()
    dl._install_cooldowns["old/repo"] = now - dl._COOLDOWN_TTL_SECS - 10
    dl._install_cooldowns["fresh/repo"] = now - 5
    dl._sweep_cooldowns(now)
    assert "old/repo" not in dl._install_cooldowns
    assert "fresh/repo" in dl._install_cooldowns
    dl._install_cooldowns.clear()


# ── MM2-07: per-role weight validation ──────────────────────────────────────

def test_small_onnx_is_not_flagged_as_truncated(tmp_path):
    # A complete-but-small ONNX model (> 64 KB, < 5 MB) must pass.
    (tmp_path / "model.onnx").write_bytes(b"\0" * (128 * 1024))
    dl._validate_snapshot_has_weights("x/onnx", str(tmp_path))  # must not raise


def test_truncated_snapshot_still_rejected(tmp_path):
    # Only tiny config/tokenizer files, no plausible weight → reject (#352).
    (tmp_path / "config.json").write_bytes(b"{}")
    (tmp_path / "tokenizer.json").write_bytes(b"x" * 2048)
    with pytest.raises(OSError):
        dl._validate_snapshot_has_weights("x/truncated", str(tmp_path))


def test_large_tensor_weight_passes(tmp_path):
    (tmp_path / "model.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))
    dl._validate_snapshot_has_weights("x/big", str(tmp_path))  # must not raise


# ── #622: install-state detector is weight-aware (truncated cache ≠ installed) ─

def _models():
    """Resolve at call time so a reloaded module is never tested stale."""
    return importlib.import_module("api.routers.setup.models")


def _catalogue():
    return importlib.import_module("services.model_catalogue")


def _make_snapshot(cache_root, repo_id, files):
    """Build a minimal HF-style snapshots/<rev>/ dir and return its cache root."""
    name = "models--" + repo_id.replace("/", "--")
    rev = cache_root / name / "snapshots" / "abc123"
    rev.mkdir(parents=True)
    for fname, data in files.items():
        (rev / fname).write_bytes(data)
    return rev


def test_snapshot_has_weights_distinguishes_truncated(tmp_path):
    full = tmp_path / "full"; full.mkdir()
    (full / "config.json").write_bytes(b"{}")
    (full / "model.safetensors").write_bytes(b"\0" * (6 * 1024 * 1024))
    assert _catalogue().snapshot_has_weights(str(full)) is True

    trunc = tmp_path / "trunc"; trunc.mkdir()
    (trunc / "config.json").write_bytes(b"{}")
    (trunc / "tokenizer.json").write_bytes(b"x" * 4096)
    assert _catalogue().snapshot_has_weights(str(trunc)) is False


def test_cache_is_complete_flags_truncated_weight_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    # Weight-bearing repo with config only (interrupted download) → incomplete.
    _make_snapshot(tmp_path, "k2-fsa/OmniVoice", {"config.json": b"{}"})
    assert _models().cache_is_complete({"repo_id": "k2-fsa/OmniVoice"}) is False
    # Same repo once the shard lands → complete.
    _make_snapshot(
        tmp_path / "ok", "k2-fsa/OmniVoice",
        {"config.json": b"{}", "model.safetensors": b"\0" * (6 * 1024 * 1024)},
    )
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path / "ok"))
    assert _models().cache_is_complete({"repo_id": "k2-fsa/OmniVoice"}) is True


def test_cosyvoice3_partial_download_is_not_installed(tmp_path, monkeypatch):
    """#3112: an interrupted CosyVoice 3 download kept some 5 MB+ weights, which the
    any-large-file check took for a complete install. Its declared files decide now."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    model = next(m for m in _models().KNOWN_MODELS if m["repo_id"] == "FunAudioLLM/Fun-CosyVoice3-0.5B-2512")
    big = b"\0" * (6 * 1024 * 1024)
    rev = tmp_path / "models--FunAudioLLM--Fun-CosyVoice3-0.5B-2512" / "snapshots" / "abc123"
    rev.mkdir(parents=True)
    for name in ("cosyvoice3.yaml", "campplus.onnx", "llm.pt"):  # flow.pt, hift.pt, ... never landed
        (rev / name).write_bytes(big)
    assert _models().cache_is_complete(model) is False

    for name in model["required_files"]:
        path = rev / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(big)
    assert _models().cache_is_complete(model) is True


def test_cache_is_complete_exempts_config_only_repo(tmp_path, monkeypatch):
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    # pyannote pipeline ships no weight of its own — a tiny cache is legit, not
    # truncated; the config_only hint must keep it from being flagged incomplete.
    _make_snapshot(tmp_path, "pyannote/speaker-diarization-3.1", {"config.yaml": b"x"})
    assert _models().cache_is_complete(
        {
            "repo_id": "pyannote/speaker-diarization-3.1",
            "config_only": True,
            "config_required_files": ["config.yaml"],
        }
    ) is True


def test_list_models_downgrades_truncated_cache(tmp_path, monkeypatch):
    """A size-positive but weight-less cache must report installed=False so the
    first-run wizard re-offers the download instead of stranding the user (#622)."""
    monkeypatch.setenv("HF_HUB_CACHE", str(tmp_path))
    _make_snapshot(tmp_path, "k2-fsa/OmniVoice", {"config.json": b"{}"})

    class _Repo:
        def __init__(self, rid, size):
            self.repo_id, self.size_on_disk = rid, size
            self.last_accessed, self.nb_files = 0, 1

    class _Info:
        repos = [_Repo("k2-fsa/OmniVoice", 4096)]  # size > 0 (config landed)

    import huggingface_hub
    monkeypatch.setattr(huggingface_hub, "scan_cache_dir", lambda: _Info())
    _models().invalidate_cache()
    out = _models().list_models()
    row = next(m for m in out["models"] if m["repo_id"] == "k2-fsa/OmniVoice")
    assert row["installed"] is False
    assert row["incomplete"] is True
    _models().invalidate_cache()


# ── The unload ordering (#1495) ─────────────────────────────────────────────
#
# Dropping the shared reference has to happen BEFORE the allocator caches are
# emptied. Inverted, the unload frees nothing and says it worked: the weights
# are still reachable so gc keeps them, empty_cache() only returns blocks the
# allocator already considered free, and the reference drops a moment later
# into a cache nobody will flush again. That is how a backend process held
# 3.6 GB across an idle sweep whose log line read "Released 1 idle engine(s)".
#
# Four call sites open-coded the pair; the one the engine-registry sweep reaches
# was the inverted one, which is why every UI-driven unload looked fine. These
# pin the ordering at the helper, at the sweep path, and at the facade — and the
# last test keeps new callers from open-coding it again.

class _Weights:
    """Stands in for the model. Identity is all these tests need."""


def _watch_free_vram(monkeypatch, manager=mm):
    """Record what the shared ref held at each free_vram() call.

    ``manager`` is explicit because other suites reimport
    services.model_manager, so more than one module object can be alive at
    once. Each caller here patches the exact object the code under test will
    reach — ``OmniVoiceBackend.unload`` imports at call time and gets whatever
    sys.modules holds now, the facade uses the alias it bound at its own import.
    Patching this file's alias for all of them passes alone and fails in a full
    run, which is how this test first went red.
    """
    seen: list = []
    monkeypatch.setattr(manager, "free_vram", lambda: seen.append(manager.model))
    monkeypatch.setattr(manager, "release_tts_side_caches", lambda: None)
    monkeypatch.setattr(manager, "model", _Weights())
    return seen


def test_unload_shared_model_clears_the_ref_before_freeing(monkeypatch):
    seen = _watch_free_vram(monkeypatch)
    assert mm.unload_shared_model() is True
    assert seen == [None], "free_vram() ran while the model was still referenced"
    assert mm.model is None


def test_unload_shared_model_is_idempotent(monkeypatch):
    seen = _watch_free_vram(monkeypatch)
    monkeypatch.setattr(mm, "model", None)
    assert mm.unload_shared_model() is False
    assert seen == [], "nothing was resident, so the allocator was left alone"


def test_engine_unload_releases_the_shared_model(monkeypatch):
    """The idle-sweep path — the one that was inverted."""
    manager = sys.modules["services.model_manager"]
    seen = _watch_free_vram(monkeypatch, manager)
    monkeypatch.setattr(tb, "clear_clone_prompt_cache", lambda: None)

    tb.OmniVoiceBackend().unload()

    assert seen == [None], "the engine sweep emptied the cache before releasing"
    assert manager.model is None


def test_facade_unload_tts_releases_the_shared_model(monkeypatch):
    seen = _watch_free_vram(monkeypatch, ml.mm)

    assert _run(ml.unload("tts")) == {"unloaded": "tts", "success": True}

    assert seen == [None]
    assert ml.mm.model is None


def test_no_caller_open_codes_the_shared_unload():
    """One ordering, in one place.

    The bug was not that someone wrote the two lines wrongly — it was that five
    modules each wrote them at all, so getting one wrong stayed invisible next
    to four that were right. Assigning ``model_manager.model`` from outside the
    module is the shape that made that possible; ``unload_shared_model()`` is
    the replacement.
    """
    import pathlib
    import re

    root = pathlib.Path(mm.__file__).resolve().parents[1]
    skip = {".venv", "venv", "site-packages", "node_modules", "__pycache__", "build", "dist"}
    pattern = re.compile(r"^\w+\.model\s*=\s*(?!=)")

    offenders = []
    for path in root.rglob("*.py"):
        if path.name == "model_manager.py" or skip & set(path.parts):
            continue
        for number, line in enumerate(path.read_text(encoding="utf-8", errors="ignore").splitlines(), 1):
            stripped = line.strip()
            if pattern.match(stripped) and ("mm." in stripped or "model_manager." in stripped):
                offenders.append(f"{path.relative_to(root)}:{number}: {stripped}")

    assert not offenders, (
        "assign model_manager.model only inside model_manager; callers use "
        "unload_shared_model(), which frees in the right order:\n  "
        + "\n  ".join(offenders)
    )


def test_free_vram_clears_cublas_workspaces_before_emptying(monkeypatch):
    """cuBLAS's 8.5 MB workspace pins the segment it sits in (#1495).

    Measured on a 4090: one live workspace block held an 803 MB segment that
    empty_cache() could never return. Clearing has to come first — afterwards
    the segment is already gone from empty_cache()'s point of view and the
    next cuBLAS call has re-taken a workspace.
    """
    calls: list = []

    class _Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def empty_cache():
            calls.append("empty_cache")

    class _C:
        @staticmethod
        def _cuda_clearCublasWorkspaces():
            calls.append("clear_cublas")

    fake = type("_Torch", (), {"cuda": _Cuda, "_C": _C, "backends": type("_B", (), {})})
    monkeypatch.setattr(mm, "_lazy_torch", lambda: fake)

    mm.free_vram()

    assert calls == ["clear_cublas", "empty_cache"]


def test_free_vram_survives_a_torch_without_the_private_api(monkeypatch):
    """The clear is a private binding, so its absence must not break an unload."""
    calls: list = []

    class _Cuda:
        @staticmethod
        def is_available():
            return True

        @staticmethod
        def empty_cache():
            calls.append("empty_cache")

    fake = type("_Torch", (), {"cuda": _Cuda, "_C": type("_C", (), {}), "backends": type("_B", (), {})})
    monkeypatch.setattr(mm, "_lazy_torch", lambda: fake)

    mm.free_vram()

    assert calls == ["empty_cache"]
