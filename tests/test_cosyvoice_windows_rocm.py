"""Managed CosyVoice HIP provisioning and admission without model acquisition."""

import contextlib
import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.fixture
def installer(monkeypatch, tmp_path):
    from services import sidecar_install

    monkeypatch.setattr(sidecar_install, "DATA_DIR", tmp_path)
    monkeypatch.delenv("OMNIVOICE_ENGINES_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_COSYVOICE_DIR", raising=False)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(sidecar_install.sys, "platform", "win32")
    monkeypatch.setattr(sidecar_install, "_host_family", lambda: "rocm")
    monkeypatch.setattr(sidecar_install, "_locate_uv", lambda: "test-uv")
    monkeypatch.setattr(sidecar_install, "_jobs", {})
    return sidecar_install


def test_explicit_managed_cosyvoice_rocm_selects_cp312(installer, monkeypatch):
    spec = installer.get_spec("cosyvoice")
    calls = []

    def run(job, argv, **kwargs):
        calls.append(argv)
        python = installer._venv_python(installer.managed_checkout(spec) / ".venv")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"synthetic interpreter")
        return 0

    monkeypatch.setattr(installer, "_run_logged", run)
    installer._step_create_venv(spec, installer._new_job(spec.engine_id))
    assert calls[0][-2:] == ["--python", "3.12"]


def test_explicit_managed_cosyvoice_rocm_installs_pinned_amd_not_cpu(installer, monkeypatch):
    spec = installer.get_spec("cosyvoice")
    calls = []
    monkeypatch.setattr(installer, "_run_logged", lambda job, argv, **kwargs: calls.append(argv) or 0)
    installer._step_install_deps(spec, installer._new_job(spec.engine_id))
    arguments = calls[0]
    assert any("torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl#sha256=" in arg for arg in arguments)
    assert any("rocm_sdk_devel-7.2.1" in arg for arg in arguments)
    assert not any("+cpu" in arg or "+cu128" in arg or "ctranslate2" in arg for arg in arguments)
    assert "--no-config" in arguments


def test_cpu_completion_stays_usable_without_attesting_cosyvoice_hip(installer, monkeypatch):
    spec = installer.get_spec("cosyvoice")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    (checkout / installer._INSTALL_COMPLETE_MARKER).write_text(
        f"{spec.probe_module}\n{spec.install_revision}\n", encoding="utf-8",
    )
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing CPU interpreter")
    monkeypatch.setenv(spec.env_var, str(checkout))
    assert installer._install_marker_valid(spec, checkout)
    assert installer.engine_venv_python(spec.env_var) == python
    assert not installer.cosyvoice_rocm_verified()


@pytest.mark.parametrize("variant", ["", "auto", "rocm", "cpu", "cuda"])
def test_healthy_cp310_cpu_sidecar_survives_main_runtime_choice(installer, monkeypatch, variant):
    from core.device_caps import HostCaps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    spec = installer.get_spec("cosyvoice")
    checkout = installer.managed_checkout(spec)
    for name in (spec.source_manifest, spec.source_required_path):
        source = checkout / name
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("existing source", encoding="utf-8")
    (checkout / installer._SOURCE_REVISION_MARKER).write_text(spec.source_revision + "\n", encoding="utf-8")
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"working CPU interpreter")
    (checkout / ".venv/pyvenv.cfg").write_text("version_info=3.10.15\n", encoding="utf-8")
    torch_version = checkout / ".venv/Lib/site-packages/torch/version.py"
    torch_version.parent.mkdir(parents=True)
    torch_version.write_text("__version__='2.7.0+cpu'\nhip=None\n", encoding="utf-8")
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    original = f"{spec.probe_module}\n{spec.install_revision}\n".encode()
    marker.write_bytes(original)
    weights = checkout / spec.weights_subdir
    weights.mkdir(parents=True)
    (weights / "cosyvoice3.yaml").write_text("synthetic config", encoding="utf-8")
    with (weights / "synthetic-weights.bin").open("wb") as stream:
        stream.truncate(5 * 1024 * 1024)
    (weights / installer._WEIGHTS_COMPLETE_MARKER).write_text(
        f"{spec.weights_repo_id}\n{spec.weights_revision}\n", encoding="utf-8",
    )
    monkeypatch.setenv(spec.env_var, str(checkout))
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setattr(installer, "_run_logged", lambda *args, **kwargs: pytest.fail("must not reinstall"))
    monkeypatch.setattr(installer.threading, "Thread", lambda *args, **kwargs: pytest.fail("must not start setup"))
    monkeypatch.setattr(installer, "_persist", lambda current: None)
    status = installer.get_status(spec.engine_id)
    assert status["installed"] is True and status["managed"] is True
    assert installer.start_install(spec.engine_id)["status"] == "already_installed"
    assert CosyVoiceSubprocessBackend.is_available() == (True, "ready")
    profile = CosyVoiceSubprocessBackend.runtime_compute_profile(
        HostCaps(family="rocm", available_families=("rocm", "cpu")),
    )
    assert profile["effective_device"] == "cpu" and profile["routing_status"] == "cpu_fallback"
    assert marker.read_bytes() == original
    assert python.read_bytes() == b"working CPU interpreter"
    assert "2.7.0+cpu" in torch_version.read_text(encoding="utf-8")


