"""A warm generate must free idle GPU memory first when the box is tight
(#730/#1190).

The cold LOAD path already evicts via _make_room_before_tts_load (inside
_load_model_with_timeout). The warm path (model already resident, get_model
returns early at the cache check) skipped it, so a generate on a VRAM-tight
MPS box contended with capture-ASR and the clone-prompt side cache until it
exceeded the execution budget and was abandoned (#730/#1190).
make_room_before_generate, called from get_model()'s warm-return path, closes
that gap for every native TTS generate; the policy is the one real
reliability-vs-latency knob.
"""
import services.model_manager as mm
from services.model_manager import make_room_before_generate


def _count(monkeypatch):
    """Patch the eviction primitives and count how often each runs."""
    calls = {"free_vram": 0, "device_cache": 0, "side_caches": 0, "capture_asr": 0}

    monkeypatch.setattr(mm, "free_vram", lambda: calls.__setitem__("free_vram", calls["free_vram"] + 1))
    monkeypatch.setattr(
        mm, "release_device_cache",
        lambda **_kw: calls.__setitem__("device_cache", calls["device_cache"] + 1),
    )
    monkeypatch.setattr(
        mm, "release_tts_side_caches",
        lambda **kw: (
            calls.__setitem__("side_caches", calls["side_caches"] + 1),
            calls.__setitem__("kept_passages", kw.get("keep_passage_choices", False)),
        ),
    )
    import services.asr_backend as ab
    monkeypatch.setattr(
        ab, "release_idle_capture_backend",
        lambda _idle_s: calls.__setitem__("capture_asr", calls["capture_asr"] + 1),
    )
    return calls


def _ram(monkeypatch, gb):
    import services.memory_budget as mb
    monkeypatch.setattr(mb, "available_memory", lambda: {"ram_available_gb": gb})


def test_never_mode_frees_nothing(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", "never")
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls == {"free_vram": 0, "device_cache": 0, "side_caches": 0, "capture_asr": 0}


def test_always_mode_frees_all(monkeypatch):
    monkeypatch.setenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", "always")
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["device_cache"] == 1 and calls["side_caches"] == 1 and calls["capture_asr"] == 1


def test_auto_skips_on_roomy_machine(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    _ram(monkeypatch, 999.0)  # ample RAM -> a roomy machine pays nothing
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls == {"free_vram": 0, "device_cache": 0, "side_caches": 0, "capture_asr": 0}


def test_auto_frees_on_tight_ram(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    _ram(monkeypatch, 0.5)  # below the 6.0 GB unified headroom
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["device_cache"] == 1 and calls["side_caches"] == 1


def test_default_mode_is_auto(monkeypatch):
    # No env set at all must behave as auto (tight RAM triggers).
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    _ram(monkeypatch, 0.5)
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["device_cache"] == 1


def _mem(monkeypatch, ram_gb, vram_gb):
    import services.memory_budget as mb
    monkeypatch.setattr(
        mb, "available_memory",
        lambda: {"ram_available_gb": ram_gb, "vram_free_gb": vram_gb},
    )


def test_auto_frees_on_tight_vram_with_roomy_ram(monkeypatch):
    # A 4 GB CUDA card on a 16 GB laptop: RAM is fine, but VRAM is nearly full.
    # Without a free, the Windows driver spills the generate into shared system
    # memory and it runs several times slower.
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    _mem(monkeypatch, ram_gb=999.0, vram_gb=0.5)
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["device_cache"] == 1 and calls["side_caches"] == 1


def test_auto_skips_on_roomy_vram_and_ram(monkeypatch):
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    _mem(monkeypatch, ram_gb=999.0, vram_gb=20.0)
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls == {"free_vram": 0, "device_cache": 0, "side_caches": 0, "capture_asr": 0}


def test_auto_vram_threshold_follows_the_low_gpu_memory_warning(monkeypatch):
    # The same figure that logs "Low GPU memory" decides the free, so the app
    # never warns about VRAM without also acting on it.
    import services.memory_budget as mb
    monkeypatch.delenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", raising=False)
    monkeypatch.setattr(mb, "_LOW_RAM_HEADROOM_GB", 3.0)
    _mem(monkeypatch, ram_gb=999.0, vram_gb=2.5)
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["device_cache"] == 1


def test_generate_never_clears_cublas_workspaces(monkeypatch):
    # Warm generates can overlap inference on other GPU-pool workers, and
    # free_vram()'s cuBLAS workspace clear is only safe on unload paths.
    monkeypatch.setenv("OMNIVOICE_FREE_VRAM_BEFORE_GENERATE", "always")
    calls = _count(monkeypatch)
    make_room_before_generate()
    assert calls["free_vram"] == 0 and calls["device_cache"] == 1
    assert calls["kept_passages"] is True


def test_load_path_still_uses_the_full_flush(monkeypatch):
    _ram(monkeypatch, 0.5)
    calls = _count(monkeypatch)
    mm._make_room_before_tts_load()
    assert calls["free_vram"] == 1 and calls["device_cache"] == 0
    assert calls["kept_passages"] is False


def test_warm_cleanup_keeps_passage_choices_but_drops_prompts():
    # Remembered reference windows are an index and a transcript, not tensors;
    # dropping them before every generate would rerun reference ASR.
    import services.tts_backend as tb
    with tb._prompt_cache_lock:
        tb._prompt_cache["k"] = object()
        tb._passage_choices[("ref",)] = (0, "text")
        tb._passage_choices[("failed",)] = (tb._NO_PASSAGE, "")
    try:
        tb.clear_clone_prompt_cache(keep_passage_choices=True)
        assert "k" not in tb._prompt_cache
        assert tb._passage_choices[("ref",)] == (0, "text")
        # A failed ranking is retried rather than remembered past the cleanup.
        assert ("failed",) not in tb._passage_choices
        tb.clear_clone_prompt_cache()
        assert ("ref",) not in tb._passage_choices
    finally:
        tb.clear_clone_prompt_cache()
