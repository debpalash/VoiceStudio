"""Tests for the audio.cpp TTS backend (Breeze-TTS-2 over loopback HTTP).

Hermetic by design: every test exercises pure builders, env-driven
resolution, or registry wiring. No binary, no network, no model download —
``resolve_server_binary`` is only asserted on its failure message, and the
host env is scrubbed of ``OMNIVOICE_AUDIOCPP_*`` overrides per test.
"""
from __future__ import annotations

import base64
import errno
import importlib
import io
import json
import os
import stat
import string
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


def _resolve_app_modules():
    """Resolve application modules per test after isolation has been applied."""
    audiocpp = importlib.import_module("engines.audiocpp")
    return SimpleNamespace(
        audiocpp=audiocpp,
        bootstrap=importlib.import_module("engines.audiocpp.bootstrap"),
        tts_backend=importlib.import_module("services.tts_backend"),
    )


@pytest.fixture
def app_modules():
    return _resolve_app_modules()


@pytest.fixture(autouse=True)
def scrub_audiocpp_env(monkeypatch):
    for var in (
        "OMNIVOICE_AUDIOCPP_BIN",
        "OMNIVOICE_AUDIOCPP_DIR",
        "OMNIVOICE_AUDIOCPP_MODEL",
        "OMNIVOICE_AUDIOCPP_PACKAGE",
        "OMNIVOICE_AUDIOCPP_BACKEND",
        "OMNIVOICE_AUDIOCPP_DEVICE",
        "OMNIVOICE_AUDIOCPP_PORT",
        "OMNIVOICE_AUDIOCPP_ASSET",
    ):
        monkeypatch.delenv(var, raising=False)


# ── server config builder ──────────────────────────────────────────────────


def test_build_server_config_is_loopback_lazy_single_model(monkeypatch, app_modules):
    monkeypatch.setattr(app_modules.audiocpp, "_cpu_thread_count", lambda: 16)
    cfg = app_modules.audiocpp.build_server_config(
        model_id="breeze-tts-2",
        family="breeze_tts",
        model_path="/models/breeze-tts-2-q8_0.gguf",
        port=17860,
    )
    assert cfg["host"] == "127.0.0.1"
    assert cfg["port"] == 17860
    assert cfg["backend"] == "cpu"
    assert cfg["threads"] == 16
    assert cfg["lazy_load"] is True
    assert cfg["max_loaded_models"] == 1
    assert cfg["models"] == [
        {
            "id": "breeze-tts-2",
            "family": "breeze_tts",
            "path": "/models/breeze-tts-2-q8_0.gguf",
            "task": "tts",
            "mode": "offline",
        }
    ]


def test_build_server_config_wires_backend_local_gpu_index(app_modules):
    cfg = app_modules.audiocpp.build_server_config(
        model_id="breeze-tts-2",
        family="breeze_tts",
        model_path="/models/breeze.gguf",
        port=17860,
        backend="vulkan",
        device=1,
    )
    assert cfg["backend"] == "vulkan"
    assert cfg["device"] == 1
    assert cfg["threads"] == 1


# ── speech payload builder ─────────────────────────────────────────────────


def test_build_speech_payload_minimal_design(app_modules):
    payload = app_modules.audiocpp.build_speech_payload(
        model_id="breeze-tts-2", text="Hello.",
    )
    assert payload == {
        "model": "breeze-tts-2",
        "input": "Hello.",
        "response_format": "json",
    }


def test_build_speech_payload_clone_maps_verified_fields(app_modules):
    payload = app_modules.audiocpp.build_speech_payload(
        model_id="breeze-tts-2",
        text="Read this.",
        ref_audio="/voices/alice.wav",
        ref_text="Exact transcript.",
        instructions="Speak slowly.",
        guidance_scale=4.0,
        seed=42,
    )
    assert payload["instructions"] == "Speak slowly."
    assert payload["voice_ref"] == {"type": "path", "path": "/voices/alice.wav"}
    assert payload["reference_text"] == "Exact transcript."
    assert payload["guidance_scale"] == 4.0
    assert payload["seed"] == 42


def test_build_speech_payload_reference_text_needs_ref_audio(app_modules):
    payload = app_modules.audiocpp.build_speech_payload(
        model_id="breeze-tts-2", text="Hi.", ref_text="stray transcript",
    )
    assert "reference_text" not in payload
    assert "voice_ref" not in payload


# ── speech reply decoder ───────────────────────────────────────────────────


def _wav_json_bytes(mono, sr):
    import numpy as np
    import soundfile as sf

    buf = io.BytesIO()
    sf.write(buf, np.asarray(mono, dtype=np.float32), sr, format="WAV")
    return {"audio": base64.b64encode(buf.getvalue()).decode("ascii")}