def installed_fixture(installer):
    from core.windows_rocm import cosyvoice

    spec = installer.get_spec("cosyvoice")
    checkout = installer.managed_checkout(spec)
    for directory, (revision, required) in cosyvoice.SOURCES.items():
        root = checkout / directory
        source = root / required
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_text("synthetic reviewed source\n", encoding="utf-8")
        (root / ".voicestudio_source_revision").write_text(revision + "\n", encoding="utf-8")
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"synthetic interpreter")
    (checkout / ".venv/pyvenv.cfg").write_text("version_info = 3.12.14\n", encoding="utf-8")
    site = checkout / ".venv/Lib/site-packages"
    for name, version in cosyvoice.expected_versions().items():
        distribution = site / f"{name.replace('-', '_')}-{version}.dist-info"
        distribution.mkdir(parents=True)
        (distribution / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    (site / "torch").mkdir()
    (site / "torch/version.py").write_text("__version__ = '2.9.1+rocm7.2.1'\nhip = '7.2.53211'\n", encoding="utf-8")
    result = {
        "ready": True, "scope": "cosyvoice-model-free", "python": str(python),
        "architecture": "win-amd64", "abi": "cp312", "packages": cosyvoice.expected_versions(),
        "recipe_sha256": cosyvoice.recipe_identity(), "source_sha256": cosyvoice.source_identity(checkout),
        "hip": "7.2.53211", "torch_gpu": True, "gpu_architecture": "gfx1201",
        "kernel_result": 4.0, "kernel_device": "cuda", "cosyvoice_imports": True,
        "media_backend": "soundfile",
    }
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker.write_text(cosyvoice.marker_text(result, python), encoding="utf-8")
    return checkout, result


@pytest.mark.parametrize("damage", [
    {"ready": 1}, {"scope": "voxcpm2-model-free"}, {"kernel_result": True},
    {"kernel_device": "cpu"}, {"cosyvoice_imports": False}, {"media_backend": "torchcodec"},
    {"gpu_architecture": "gfx1102"}, {"hip": "10.0"}, {"packages": {}},
    {"recipe_sha256": "old"}, {"source_sha256": "old"}, {"python": "foreign-python.exe"},
])
def test_qualifier_rejects_wrong_or_cpu_report_before_atomic_marker(installer, monkeypatch, damage):
    checkout, result = installed_fixture(installer)
    spec = installer.get_spec("cosyvoice")
    (checkout / installer._INSTALL_COMPLETE_MARKER).unlink()
    result.update(damage)

    def run(job, argv, **kwargs):
        job["_last_run_output"] = [json.dumps(result)]
        return 0

    monkeypatch.setattr(installer, "_run_logged", run)
    with pytest.raises(installer._StepError, match="invalid_report"):
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert not (checkout / installer._INSTALL_COMPLETE_MARKER).exists()


def test_valid_owned_verifier_publishes_current_report_and_preserves_parent_sdk(installer, monkeypatch):
    from core.windows_rocm import cosyvoice

    checkout, result = installed_fixture(installer)
    spec = installer.get_spec("cosyvoice")
    calls = []
    monkeypatch.setenv("HIP_PATH", "unrelated-sdk")
    monkeypatch.setenv("ROCM_PATH", "parent-private-sdk")
    monkeypatch.setenv("ROCM_HOME", "parent-home")

    def run(job, argv, **kwargs):
        calls.append((argv, kwargs))
        job["_last_run_output"] = ["real probe logging", json.dumps(result)]
        return 0

    monkeypatch.setattr(installer, "_run_logged", run)
    installer._step_verify(spec, installer._new_job(spec.engine_id))
    argv, options = calls[0]
    assert argv == [str(checkout / ".venv/Scripts/python.exe"), "-I", str(Path(cosyvoice.__file__).resolve()), str(checkout)]
    assert options["timeout"] == 600
    assert options["env"]["HF_HUB_OFFLINE"] == options["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert not {"HIP_PATH", "ROCM_PATH", "ROCM_HOME"}.intersection(options["env"])
    assert installer.os.environ["HIP_PATH"] == "unrelated-sdk"
    assert cosyvoice.read_verified_install(checkout) == result
    assert installer._install_marker_valid(spec, checkout)
    assert not list(checkout.glob(".voicestudio_install_complete.*"))


@pytest.mark.parametrize("output,exit_code", [("not JSON", 0), ("", 0), ("{}", 1)])
def test_failed_or_unreported_verification_never_publishes_marker(installer, monkeypatch, output, exit_code):
    checkout, result = installed_fixture(installer)
    (checkout / installer._INSTALL_COMPLETE_MARKER).unlink()

    def run(job, argv, **kwargs):
        job["_last_run_output"] = [output]
        return exit_code

    monkeypatch.setattr(installer, "_run_logged", run)
    spec = installer.get_spec("cosyvoice")
    with pytest.raises(installer._StepError):
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert not (checkout / installer._INSTALL_COMPLETE_MARKER).exists()


@pytest.mark.parametrize("damage", ["none", "cpu-torch", "package", "python", "source", "old-marker", "missing-sdk"])
def test_admission_binds_current_venv_sources_and_marker(installer, monkeypatch, damage):
    from core.device_caps import HostCaps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    checkout, result = installed_fixture(installer)
    if damage == "cpu-torch":
        (checkout / ".venv/Lib/site-packages/torch/version.py").write_text("__version__='2.9.1+rocm7.2.1'\nhip=None\n", encoding="utf-8")
    elif damage == "package":
        (checkout / ".venv/Lib/site-packages/hydra_core-1.3.8.dist-info/METADATA").write_text("Name: hydra-core\nVersion: 1.3.6\n", encoding="utf-8")
    elif damage == "python":
        (checkout / ".venv/pyvenv.cfg").write_text("version_info=3.10.15\n", encoding="utf-8")
    elif damage == "source":
        (checkout / "cosyvoice/cli/cosyvoice.py").write_text("modified source", encoding="utf-8")
    elif damage == "old-marker":
        (checkout / installer._INSTALL_COMPLETE_MARKER).write_text("cosyvoice\nwindows-rocm-cosyvoice-v0\n{}\n", encoding="utf-8")
    elif damage == "missing-sdk":
        (checkout / ".venv/Lib/site-packages/rocm_sdk_devel-7.2.1.dist-info/METADATA").unlink()
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(checkout))
    expected = damage == "none"
    assert installer.cosyvoice_rocm_verified() is expected
    assert installer._install_marker_valid(installer.get_spec("cosyvoice"), checkout) is expected
    caps = HostCaps(family="rocm", available_families=("rocm", "cpu"))
    profile = CosyVoiceSubprocessBackend.runtime_compute_profile(caps)
    assert profile["effective_device"] == ("rocm" if expected else "cpu")
    assert profile["routing_status"] == ("accelerated" if expected else "cpu_fallback")
    cpu = HostCaps(family="cpu", available_families=("rocm", "cpu"), requested_family="cpu")
    assert CosyVoiceSubprocessBackend.runtime_compute_profile(cpu)["effective_device"] == "cpu"


@pytest.mark.parametrize("platform,family,variant,device", [
    ("win32", "cuda", "cuda", "auto"), ("win32", "cpu", "cpu", "auto"),
    ("win32", "rocm", "rocm", "cpu"), ("linux", "rocm", "rocm", "auto"),
    ("linux", "cuda", "cuda", "auto"), ("darwin", "mps", "", "auto"),
])
def test_existing_platform_and_explicit_cpu_recipes_stay_intact(installer, monkeypatch, platform, family, variant, device):
    monkeypatch.setattr(installer.sys, "platform", platform)
    monkeypatch.setattr(installer, "_host_family", lambda: family)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setenv("OMNIVOICE_DEVICE", device)
    calls = []
    monkeypatch.setattr(installer, "_run_logged", lambda job, argv, **kwargs: calls.append(argv) or 0)
    spec = installer.get_spec("cosyvoice")
    installer._step_install_deps(spec, installer._new_job(spec.engine_id))
    assert all(argument in calls[0] for argument in installer._torch_pin_args(spec))
    assert not any("rocm_sdk" in argument for argument in calls[0])
    assert spec.venv_args == ("--python", "3.10")


@pytest.mark.parametrize("managed,cpu", [(True, False), (True, True), (False, False), (False, True)])
def test_only_verified_managed_child_cleans_sdk_and_honors_cpu(installer, monkeypatch, managed, cpu):
    from core import device_caps
    from core.windows_rocm.cosyvoice import CHILD_CHECKOUT
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    monkeypatch.setattr(installer, "cosyvoice_rocm_verified", lambda: managed)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "test-managed-checkout" if managed else "custom-source")
    monkeypatch.setenv("HIP_PATH", "global-sdk")
    monkeypatch.setenv("ROCM_HOME", "global-home")
    monkeypatch.setenv("ROCM_PATH", "parent-sdk")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "cpu" if cpu else "rocm")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "12.0.0")
    monkeypatch.setenv("HF_TOKEN", "fixture-secret")
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: SimpleNamespace(
        family="cpu" if cpu else "rocm", requested_family="cpu" if cpu else "auto",
    ))
    environment = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)._child_environment()
    assert ("HIP_PATH" not in environment) is managed
    assert (CHILD_CHECKOUT in environment) is managed
    assert environment["HSA_OVERRIDE_GFX_VERSION"] == "12.0.0"
    assert environment["HF_TOKEN"] == "fixture-secret"
    if managed and cpu:
        assert environment["HIP_VISIBLE_DEVICES"] == environment["CUDA_VISIBLE_DEVICES"] == "-1"
    assert installer.os.environ["HIP_PATH"] == "global-sdk"


