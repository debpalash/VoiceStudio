"""Bounded catalogue checks, core reference I/O, and sidecar metadata truth."""
import importlib.util
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
import soundfile


def test_main_clone_reference_uses_soundfile_before_omnivoice_import(monkeypatch, tmp_path):
    from importlib import metadata

    from services import model_manager as mm
    version = metadata.version
    monkeypatch.setattr(metadata, "version", lambda name: "2.9.1" if name == "torchaudio" else version(name))
    audio = SimpleNamespace(load=lambda *a, **kw: pytest.fail("TorchCodec must not decode this reference"))
    monkeypatch.setitem(sys.modules, "torchaudio", audio)
    monkeypatch.setitem(sys.modules, "omnivoice.models.omnivoice", SimpleNamespace(OmniVoice=object))
    monkeypatch.setattr(mm, "_OmniVoice", None)
    assert mm._lazy_omnivoice() is object
    path = Path(__file__).parents[1] / "omnivoice/utils/audio.py"
    spec = importlib.util.spec_from_file_location("clone_reference_audio_compat_test", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    wav = tmp_path / "reference.wav"
    soundfile.write(wav, np.full((160, 2), 0.25), 16000, subtype="FLOAT")
    waveform = module.load_audio(str(wav), 16000)
    assert waveform.shape == (1, 160) and waveform.mean().item() == 0.25


@pytest.mark.parametrize("known_hip,expected", [(None, True), (False, True), (True, False)])
def test_opted_windows_indextts_does_not_infer_gpu_from_main_runtime(monkeypatch, tmp_path, known_hip, expected):
    from engines.indextts import IndexTTS2Backend, bootstrap

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(bootstrap, "_resolved_python", tmp_path / ".venv/Scripts/python.exe")
    monkeypatch.setattr("services.sidecar_install.venv_torch_hip", lambda path: known_hip)
    assert IndexTTS2Backend._sidecar_torch_is_cuda_only() is expected


@pytest.mark.parametrize("platform,remapped", [("win32", False), ("linux", True)])
def test_hsa_remap_is_linux_only(monkeypatch, platform, remapped):
    from core.device_caps import arch_unsupported
    from services import model_manager as mm

    monkeypatch.setattr(sys, "platform", platform)
    monkeypatch.delenv("HSA_OVERRIDE_GFX_VERSION", raising=False)
    torch = SimpleNamespace(
        version=SimpleNamespace(hip="7.2.1"),
        cuda=SimpleNamespace(is_available=lambda: True,
                             get_device_name=lambda index: "AMD Radeon",
                             get_device_properties=lambda index: SimpleNamespace(gcnArchName="gfx1102"),
                             get_arch_list=lambda: ["gfx1100"]),
    )
    assert arch_unsupported(torch) == (None if remapped else ("gfx1102", ("gfx1100",)))
    mm._configure_rocm_if_needed(torch)
    import os
    assert ("HSA_OVERRIDE_GFX_VERSION" in os.environ) is remapped


def test_shared_argos_ct2_import_boundary_initializes_windows_dlls(monkeypatch):
    from core import execstack
    from core.windows_rocm import runtime

    monkeypatch.setattr(sys, "platform", "win32")
    prepared = []
    monkeypatch.setattr(runtime, "initialize_dll_search", lambda: prepared.append(True))
    assert execstack.ensure_ctranslate2_loadable()[0]
    assert prepared == [True]


def test_explicit_manual_windows_hsa_override_keeps_existing_behavior(monkeypatch):
    from core.device_caps import arch_unsupported

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setenv("HSA_OVERRIDE_GFX_VERSION", "11.0.0")
    torch = SimpleNamespace(version=SimpleNamespace(hip="7.2.1"), cuda=SimpleNamespace(
        is_available=lambda: True, get_arch_list=lambda: ["gfx1100"],
    ))
    assert arch_unsupported(torch) is None


def test_package_root_ignores_a_stray_folder_in_the_working_directory(tmp_path, monkeypatch):
    """A ``ctranslate2/`` folder in the cwd must not decide which DLL dirs load."""
    from core.windows_rocm import runtime

    (tmp_path / "ctranslate2").mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.syspath_prepend(str(tmp_path))
    root = runtime.package_root("ctranslate2")
    assert root is None or tmp_path not in root.parents