def test_decode_speech_json_roundtrip_mono(app_modules):
    obj = _wav_json_bytes([0.0, 0.5, -0.5, 0.25], 24000)
    sr, wav = app_modules.audiocpp.decode_speech_json(obj)
    assert sr == 24000
    assert wav.ndim == 1 and len(wav) == 4
    assert abs(float(wav[1]) - 0.5) < 1e-4


def test_decode_speech_json_downmixes_stereo(app_modules):
    import numpy as np

    stereo = np.array([[0.5, -0.5], [0.5, -0.5]], dtype=np.float32)
    obj = _wav_json_bytes(stereo, 24000)
    sr, wav = app_modules.audiocpp.decode_speech_json(obj)
    assert sr == 24000
    assert wav.ndim == 1 and len(wav) == 2


def test_decode_speech_json_error_payload_raises(app_modules):
    with pytest.raises(ValueError, match="audio.cpp speech failed"):
        app_modules.audiocpp.decode_speech_json({"error": "model not loaded"})


# ── bootstrap resolution ───────────────────────────────────────────────────


def test_binary_name_exe_on_windows_only(app_modules):
    bootstrap = app_modules.bootstrap
    assert bootstrap.binary_name("windows-x64") == "audiocpp_server.exe"
    assert bootstrap.binary_name("linux-x64") == "audiocpp_server"
    assert bootstrap.binary_name("darwin-arm64") == "audiocpp_server"