def test_external_checkout_is_never_given_managed_hip_admission(installer, monkeypatch, tmp_path):
    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(tmp_path / "custom-source"))
    assert not installer.cosyvoice_rocm_verified()


def test_healthy_custom_sidecar_status_never_offers_an_automatic_managed_replacement(installer, monkeypatch, tmp_path):
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    spec = installer.get_spec("cosyvoice")
    checkout = tmp_path / "user-source"
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_bytes(b"user-owned interpreter")
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker.write_text("user-controlled completion\n", encoding="utf-8")
    monkeypatch.setenv(spec.env_var, str(checkout))
    original_find_spec = importlib.util.find_spec
    monkeypatch.setattr(importlib.util, "find_spec", lambda name, *args, **kwargs: (
        None if name == "cosyvoice" else original_find_spec(name, *args, **kwargs)
    ))
    monkeypatch.setattr(installer.threading, "Thread", lambda *args, **kwargs: pytest.fail("must not start setup"))
    assert CosyVoiceSubprocessBackend.is_available() == (True, "ready")
    status = installer.get_status(spec.engine_id)
    assert status["installed"] is True and status["managed"] is False
    assert installer.start_install(spec.engine_id)["status"] == "already_installed"
    assert installer.os.environ[spec.env_var] == str(checkout)
    assert python.read_bytes() == b"user-owned interpreter"
    assert marker.read_text(encoding="utf-8") == "user-controlled completion\n"
    assert not installer.managed_checkout(spec).exists()


@pytest.mark.parametrize("target", ["checkout", "venv"])
def test_linked_checkout_or_venv_is_refused_before_repair(installer, monkeypatch, tmp_path, target):
    spec = installer.get_spec("cosyvoice")
    checkout = installer.managed_checkout(spec)
    linked = checkout if target == "checkout" else checkout / ".venv"
    real_resolve = Path.resolve

    def resolve(path, *args, **kwargs):
        return tmp_path / "foreign" if path == linked else real_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr(installer, "_run_logged", lambda *args, **kwargs: pytest.fail("must not install"))
    with pytest.raises(installer._StepError, match="invalid_report"):
        installer._step_create_venv(spec, installer._new_job(spec.engine_id))
    assert not checkout.exists()


@pytest.mark.parametrize("format", ["WAV", "FLAC", "MP3"])
def test_media_adapter_decodes_real_local_audio_without_torchcodec(tmp_path, format):
    import numpy as np
    import soundfile
    from core.windows_rocm import cosyvoice

    path = tmp_path / f"reference.{format.lower()}"
    soundfile.write(path, np.sin(np.arange(4800) / 12) * 0.1, 24000, format=format)
    audio, rate = cosyvoice.load_audio(path, backend="soundfile")
    assert rate == 24000
    assert audio.shape == (1, 4800)
    assert bool(audio.abs().max() > 0)
    sliced, rate = cosyvoice.load_audio(path, frame_offset=120, num_frames=240, channels_first=False)
    assert sliced.shape == (240, 1)