def test_device_parser_and_auto_selection_prefer_discrete_gpu(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list("""
available_devices=3
Vulkan:0 "AMD Ryzen 9 7950X (RADV RAPHAEL_MENDOCINO)" [IGPU]
Vulkan:1 "NVIDIA GeForce RTX 4090" [GPU]
CPU:0 "AMD Ryzen 9 7950X" [CPU]
select with: --backend <vulkan|cpu> --device <index>
""")
    assert devices[1].hardware_family == "cuda"
    assert bootstrap.runtime_targets(devices) == ("vulkan", "cpu")
    selected = bootstrap.select_device(devices)
    assert (selected.device.backend, selected.device.index) == ("vulkan", 1)


def test_global_family_matches_gpu_inside_vulkan_registry(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list("""
Vulkan:0 "AMD Radeon 780M" [IGPU]
Vulkan:1 "NVIDIA GeForce RTX 4090" [GPU]
CPU:0 "Host CPU" [CPU]
""")
    selected = bootstrap.select_device(devices, requested_family="cuda")
    assert selected.device.index == 1
    assert selected.device.target == "vulkan"


def test_preferred_name_does_not_match_nameless_device(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list(
        'CUDA:0 [GPU]\nCUDA:1 "NVIDIA GeForce RTX 4090" [GPU]'
    )

    selected = bootstrap.select_device(
        devices,
        requested_family="cuda",
        preferred_name="NVIDIA GeForce RTX 4090",
    )

    assert selected.device.index == 1


def test_auto_selection_prefers_native_cpu_to_software_or_meta_vulkan(
    app_modules,
):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list("""
Vulkan:0 "llvmpipe" [CPU]
Vulkan:1 "registry metadata" [META]
CPU:0 "Host CPU" [CPU]
""")

    assert [device.kind for device in devices] == ["CPU", "META", "CPU"]
    assert bootstrap.runtime_targets(devices) == ("cpu", "vulkan")
    selected = bootstrap.select_device(devices)
    assert selected.device.backend == "cpu"
    software_vulkan = bootstrap.select_device(
        devices, backend_override="vulkan", device_override=0,
    )
    assert software_vulkan.device.target == "cpu"
    meta_vulkan = bootstrap.select_device(
        devices, backend_override="vulkan", device_override=1,
    )
    assert meta_vulkan.device.kind == "META"
    implicit_vulkan = bootstrap.select_device(
        devices, backend_override="vulkan",
    )
    assert implicit_vulkan.device.kind == "META"


def test_musa_registry_uses_cuda_routing(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list('MUSA:0 "Moore Threads GPU" [GPU]')

    assert devices[0].backend == "cuda"
    assert devices[0].hardware_family == "cuda"
    assert bootstrap.select_device(devices).device.target == "cuda"


def test_device_parser_accepts_an_empty_description(app_modules):
    devices = app_modules.bootstrap.parse_device_list("CPU:0 [CPU]")
    assert len(devices) == 1
    assert devices[0].name == ""
    assert devices[0].target == "cpu"


def test_software_vulkan_uses_cpu_thread_count(monkeypatch, app_modules):
    audiocpp = app_modules.audiocpp
    monkeypatch.setattr(audiocpp, "_cpu_thread_count", lambda: 7)
    cfg = audiocpp.build_server_config(
        model_id="model",
        family="family",
        model_path="model.gguf",
        port=17860,
        backend="vulkan",
        device=0,
        execution_target="cpu",
    )
    assert cfg["threads"] == 7


def test_explicit_backend_device_is_strict_and_backend_local(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list(
        'Vulkan:0 "GPU" [GPU]\nCPU:0 "CPU" [CPU]'
    )
    with pytest.raises(RuntimeError, match="requires"):
        bootstrap.select_device(devices, device_override=0)
    with pytest.raises(RuntimeError, match="unavailable"):
        bootstrap.select_device(
            devices, backend_override="cuda", device_override=0,
        )
    selected = bootstrap.select_device(
        devices, backend_override="vulkan", device_override=0,
    )
    assert selected.device.backend == "vulkan"


def test_missing_global_gpu_falls_back_to_cpu_with_reason(app_modules):
    bootstrap = app_modules.bootstrap
    devices = bootstrap.parse_device_list('CPU:0 "CPU" [CPU]')
    selected = bootstrap.select_device(devices, requested_family="cuda")
    assert selected.device.target == "cpu"
    assert "running on CPU" in selected.fallback_reason


def test_runtime_profile_reports_vulkan_independently_of_torch(
    monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    devices = app_modules.bootstrap.parse_device_list(
        'Vulkan:1 "NVIDIA GeForce RTX 4090" [GPU]\nCPU:0 "CPU" [CPU]'
    )
    monkeypatch.setattr(app_modules.bootstrap, "probe_devices", lambda: devices)
    caps = HostCaps(family="cpu", available_families=("cpu",), notes=())
    profile = app_modules.audiocpp.AudioCPPBackend.runtime_compute_profile(caps)
    assert profile == {
        "gpu_compat": ("vulkan", "cpu"),
        "min_vram_gb": 6.0,
        "effective_device": "vulkan",
        "routing_status": "accelerated",
        "routing_reason": None,
        "runtime_backend": "vulkan",
        "runtime_device_index": 1,
        "runtime_device_name": "NVIDIA GeForce RTX 4090",
        "runtime_hardware_family": "cuda",
        "runtime_vram_gb": 0.0,
        "runtime_device_verified": False,
    }


def test_engine_override_keeps_low_vram_caveat_when_global_device_is_cpu(
    monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    devices = app_modules.bootstrap.parse_device_list(
        'Vulkan:0 "NVIDIA GTX 1650" [GPU]\nCPU:0 "CPU" [CPU]'
    )
    monkeypatch.setattr(app_modules.bootstrap, "probe_devices", lambda: devices)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_BACKEND", "vulkan")
    caps = HostCaps(
        family="cpu",
        available_families=("cuda", "cpu"),
        device_name="NVIDIA GTX 1650",
        vram_gb=4.0,
        requested_family="cpu",
    )

    profile = app_modules.audiocpp.AudioCPPBackend.runtime_compute_profile(caps)

    assert profile["runtime_hardware_family"] == "cuda"
    assert profile["runtime_vram_gb"] == 4.0
    assert profile["runtime_device_verified"] is True
    assert "4.0 GB VRAM" in profile["routing_reason"]


def test_ambiguous_same_name_gpus_do_not_borrow_device_zero_vram(
    monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    devices = app_modules.bootstrap.parse_device_list(
        'CUDA:0 "NVIDIA RTX 4090" [GPU]\n'
        'CUDA:1 "NVIDIA RTX 4090" [GPU]'
    )
    monkeypatch.setattr(app_modules.bootstrap, "probe_devices", lambda: devices)
    caps = HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        device_name="NVIDIA RTX 4090",
        vram_gb=24.0,
    )

    selection = app_modules.bootstrap.resolve_compute_selection(caps)

    assert selection.verified_vram_gb == 0.0


@pytest.mark.parametrize(
    "device_line, family, device_name",
    [
        ('CUDA:0 "NVIDIA GTX 1650" [GPU]', "cuda", "NVIDIA GTX 1650"),
        ('HIP:0 "AMD Radeon RX 6500 XT" [GPU]', "rocm", "AMD Radeon RX 6500 XT"),
        ('Vulkan:0 "NVIDIA GTX 1650" [GPU]', "cuda", "NVIDIA GTX 1650"),
        ('Vulkan:0 "Intel Arc A380" [GPU]', "xpu", "Intel Arc A380"),
        ('Vulkan:0 "Discrete Graphics" [GPU]', "cpu", "Discrete Graphics"),
    ],
)
def test_runtime_profile_preserves_low_vram_caveat_for_native_accelerators(
    device_line, family, device_name, monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    devices = app_modules.bootstrap.parse_device_list(
        f'{device_line}\nCPU:0 "CPU" [CPU]'
    )
    monkeypatch.setattr(app_modules.bootstrap, "probe_devices", lambda: devices)
    caps = HostCaps(
        family=family,
        available_families=(family, "cpu"),
        device_name=device_name,
        vram_gb=4.0,
    )

    profile = app_modules.audiocpp.AudioCPPBackend.runtime_compute_profile(caps)

    assert profile["routing_status"] == "accelerated"
    assert profile["min_vram_gb"] == 6.0
    assert "4.0 GB VRAM" in profile["routing_reason"]


@pytest.mark.parametrize(
    "device_line, family",
    [
        ('Metal:0 "Apple Silicon" [GPU]', "mps"),
        ('Vulkan:0 "AMD Radeon 780M" [IGPU]', "rocm"),
    ],
)
def test_runtime_profile_does_not_apply_discrete_vram_floor_to_unified_memory(
    device_line, family, monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    devices = app_modules.bootstrap.parse_device_list(
        f'{device_line}\nCPU:0 "CPU" [CPU]'
    )
    monkeypatch.setattr(app_modules.bootstrap, "probe_devices", lambda: devices)
    caps = HostCaps(
        family=family,
        available_families=(family, "cpu"),
        device_name=devices[0].name,
        vram_gb=4.0,
    )

    profile = app_modules.audiocpp.AudioCPPBackend.runtime_compute_profile(caps)

    assert profile["routing_status"] == "accelerated"
    assert profile["min_vram_gb"] == 0.0
    assert profile["routing_reason"] is None


@pytest.mark.parametrize("output", [
    "", 'Vulkan:x "GPU" [GPU]', 'CPU:0 "CPU" [ALIEN]',
    'CPU:0 "CPU" [CPU]\nCPU:0 "CPU" [CPU]',
])
def test_device_parser_rejects_ambiguous_or_malformed_output(
    output, app_modules,
):
    with pytest.raises(RuntimeError):
        app_modules.bootstrap.parse_device_list(output)


def test_device_parser_rejects_aliased_backend_duplicate(app_modules):
    with pytest.raises(RuntimeError, match="duplicate"):
        app_modules.bootstrap.parse_device_list(
            'HIP:0 "AMD GPU" [GPU]\nROCm:0 "AMD GPU" [GPU]'
        )


def test_failed_device_probe_is_cached_until_invalidated(
    monkeypatch, app_modules,
):
    bootstrap = app_modules.bootstrap
    bootstrap.invalidate()
    run = Mock(return_value=SimpleNamespace(
        returncode=1, stdout="", stderr="private failure",
    ))
    monkeypatch.setattr(bootstrap.subprocess, "run", run)

    for _ in range(2):
        with pytest.raises(RuntimeError, match="device discovery failed"):
            bootstrap._probe_devices("/configured/audiocpp_server")

    assert run.call_count == 1
    bootstrap.invalidate()


def test_release_assets_have_complete_sha256_pins(app_modules):
    bootstrap = app_modules.bootstrap
    for filename, digest in bootstrap._ASSETS.values():
        assert filename
        assert len(digest) == 64
        assert set(digest) <= set(string.hexdigits)
    assert "windows-x64-vulkan" in bootstrap._ASSETS["windows-x64"][0]
    assert "ubuntu-x64-vulkan" in bootstrap._ASSETS["linux-x64"][0]


def test_model_catalog_and_backend_share_immutable_revision(app_modules):
    from services.hf_revisions import revision_for

    bootstrap = app_modules.bootstrap
    assert revision_for(bootstrap.HF_MODEL_REPO) == bootstrap.HF_MODEL_REVISION


def test_server_port_default_and_overrides(monkeypatch, app_modules):
    bootstrap = app_modules.bootstrap
    assert bootstrap.server_port() == bootstrap.DEFAULT_PORT
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_PORT", "18081")
    assert bootstrap.server_port() == 18081
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_PORT", "bogus")
    assert bootstrap.server_port() == bootstrap.DEFAULT_PORT


def test_package_filename_default_and_override(monkeypatch, app_modules):
    bootstrap = app_modules.bootstrap
    assert bootstrap.package_filename() == bootstrap.DEFAULT_PACKAGE
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_PACKAGE", "breeze-tts-2-bf16.gguf")
    assert bootstrap.package_filename() == "breeze-tts-2-bf16.gguf"


def test_materialize_hf_symlink_keeps_gguf_suffix_without_copy(
    tmp_path, app_modules, symlink_or_skip,
):
    bootstrap = app_modules.bootstrap
    blob = tmp_path / "content-addressed-blob"
    blob.write_bytes(b"GGUF test payload")
    snapshot = tmp_path / "breeze-tts-2-q8_0.gguf"
    symlink_or_skip(snapshot, blob)

    materialized = bootstrap._materialize_gguf_cache_path(snapshot)

    assert materialized.suffix == ".gguf"
    assert not materialized.is_symlink()
    assert os.path.samefile(materialized, blob)


def test_materialize_rejects_extensionless_model(tmp_path, app_modules):
    bootstrap = app_modules.bootstrap
    model = tmp_path / "model-blob"
    model.write_bytes(b"GGUF test payload")

    with pytest.raises(RuntimeError, match="must be a .gguf file"):
        bootstrap._materialize_gguf_cache_path(model)


def test_materialize_replaces_preexisting_symlink_alias(
    tmp_path, app_modules, symlink_or_skip,
):
    bootstrap = app_modules.bootstrap
    blob = tmp_path / "content-addressed-blob"
    blob.write_bytes(b"GGUF test payload")
    snapshot = tmp_path / "breeze-tts-2-q8_0.gguf"
    symlink_or_skip(snapshot, blob)
    alias = snapshot.with_name(
        f".{snapshot.stem}-{bootstrap.HF_MODEL_REVISION[:12]}.audiocpp.gguf"
    )
    symlink_or_skip(alias, blob)

    materialized = bootstrap._materialize_gguf_cache_path(snapshot)

    assert materialized == alias
    assert not materialized.is_symlink()
    assert os.path.samefile(materialized, blob)


def test_materialize_cross_filesystem_symlink_links_beside_target(
    tmp_path, monkeypatch, app_modules, symlink_or_skip,
):
    bootstrap = app_modules.bootstrap
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    blob = source_dir / "content-addressed-blob"
    blob.write_bytes(b"GGUF test payload")
    link_dir = tmp_path / "link"
    link_dir.mkdir()
    snapshot = link_dir / "custom.gguf"
    symlink_or_skip(snapshot, blob)
    real_link = os.link
    calls = 0

    def cross_filesystem_once(source, destination):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError(errno.EXDEV, "cross-device link")
        return real_link(source, destination)

    monkeypatch.setattr(bootstrap.os, "link", cross_filesystem_once)

    materialized = bootstrap._materialize_gguf_cache_path(snapshot)

    assert materialized.parent == blob.parent
    assert materialized.suffix == ".gguf"
    assert not materialized.is_symlink()
    assert os.path.samefile(materialized, blob)


def test_file_override_materializes_hf_style_symlink(
    tmp_path, monkeypatch, app_modules, symlink_or_skip,
):
    bootstrap = app_modules.bootstrap
    blob = tmp_path / "blob"
    blob.write_bytes(b"GGUF test payload")
    model = tmp_path / "custom.gguf"
    symlink_or_skip(model, blob)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_MODEL", str(model))

    resolved = bootstrap.resolve_model_file()

    assert resolved.suffix == ".gguf"
    assert not resolved.is_symlink()
    assert os.path.samefile(resolved, blob)


def test_directory_override_materializes_hf_style_symlink(
    tmp_path, monkeypatch, app_modules, symlink_or_skip,
):
    bootstrap = app_modules.bootstrap
    blob = tmp_path / "blob"
    blob.write_bytes(b"GGUF test payload")
    model_dir = tmp_path / "models"
    model_dir.mkdir()
    model = model_dir / bootstrap.DEFAULT_PACKAGE
    symlink_or_skip(model, blob)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_MODEL", str(model_dir))

    resolved = bootstrap.resolve_model_file()

    assert resolved.suffix == ".gguf"
    assert not resolved.is_symlink()
    assert os.path.samefile(resolved, blob)


def test_cached_model_resolution_is_strictly_offline(
    tmp_path, monkeypatch, app_modules,
):
    bootstrap = app_modules.bootstrap
    package_dir = tmp_path / bootstrap.PACKAGE_DIR
    package_dir.mkdir()
    model = package_dir / bootstrap.DEFAULT_PACKAGE
    model.write_bytes(b"GGUF test payload")
    calls = []

    def cached_snapshot(**kwargs):
        calls.append(kwargs)
        return str(tmp_path)

    monkeypatch.setattr("huggingface_hub.snapshot_download", cached_snapshot)

    assert bootstrap.resolve_model_file() == model
    assert calls == [{
        "repo_id": bootstrap.HF_MODEL_REPO,
        "revision": bootstrap.HF_MODEL_REVISION,
        "allow_patterns": [f"{bootstrap.PACKAGE_DIR}/{bootstrap.DEFAULT_PACKAGE}"],
        "local_files_only": True,
    }]


def test_missing_cached_model_requires_explicit_install(
    monkeypatch, app_modules,
):
    bootstrap = app_modules.bootstrap

    def cache_miss(**_kwargs):
        raise OSError("not cached")

    monkeypatch.setattr("huggingface_hub.snapshot_download", cache_miss)

    with pytest.raises(RuntimeError, match="the engine's Weights list in Model Catalogue"):
        bootstrap.resolve_model_file()


def test_resolve_server_binary_missing_gives_install_hint(
    tmp_path, monkeypatch, app_modules,
):
    bootstrap = app_modules.bootstrap
    # Point both env probes at nothing; the package bin/ is unpopulated in
    # a source checkout, so this must fail with guidance, not a bare error.
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_BIN", str(tmp_path / "nope"))
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_DIR", str(tmp_path / "nodir"))
    with pytest.raises(RuntimeError, match="docs/engines/audio-cpp.md") as exc:
        bootstrap.resolve_server_binary()
    assert bootstrap.default_asset()[1] in str(exc.value)


def test_resolve_server_binary_prefers_env_bin(tmp_path, monkeypatch, app_modules):
    bootstrap = app_modules.bootstrap
    fake = tmp_path / "audiocpp_server"
    fake.write_bytes(b"#!/bin/sh\n")
    if os.name != "nt":
        fake.chmod(0o755)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_BIN", str(fake))
    assert bootstrap.resolve_server_binary() == fake


def test_resolve_server_binary_rejects_non_executable_posix(
    tmp_path, monkeypatch, app_modules,
):
    if os.name == "nt":
        pytest.skip("POSIX executable bits do not apply on Windows")
    bootstrap = app_modules.bootstrap
    fake = tmp_path / "audiocpp_server"
    fake.write_bytes(b"binary")
    fake.chmod(0o644)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_BIN", str(fake))

    with pytest.raises(RuntimeError, match=r"chmod \+x audiocpp_server"):
        bootstrap.resolve_server_binary()

    assert bootstrap.is_installed() is False


def test_non_executable_explicit_binary_does_not_fall_through(
    tmp_path, monkeypatch, app_modules,
):
    if os.name == "nt":
        pytest.skip("POSIX executable bits do not apply on Windows")
    bootstrap = app_modules.bootstrap
    explicit = tmp_path / "explicit" / "audiocpp_server"
    explicit.parent.mkdir()
    explicit.write_bytes(b"binary")
    explicit.chmod(0o644)
    fallback_dir = tmp_path / "fallback"
    fallback_dir.mkdir()
    fallback = fallback_dir / "audiocpp_server"
    fallback.write_bytes(b"#!/bin/sh\n")
    fallback.chmod(0o755)
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_BIN", str(explicit))
    monkeypatch.setenv("OMNIVOICE_AUDIOCPP_DIR", str(fallback_dir))

    with pytest.raises(RuntimeError, match=r"chmod \+x audiocpp_server"):
        bootstrap.resolve_server_binary()
    assert bootstrap.is_installed() is False


# ── backend protocol + registry ────────────────────────────────────────────


def test_app_modules_are_resolved_at_test_time(monkeypatch):
    real_import = importlib.import_module
    imported = []

    def recording_import(name):
        imported.append(name)
        return real_import(name)

    monkeypatch.setattr(importlib, "import_module", recording_import)

    resolved = _resolve_app_modules()

    assert resolved.audiocpp is real_import("engines.audiocpp")
    assert imported == [
        "engines.audiocpp",
        "engines.audiocpp.bootstrap",
        "services.tts_backend",
    ]


def test_backend_metadata(app_modules):
    AudioCPPBackend = app_modules.audiocpp.AudioCPPBackend
    assert AudioCPPBackend.id == "audiocpp"
    assert AudioCPPBackend.supports_voice_design is True
    assert AudioCPPBackend.supports_cloning is True
    assert AudioCPPBackend.runs_out_of_process is True
    assert AudioCPPBackend._is_subprocess_isolated is True
    assert AudioCPPBackend.gpu_compat == ("cpu",)
    assert AudioCPPBackend.min_vram_gb == 0.0
    backend = AudioCPPBackend()
    assert backend.sample_rate == 24000
    assert backend.supported_languages == ["en", "zh"]
    assert "breeze_tts" in (backend.model_identity() or "")


def test_server_identity_rejects_unrelated_loopback_listener(
    monkeypatch, app_modules,
):
    backend = app_modules.audiocpp.AudioCPPBackend()
    backend._proc = Mock()
    backend._proc.poll.return_value = None
    backend._port = 17860
    backend._server_model_id = "breeze-tts-2-private-launch"
    monkeypatch.setattr(
        backend,
        "_get_json",
        lambda path: {"data": [{"id": "someone-elses-model"}]},
    )

    with pytest.raises(RuntimeError, match="did not prove"):
        backend._verify_server_identity()


def test_server_spawn_uses_contained_owner(tmp_path, monkeypatch, app_modules):
    backend = app_modules.audiocpp.AudioCPPBackend()
    binary = tmp_path / "audiocpp_server"
    binary.write_bytes(b"binary")
    model = tmp_path / "model.gguf"
    model.write_bytes(b"GGUF")
    monkeypatch.setattr(app_modules.bootstrap, "resolve_server_binary", lambda: binary)
    monkeypatch.setattr(app_modules.bootstrap, "resolve_model_file", lambda: model)
    monkeypatch.setattr(app_modules.bootstrap, "server_port", lambda: 17860)
    devices = app_modules.bootstrap.parse_device_list('CPU:0 "Test CPU" [CPU]')
    monkeypatch.setattr(
        app_modules.bootstrap, "resolve_compute_selection",
        lambda *args, **kwargs: app_modules.bootstrap.select_device(devices),
    )
    monkeypatch.setattr(
        importlib.import_module("core.config"), "DATA_DIR", tmp_path / "data",
    )
    proc = Mock()
    proc.poll.return_value = None
    spawn = Mock(return_value=proc)
    monkeypatch.setattr(app_modules.audiocpp, "spawn_owned", spawn)
    monkeypatch.setattr(backend, "_wait_for_health", lambda: None)

    backend._ensure_loaded()

    spawn.assert_called_once()
    config = json.loads(backend._server_json.read_text())
    assert config["backend"] == "cpu"
    assert config["models"][0]["id"].startswith("breeze-tts-2-")
    assert config["models"][0]["id"] != "breeze-tts-2"
    if os.name != "nt":
        assert stat.S_IMODE(backend._server_json.stat().st_mode) == 0o600
    backend._proc = None


def test_generate_timeout_terminates_owned_server(monkeypatch, app_modules):
    backend = app_modules.audiocpp.AudioCPPBackend()
    devices = app_modules.bootstrap.parse_device_list(
        'Vulkan:0 "AMD Radeon 780M" [IGPU]'
    )

    def mark_loaded():
        backend._port = 17860
        backend._server_model_id = "breeze-tts-2-private-launch"
        backend._selection = app_modules.bootstrap.select_device(devices)

    monkeypatch.setattr(backend, "_ensure_loaded", mark_loaded)
    monkeypatch.setattr(
        backend, "_post_json", Mock(side_effect=TimeoutError("wedged")),
    )
    terminate = Mock()
    monkeypatch.setattr(backend, "_terminate_server", terminate)
    model_manager = importlib.import_module("services.model_manager")
    timeout_budget = Mock(return_value=100.0)
    monkeypatch.setattr(model_manager, "generate_timeout_s", timeout_budget)
    monkeypatch.setattr(model_manager, "GENERATE_PROGRESS_GRACE_S", 40.0)
    progress = Mock()
    monkeypatch.setattr(model_manager, "report_generate_progress", progress)
    monotonic = iter((10.0, 30.0))
    monkeypatch.setattr(
        app_modules.audiocpp, "time", SimpleNamespace(monotonic=lambda: next(monotonic)),
    )

    with pytest.raises(RuntimeError, match="timed out"):
        backend.generate("Hello from the timeout test.")

    assert backend._post_json.call_args.kwargs["timeout"] == 65.0
    assert timeout_budget.call_args.kwargs == {
        "execution_device": "vulkan",
        "min_vram_gb": 0.0,
        "hardware_family": "rocm",
        "vram_gb": 0.0,
    }
    progress.assert_called_once_with()
    terminate.assert_called_once_with()


def test_generate_uses_progress_lease_after_first_download(
    monkeypatch, app_modules,
):
    backend = app_modules.audiocpp.AudioCPPBackend()

    def mark_loaded():
        enter = next(monotonic)
        assert enter == 20.0
        backend._port = 17860
        backend._server_model_id = "breeze-tts-2-private-launch"

    monotonic = iter((10.0, 20.0, 130.0))
    monkeypatch.setattr(backend, "_ensure_loaded", mark_loaded)
    monkeypatch.setattr(
        backend, "_post_json", Mock(side_effect=TimeoutError("wedged")),
    )
    monkeypatch.setattr(backend, "_terminate_server", Mock())
    model_manager = importlib.import_module("services.model_manager")
    monkeypatch.setattr(
        model_manager, "generate_timeout_s",
        lambda text, **kwargs: 100.0,
    )
    monkeypatch.setattr(model_manager, "GENERATE_PROGRESS_GRACE_S", 40.0)
    progress = Mock()
    monkeypatch.setattr(model_manager, "report_generate_progress", progress)
    monkeypatch.setattr(
        app_modules.audiocpp, "time", SimpleNamespace(monotonic=lambda: next(monotonic)),
    )

    with pytest.raises(RuntimeError, match="timed out"):
        backend.generate("Hello after a slow first download.")

    assert backend._post_json.call_args.kwargs["timeout"] == 25.0
    progress.assert_called_once_with()


def test_terminate_server_reaps_after_forced_kill(app_modules):
    backend = app_modules.audiocpp.AudioCPPBackend()
    proc = Mock()
    proc.wait.side_effect = [
        subprocess.TimeoutExpired("audiocpp_server", 5.0),
        0,
    ]
    backend._proc = proc

    backend._terminate_server()

    proc.terminate.assert_called_once_with()
    proc.kill.assert_called_once_with()
    assert proc.wait.call_count == 2
    assert backend._proc is None


def test_generate_rejects_empty_text_without_spawning(app_modules):
    AudioCPPBackend = app_modules.audiocpp.AudioCPPBackend
    backend = AudioCPPBackend()
    with pytest.raises(app_modules.audiocpp.TTSInputError):
        backend.generate("   ")


def test_unload_before_load_is_safe(app_modules):
    app_modules.audiocpp.AudioCPPBackend().unload()


def test_registry_resolves_audiocpp(app_modules):
    assert (
        app_modules.tts_backend.get_backend_class("audiocpp")
        is app_modules.audiocpp.AudioCPPBackend
    )


def test_is_available_false_without_binary_gives_reason(app_modules):
    ok, msg = app_modules.audiocpp.AudioCPPBackend.is_available()
    if ok:
        pytest.skip("audiocpp_server installed on this host")
    assert "audiocpp_server" in msg
    assert "audio-cpp.md" in msg


def test_is_available_requires_explicitly_installed_model(
    tmp_path, monkeypatch, app_modules,
):
    bootstrap = app_modules.bootstrap
    binary = tmp_path / "audiocpp_server"
    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: binary)

    def missing_model():
        raise RuntimeError(
            "Breeze-TTS-2 is not installed. Install it from the engine's Weights list in Model Catalogue."
        )

    monkeypatch.setattr(bootstrap, "resolve_model_file", missing_model)

    ok, msg = app_modules.audiocpp.AudioCPPBackend.is_available()

    assert ok is False
    assert "the engine's Weights list in Model Catalogue" in msg


def test_is_available_requires_binary_and_model(tmp_path, monkeypatch, app_modules):
    bootstrap = app_modules.bootstrap
    binary = tmp_path / "audiocpp_server"
    model = tmp_path / "breeze-tts-2-q8_0.gguf"
    monkeypatch.setattr(bootstrap, "resolve_server_binary", lambda: binary)
    monkeypatch.setattr(bootstrap, "resolve_model_file", lambda: model)
    selection = Mock(side_effect=AssertionError("availability probed devices"))
    monkeypatch.setattr(bootstrap, "resolve_compute_selection", selection)

    assert app_modules.audiocpp.AudioCPPBackend.is_available() == (True, "ready")
    selection.assert_not_called()


def test_auto_probe_failure_surfaces_cpu_fallback_through_async_profile(
    monkeypatch, app_modules,
):
    import asyncio

    from core.device_caps import HostCaps
    from services.engine_routing import runtime_compute_profile_async

    bootstrap = app_modules.bootstrap
    monkeypatch.setattr(
        bootstrap,
        "probe_devices",
        Mock(side_effect=RuntimeError("device discovery timed out")),
    )
    caps = HostCaps(family="cuda", available_families=("cuda", "cpu"))

    loop = asyncio.new_event_loop()
    try:
        profile = loop.run_until_complete(
            runtime_compute_profile_async(
                app_modules.audiocpp.AudioCPPBackend,
                caps,
            )
        )
    finally:
        loop.close()

    assert profile["gpu_compat"] == ("cpu",)
    assert profile["effective_device"] == "cpu"
    assert profile["runtime_backend"] == "cpu"
    assert profile["runtime_device_index"] == 0
    assert profile["routing_status"] == "cpu_fallback"
    assert profile["routing_reason"] == (
        "device discovery timed out; running on CPU"
    )


@pytest.mark.parametrize(
    ("env_name", "env_value"),
    [
        ("OMNIVOICE_AUDIOCPP_BACKEND", "cpu"),
        ("OMNIVOICE_AUDIOCPP_DEVICE", "0"),
    ],
)
def test_explicit_compute_override_keeps_probe_failure_fatal(
    env_name, env_value, monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    monkeypatch.setenv(env_name, env_value)
    monkeypatch.setattr(
        app_modules.bootstrap,
        "probe_devices",
        Mock(side_effect=RuntimeError("device discovery timed out")),
    )
    caps = HostCaps(family="cpu", available_families=("cpu",))

    with pytest.raises(RuntimeError, match="device discovery timed out"):
        app_modules.bootstrap.resolve_compute_selection(caps)

    profile = app_modules.audiocpp.AudioCPPBackend.runtime_compute_profile(caps)
    assert profile["routing_status"] == "unavailable"
    assert profile["routing_reason"] == "device discovery timed out"


def test_requested_compute_family_keeps_probe_failure_fatal(
    monkeypatch, app_modules,
):
    from core.device_caps import HostCaps

    monkeypatch.setattr(
        app_modules.bootstrap,
        "probe_devices",
        Mock(side_effect=RuntimeError("device discovery timed out")),
    )
    caps = HostCaps(
        family="cuda",
        available_families=("cuda", "cpu"),
        requested_family="cuda",
    )

    with pytest.raises(RuntimeError, match="device discovery timed out"):
        app_modules.bootstrap.resolve_compute_selection(caps)


def test_install_hint_present(app_modules):
    assert "audiocpp" in app_modules.tts_backend._INSTALL_HINTS


def test_no_hf_token_in_module_source(app_modules):
    # The bootstrap downloads from GitHub + ungated HF; no token may ever be
    # interpolated into an error string this module surfaces to the UI.
    import pathlib

    for name in ("__init__.py", "bootstrap.py"):
        src = pathlib.Path(app_modules.bootstrap.__file__).parent.joinpath(name).read_text()
        assert "HF_TOKEN" not in src
        assert "Authorization" not in src