def test_media_adapter_handles_filelike_and_rejects_remote_or_unknown_semantics():
    import numpy as np
    import soundfile
    from core.windows_rocm import cosyvoice

    stream = io.BytesIO()
    soundfile.write(stream, np.ones((480, 2)) * 0.1, 16000, format="WAV")
    stream.seek(0)
    audio, rate = cosyvoice.load_audio(stream)
    assert audio.shape == (2, 480) and rate == 16000
    for options in ({"backend": "ffmpeg"}, {"normalize": False}, {"frame_offset": -1}):
        with pytest.raises(ValueError):
            cosyvoice.load_audio(stream, **options)
    with pytest.raises(ValueError, match="local"):
        cosyvoice.load_audio("https://unapproved.example/audio.wav")


def test_upstream_namespaces_are_adapted_without_changing_shared_torchaudio(monkeypatch, tmp_path):
    from core.windows_rocm import cosyvoice

    original = SimpleNamespace(load=object(), transforms=object(), functional=object())
    modules = {name: SimpleNamespace(torchaudio=original) for name in (
        "cosyvoice.utils.file_utils", "cosyvoice.dataset.processor",
    )}
    modules["torch"] = object()
    monkeypatch.setattr(cosyvoice, "_runtime", lambda: SimpleNamespace(initialize_dll_search=lambda: None))
    monkeypatch.setattr(cosyvoice, "import_module", modules.__getitem__)
    monkeypatch.setattr(sys, "path", list(sys.path))
    load = original.load
    cosyvoice.prepare_imports(tmp_path)
    first = modules["cosyvoice.utils.file_utils"].torchaudio
    cosyvoice.prepare_imports(tmp_path)
    assert modules["cosyvoice.utils.file_utils"].torchaudio is first
    assert original.load is load
    for module in (modules["cosyvoice.utils.file_utils"], modules["cosyvoice.dataset.processor"]):
        assert module.torchaudio.load is cosyvoice.load_audio
        assert module.torchaudio.transforms is original.transforms


def test_saved_cpu_preference_reaches_private_child_before_torch_import(installer, monkeypatch):
    from core import device_caps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    monkeypatch.setattr(installer, "cosyvoice_rocm_verified", lambda: True)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "managed-checkout")
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: SimpleNamespace(family="cpu", requested_family="cpu"))
    environment = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)._child_environment()
    assert environment["OMNIVOICE_DEVICE"] == "cpu"
    assert environment["HIP_VISIBLE_DEVICES"] == environment["CUDA_VISIBLE_DEVICES"] == "-1"
    assert installer.os.environ["OMNIVOICE_DEVICE"] == "auto"


def spawn_boundary(monkeypatch):
    from services.subprocess_backend import SubprocessBackend

    calls = []

    def spawn(instance, *, environment=None):
        if instance._proc is not None and instance._proc.poll() is None:
            return
        environment = instance._child_environment() if environment is None else environment
        calls.append(("spawn", environment))
        instance._proc = SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(SubprocessBackend, "_spawn", spawn)
    return calls


@pytest.mark.parametrize("variant,device,requested", [
    ("cpu", "auto", "auto"), ("rocm", "cpu", "auto"), ("rocm", "auto", "cpu"),
])
def test_captured_cpu_intent_matches_spawn_cache_visibility_and_catalogue(
    installer, monkeypatch, variant, device, requested,
):
    from core import device_caps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    caps = device_caps.HostCaps(family="rocm", available_families=("rocm", "cpu"), requested_family=requested)
    monkeypatch.setattr(installer, "cosyvoice_rocm_verified", lambda: True)
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "managed-checkout")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setenv("OMNIVOICE_DEVICE", device)
    instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
    instance._proc = None
    calls = spawn_boundary(monkeypatch)
    instance._spawn()
    environment = calls[0][1]
    assert environment["HIP_VISIBLE_DEVICES"] == environment["CUDA_VISIBLE_DEVICES"] == "-1"
    assert instance._cosyvoice_spawn_device == "cpu"
    profile = CosyVoiceSubprocessBackend.runtime_compute_profile(caps)
    assert profile["effective_device"] == "cpu"
    assert profile["routing_status"] != "accelerated"


def test_variant_only_change_reaps_captured_cpu_worker_before_gpu_spawn(installer, monkeypatch):
    from core import device_caps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend

    caps = device_caps.HostCaps(family="rocm", available_families=("rocm", "cpu"), requested_family="auto")
    monkeypatch.setattr(installer, "cosyvoice_rocm_verified", lambda: True)
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "managed-checkout")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "cpu")
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.delenv("HIP_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
    instance._proc = None
    calls = spawn_boundary(monkeypatch)
    monkeypatch.setattr(instance, "_send", lambda message: calls.append(("send", message)))
    monkeypatch.setattr(instance, "_reap_unusable_process", lambda process, **kwargs: calls.append(("reap", process)))
    monkeypatch.setattr(instance, "_retry_timeout_cleanup", lambda: True)
    instance._spawn()
    old_worker = instance._proc
    calls.clear()
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    instance._spawn()
    assert calls[:2] == [("send", {"op": "shutdown"}), ("reap", old_worker)]
    assert calls[2][0] == "spawn" and instance._proc is not old_worker
    assert instance._cosyvoice_spawn_device == "cuda"
    environment = calls[2][1]
    assert environment["OMNIVOICE_TORCH_VARIANT"] == "rocm"
    assert "HIP_VISIBLE_DEVICES" not in environment and "CUDA_VISIBLE_DEVICES" not in environment
    assert CosyVoiceSubprocessBackend.runtime_compute_profile(caps)["effective_device"] == "rocm"


@pytest.mark.parametrize("variant,expected", [("cpu", "cpu"), ("rocm", "cuda")])
def test_real_spawn_uses_captured_environment_despite_parent_variant_change(
    installer, monkeypatch, variant, expected,
):
    from core import device_caps
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services import binary_preflight, endpoint_race, subprocess_backend

    caps = device_caps.HostCaps(family="rocm", available_families=("rocm", "cpu"), requested_family="auto")
    monkeypatch.setattr(installer, "cosyvoice_rocm_verified", lambda: True)
    monkeypatch.setattr(device_caps, "detect_host_caps", lambda: caps)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", "managed-checkout")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.delenv("HIP_VISIBLE_DEVICES", raising=False)
    monkeypatch.delenv("CUDA_VISIBLE_DEVICES", raising=False)
    instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
    instance._proc = None
    monkeypatch.setattr(instance, "_retry_timeout_cleanup", lambda: True)
    monkeypatch.setattr(instance, "venv_python", lambda: Path("synthetic-python"))
    monkeypatch.setattr(instance, "_recv_with_timeout", lambda timeout: {"op": "ready"})
    monkeypatch.setattr(instance, "_drain_stderr", lambda *args: None)
    monkeypatch.setattr(instance, "_touch", lambda: None)
    monkeypatch.setattr(subprocess_backend, "_ensure_reaper_running", lambda: None)
    monkeypatch.setattr(subprocess_backend, "_heartbeat_while_resolving", lambda engine: contextlib.nullcontext())
    monkeypatch.setattr(binary_preflight, "validate_executable", lambda *args, **kwargs: None)
    monkeypatch.setattr(endpoint_race, "download_endpoint", lambda **kwargs: None)
    captured = []

    def spawn(argv, **kwargs):
        captured.append(kwargs["env"])
        monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm" if variant == "cpu" else "cpu")
        return SimpleNamespace(poll=lambda: None)

    monkeypatch.setattr(subprocess_backend, "spawn_owned", spawn)
    instance._spawn()
    instance._stderr_thread.join()
    assert len(captured) == 1
    assert captured[0]["OMNIVOICE_TORCH_VARIANT"] == variant
    assert captured[0]["OMNIVOICE_DEVICE"] == instance._cosyvoice_spawn_device == expected
    if expected == "cpu":
        assert captured[0]["HIP_VISIBLE_DEVICES"] == captured[0]["CUDA_VISIBLE_DEVICES"] == "-1"
    else:
        assert "HIP_VISIBLE_DEVICES" not in captured[0] and "CUDA_VISIBLE_DEVICES" not in captured[0]


def test_device_change_retires_only_captured_owned_worker_before_respawn(monkeypatch):
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services.subprocess_backend import SubprocessBackend

    current = SimpleNamespace(poll=lambda: None)
    instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
    instance._proc = current
    instance._cosyvoice_spawn_device = "cuda"
    calls = []
    monkeypatch.setattr(instance, "_child_environment", lambda: {
        "VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT": "managed", "OMNIVOICE_DEVICE": "cpu",
    })
    monkeypatch.setattr(instance, "_send", lambda message: calls.append(message))
    monkeypatch.setattr(instance, "_reap_unusable_process", lambda process, **kwargs: calls.append(process))
    monkeypatch.setattr(instance, "_retry_timeout_cleanup", lambda: True)
    monkeypatch.setattr(SubprocessBackend, "_spawn", lambda self, **kwargs: calls.append("spawn"))
    instance._spawn()
    assert calls == [{"op": "shutdown"}, current, "spawn"]
    assert instance._proc is None


def test_device_change_cannot_discard_an_unclosed_owner(monkeypatch):
    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services.subprocess_backend import SubprocessBackend

    current = SimpleNamespace(poll=lambda: None)
    instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
    instance._proc = current
    instance._cosyvoice_spawn_device = "cuda"
    monkeypatch.setattr(instance, "_child_environment", lambda: {
        "VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT": "managed", "OMNIVOICE_DEVICE": "cpu",
    })
    monkeypatch.setattr(instance, "_send", lambda message: None)
    monkeypatch.setattr(instance, "_reap_unusable_process", lambda process, **kwargs: None)
    monkeypatch.setattr(instance, "_retry_timeout_cleanup", lambda: False)
    monkeypatch.setattr(SubprocessBackend, "_spawn", lambda self, **kwargs: pytest.fail("must not replace live owner"))
    with pytest.raises(RuntimeError, match="still stopping"):
        instance._spawn()
    assert instance._proc is current


def test_device_change_gives_a_healthy_real_process_its_normal_shutdown_budget(monkeypatch, tmp_path):
    import subprocess
    import threading
    import time

    from engines.cosyvoice_subprocess import CosyVoiceSubprocessBackend
    from services.subprocess_backend import SubprocessBackend

    ready = tmp_path / "shutdown-ready"
    script = (
        "import sys,time; from pathlib import Path; "
        "Path(sys.argv[1]).write_text('ready'); "
        "sys.stdin.buffer.read(1); time.sleep(0.7)"
    )
    current = subprocess.Popen(
        [sys.executable, "-I", "-B", "-c", script, str(ready)],
        stdin=subprocess.PIPE, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    try:
        deadline = time.monotonic() + 5
        while not ready.exists() and current.poll() is None and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "The inert child did not reach its shutdown wait"
        instance = CosyVoiceSubprocessBackend.__new__(CosyVoiceSubprocessBackend)
        instance._proc = current
        instance._cosyvoice_spawn_device = "cuda"
        instance._timeout_quarantine = []
        instance._timeout_quarantine_lock = threading.Lock()
        monkeypatch.setattr(instance, "_child_environment", lambda: {
            "VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT": "managed", "OMNIVOICE_DEVICE": "cpu",
        })

        def shutdown(message):
            assert message == {"op": "shutdown"}
            current.stdin.write(b"x")
            current.stdin.flush()

        monkeypatch.setattr(instance, "_send", shutdown)
        replacements = []
        monkeypatch.setattr(SubprocessBackend, "_spawn", lambda self, **kwargs: replacements.append(current.returncode))
        instance._spawn()
        assert current.returncode == 0, "A healthy delayed shutdown was force-stopped"
        assert replacements == [0], "Replacement must follow the original process's natural exit"
        assert not instance._timeout_quarantine
    finally:
        if current.poll() is None:
            current.kill()
        current.wait(timeout=5)
        current.stdin.close()


@pytest.mark.parametrize("bad", [None, "llm", "flow", "hift"])
def test_each_real_neural_component_must_match_the_declared_device(bad):
    from core.windows_rocm import cosyvoice

    components = {}
    for name in ("llm", "flow", "hift"):
        parameter = SimpleNamespace(device=SimpleNamespace(type="cpu" if name == bad else "cuda"))
        components[name] = SimpleNamespace(parameters=lambda item=parameter: iter([item]))
    model = SimpleNamespace(model=SimpleNamespace(**components))
    if bad is None:
        cosyvoice.validate_model_device(model, "cuda")
    else:
        with pytest.raises(RuntimeError, match="device_runtime"):
            cosyvoice.validate_model_device(model, "cuda")


def test_qualifier_source_pins_follow_the_actual_installer_spec(installer):
    from core.windows_rocm import cosyvoice

    spec = installer.get_spec("cosyvoice")
    expected = {"": (spec.source_revision, spec.source_required_path)}
    expected.update({item.path: (item.revision, item.required_path) for item in spec.extra_sources})
    assert cosyvoice.SOURCES == expected


def load_worker():
    path = Path(__file__).resolve().parents[1] / "backend/engines/cosyvoice_subprocess/main.py"
    specification = importlib.util.spec_from_file_location("_qualified_cosyvoice_worker_test", path)
    worker = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(worker)
    return worker


def test_actual_worker_qualifies_then_rejects_tampered_warm_model(installer, monkeypatch):
    import torch
    from core.windows_rocm import cosyvoice

    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(checkout))
    monkeypatch.setenv(cosyvoice.CHILD_CHECKOUT, str(checkout))
    monkeypatch.setenv("OMNIVOICE_DEVICE", "auto")
    monkeypatch.setattr(sys, "executable", str(checkout / ".venv/Scripts/python.exe"))
    worker = load_worker()
    support = worker._windows_rocm_support()
    imports = []
    monkeypatch.setattr(support, "prepare_imports", lambda path: imports.append(path))
    monkeypatch.setattr(worker, "_qwen_full_precision_load", contextlib.nullcontext)
    monkeypatch.setattr(worker, "_repair_qwen_cache", lambda model: None)
    monkeypatch.setattr(torch.cuda, "is_available", lambda: True)
    parameter = SimpleNamespace(device=SimpleNamespace(type="cuda"))
    components = {name: SimpleNamespace(parameters=lambda: iter([parameter])) for name in ("llm", "flow", "hift")}
    model = SimpleNamespace(model=SimpleNamespace(**components))
    monkeypatch.setitem(sys.modules, "cosyvoice.cli.cosyvoice", SimpleNamespace(AutoModel=lambda **kwargs: model))
    monkeypatch.setattr(sys, "path", list(sys.path))
    (checkout / "pretrained_models/Fun-CosyVoice3-0.5B").mkdir(parents=True)
    assert worker._load_model(io.BytesIO()) is model
    assert worker._load_model(io.BytesIO()) is model
    assert imports == [checkout]
    parameter.device.type = "cpu"
    with pytest.raises(RuntimeError, match="device_runtime"):
        worker._load_model(io.BytesIO())
    parameter.device.type = "cuda"
    (checkout / installer._INSTALL_COMPLETE_MARKER).unlink()
    with pytest.raises(RuntimeError, match="invalid_report"):
        worker._load_model(io.BytesIO())


def test_worker_requires_explicit_managed_handoff_and_matching_interpreter(installer, monkeypatch):
    from core.windows_rocm import cosyvoice

    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(checkout))
    monkeypatch.delenv(cosyvoice.CHILD_CHECKOUT, raising=False)
    worker = load_worker()
    assert worker._windows_rocm_support() is None
    monkeypatch.setenv(cosyvoice.CHILD_CHECKOUT, str(checkout))
    monkeypatch.setattr(sys, "executable", "foreign-python.exe")
    with pytest.raises(RuntimeError, match="invalid_report"):
        worker._windows_rocm_support()


def test_worker_accepts_equivalent_windows_path_presentation(installer, monkeypatch):
    from core.windows_rocm import cosyvoice

    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_COSYVOICE_DIR", str(checkout).replace("\\", "/"))
    monkeypatch.setenv(cosyvoice.CHILD_CHECKOUT, str(checkout))
    monkeypatch.setattr(sys, "executable", str(checkout / ".venv/Scripts/python.exe"))
    assert load_worker()._windows_rocm_support() is not None
