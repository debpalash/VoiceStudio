"""Source installs must honour `OMNIVOICE_TORCH_VARIANT=rocm` (#1665).

`uv sync` always restores the lockfile's CUDA torch build, which is CPU-only
on AMD cards, and `uv run` re-syncs before every launch. These tests pin the
source flow: `scripts/setup.py` reinstalls the ROCm wheel after the sync, and
`scripts/dev-backend.mjs` launches with `uv run --no-sync` so it sticks.
"""
import builtins
from contextlib import contextmanager, nullcontext, redirect_stdout
from io import BytesIO, StringIO
import hashlib
import importlib.metadata
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import sysconfig
from types import ModuleType, SimpleNamespace
from unittest.mock import MagicMock, Mock
import zipfile

import pytest

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETUP = os.path.join(_ROOT, "scripts", "setup.py")
_DEV_BACKEND = os.path.join(_ROOT, "scripts", "dev-backend.mjs")


def _load_setup():
    spec = importlib.util.spec_from_file_location("vs_setup", _SETUP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rocm_opt_in_requires_explicit_variant():
    setup = _load_setup()
    assert setup._rocm_opt_in({}) is None
    assert setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "auto"}) is None
    assert setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "ROCm"}, platform="linux") == setup.ROCM_TORCH_INDEX
    assert (
        setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "rocm", "OMNIVOICE_TORCH_INDEX": "https://x/"}, platform="linux")
        == "https://x/"
    )


@pytest.mark.parametrize("variant", ["rocm", " ROCm ", "ROCM"])
def test_windows_rocm_opt_in_uses_the_amd_wheel_page(variant):
    setup = _load_setup()
    environment = {"OMNIVOICE_TORCH_VARIANT": variant, "OMNIVOICE_TORCH_INDEX": "https://linux.invalid/"}
    assert setup._rocm_opt_in(environment, platform="win32") == (
        setup.WINDOWS_ROCM_FIND_LINKS
    )
    environment["OMNIVOICE_WINDOWS_ROCM_FIND_LINKS"] = "https://windows.invalid/"
    assert setup._rocm_opt_in(environment, platform="win32") == "https://windows.invalid/"
    assert setup._rocm_opt_in({}, platform="win32") is None


@pytest.mark.parametrize("platform", ["win32", "linux", "darwin"])
@pytest.mark.parametrize("variant", [None, "auto", "cpu", "cuda"])
def test_default_setup_never_enters_windows_rocm(monkeypatch, platform, variant):
    setup = _load_setup()
    monkeypatch.setattr(setup.sys, "platform", platform)
    if variant is None:
        monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT", raising=False)
    else:
        monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", lambda: None)
    compat_checks = []
    monkeypatch.setattr(setup, "_find_compat_dir", lambda: compat_checks.append(True))
    monkeypatch.setattr(setup, "_installed_torch_is_rocm", lambda: pytest.fail("unexpected ROCm probe"))
    monkeypatch.setattr(setup, "_ensure_windows_rocm_ctranslate2", lambda: pytest.fail("unexpected CT2 install"))
    setup.main()
    assert compat_checks == ([] if platform == "darwin" else [True])


@pytest.mark.parametrize("python_platform", ["win32", "win-arm64"])
def test_windows_rocm_rejects_unsupported_interpreter_before_package_changes(monkeypatch, python_platform):
    setup = _load_setup()
    monkeypatch.setattr(sysconfig, "get_platform", lambda: python_platform)
    original_check = setup._rocm_python_supported
    monkeypatch.setattr(setup, "_rocm_python_supported", lambda: original_check("win32", (3, 12)))
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(setup, "_installed_torch_is_rocm", lambda: pytest.fail("unsupported interpreter probed"))
    monkeypatch.setattr(setup.subprocess, "check_call", lambda *args, **kwargs: pytest.fail("package install reached"))
    with pytest.raises(RuntimeError, match="x64 Python 3.12"):
        setup._ensure_rocm_torch()


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_torch_install_failure_is_fatal_only_for_windows_opt_in(monkeypatch, capsys, platform):
    setup = _load_setup()
    monkeypatch.setattr(setup.sys, "platform", platform)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(setup, "_rocm_python_supported", lambda: True)
    monkeypatch.setattr(setup, "_installed_torch_is_rocm", lambda: False)
    monkeypatch.setattr(setup, "_check_windows_rocm", lambda: pytest.fail("failed installation validated"))

    def fail_install(command):
        raise subprocess.CalledProcessError(1, command)

    monkeypatch.setattr(setup.subprocess, "check_call", fail_install)
    if platform == "win32":
        with pytest.raises(RuntimeError, match="Windows ROCm PyTorch installation failed"):
            setup._ensure_rocm_torch()
        assert "keeping the default" not in capsys.readouterr().out
    else:
        setup._ensure_rocm_torch()
        assert "keeping the default" in capsys.readouterr().out


def test_windows_rocm_ctranslate2_release_identity():
    setup = _load_setup()
    assert setup.WINDOWS_ROCM_CT2_URL == (
        "https://github.com/OpenNMT/CTranslate2/releases/download/v4.8.2/rocm-python-wheels-Windows.zip"
    )
    assert setup.WINDOWS_ROCM_CT2_ARCHIVE_SHA256 == "43DA4BAA5FEAEE49F77E176277A9647F99C493173C81A0BC60F491CAC97532C2"
    assert setup.WINDOWS_ROCM_CT2_WHEEL_MEMBER == "temp-windows/ctranslate2-4.8.2-cp312-cp312-win_amd64.whl"
    assert setup.WINDOWS_ROCM_CT2_WHEEL_SHA256 == "D5B9B0D34B23584638FC087BF99A85B0BA9917B84860D5FBDDCC73B0AEDD4FD4"


def test_windows_rocm_reinstall_uses_312_and_bypasses_project_constraints(monkeypatch):
    setup = _load_setup()
    monkeypatch.setattr(sysconfig, "get_platform", lambda: "win-amd64")
    assert setup.rocm_torch_reinstall_cmd(
        setup.WINDOWS_ROCM_FIND_LINKS,
        python=r"C:\probe\Scripts\python.exe",
        platform="win32",
    ) == [
        "uv", "--no-config", "pip", "install",
        "--python", r"C:\probe\Scripts\python.exe",
        *setup.WINDOWS_ROCM_TORCH_PINS,
        "--reinstall-package", "torch",
        "--reinstall-package", "torchaudio",
        "--reinstall-package", "torchvision",
        "--find-links", setup.WINDOWS_ROCM_FIND_LINKS,
    ]
    assert setup.WINDOWS_ROCM_TORCH_PINS == (
        "torch==2.9.1+rocm7.2.1",
        "torchaudio==2.9.1+rocm7.2.1",
        "torchvision==0.24.1+rocm7.2.1",
        "rocm[libraries]==7.2.1",
    )
    assert setup._rocm_python_supported("win32", (3, 12))
    assert not setup._rocm_python_supported("win32", (3, 11))
    assert setup._rocm_python_supported("linux", (3, 11))


@pytest.mark.parametrize("platform,version,python_platform,supported", [
    ("win32", (3, 12), "win-amd64", True),
    ("win32", (3, 12), "win32", False),
    ("win32", (3, 12), "win-arm64", False),
    ("win32", (3, 12), "linux-x86_64", False),
    ("win32", (3, 11), "win-amd64", False),
    ("win32", (3, 13), "win-amd64", False),
    ("linux", (3, 11), "linux-aarch64", True),
    ("darwin", (3, 11), "macosx-11.0-arm64", True),
])
def test_rocm_python_requires_windows_312_x64_only(monkeypatch, platform, version, python_platform, supported):
    setup = _load_setup()
    monkeypatch.setattr(sysconfig, "get_platform", lambda: python_platform)
    assert setup._rocm_python_supported(platform, version) is supported


@pytest.mark.parametrize("changed_package", [None, "torch", "torchaudio", "torchvision", "rocm", "missing", "cuda"])
def test_windows_rocm_reuse_requires_complete_pinned_stack(monkeypatch, changed_package):
    setup = _load_setup()
    versions = {
        "torch": "2.9.1+rocm7.2.1",
        "torchaudio": "2.9.1+rocm7.2.1",
        "torchvision": "0.24.1+rocm7.2.1",
        "rocm": "7.2.1",
    }
    if changed_package in versions:
        versions[changed_package] = "0.0.0"
    if changed_package == "missing":
        del versions["torchaudio"]

    def version(package):
        if package not in versions:
            raise importlib.metadata.PackageNotFoundError(package)
        return versions[package]

    def run_probe(command, **kwargs):
        output = StringIO()
        try:
            with redirect_stdout(output):
                exec(command[2], {})
        except Exception as exc:
            return SimpleNamespace(returncode=1, stdout=output.getvalue(), stderr=str(exc))
        return SimpleNamespace(returncode=0, stdout=output.getvalue(), stderr="")

    monkeypatch.setattr(importlib.metadata, "version", version)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(
        version=SimpleNamespace(hip=None if changed_package == "cuda" else "7.2"),
    ))
    monkeypatch.setattr(setup.subprocess, "run", run_probe)
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup, "_rocm_opt_in", lambda: setup.WINDOWS_ROCM_FIND_LINKS)
    monkeypatch.setattr(setup, "_rocm_python_supported", lambda: True)
    original_probe = setup._installed_torch_is_rocm
    monkeypatch.setattr(setup, "_installed_torch_is_rocm", lambda: original_probe(platform="win32"))
    assert setup._installed_torch_is_rocm() is (changed_package is None)
    installs = []
    validations = []
    monkeypatch.setattr(setup.subprocess, "check_call", lambda command: installs.append(command))
    monkeypatch.setattr(setup, "_check_windows_rocm", lambda: validations.append(True))
    setup._ensure_rocm_torch()
    assert len(installs) == (0 if changed_package is None else 1)
    assert validations == [True]


def test_windows_detects_existing_rocm_in_an_isolated_process(monkeypatch):
    setup = _load_setup()
    commands = []

    def probe(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="True\n")

    monkeypatch.setattr(setup.subprocess, "run", probe)
    assert setup._installed_torch_is_rocm(platform="win32")
    assert commands[0][0][0] == setup.sys.executable
    assert "torch" in commands[0][0][2]


def test_windows_verifies_rocm_in_a_new_interpreter(monkeypatch):
    setup = _load_setup()
    commands = []
    monkeypatch.setattr(setup.subprocess, "check_call", lambda *args, **kwargs: commands.append((args, kwargs)))
    setup._check_windows_rocm()
    assert commands[0][0][0][0] == setup.sys.executable
    assert "torch.cuda.is_available()" in commands[0][0][0][2]


@pytest.mark.parametrize("optimization", [0, 1, 2], ids=["optimize_zero", "optimize_one", "optimize_two"])
@pytest.mark.parametrize("hip,available,value,error", [
    (None, True, 4, "HIP runtime"),
    ("7.2", False, 4, "GPU"),
    ("7.2", True, 0, "matrix result"),
    ("7.2", True, float("nan"), "matrix result"),
    ("7.2", True, 4, None),
], ids=["missing_hip", "missing_gpu", "wrong_result", "nonfinite_result", "valid"])
def test_windows_rocm_probe_remains_effective_under_optimization(
    monkeypatch, optimization, hip, available, value, error,
):
    setup = _load_setup()
    commands = []
    monkeypatch.setattr(setup.subprocess, "check_call", lambda command, **kwargs: commands.append(command))
    setup._check_windows_rocm()
    operations = []
    tensor = MagicMock()
    result = MagicMock()
    tensor.__matmul__.side_effect = lambda other: operations.append("matmul") or result
    result.__getitem__.return_value.item.side_effect = lambda: operations.append("item") or value
    fake_torch = SimpleNamespace(
        version=SimpleNamespace(hip=hip),
        cuda=SimpleNamespace(
            is_available=Mock(return_value=available),
            synchronize=Mock(side_effect=lambda: operations.append("synchronize")),
        ),
        ones=Mock(return_value=tensor),
    )
    monkeypatch.setitem(sys.modules, "torch", fake_torch)
    probe = compile(commands[0][2], "<windows-rocm-probe>", "exec", optimize=optimization)
    if error is None:
        exec(probe, {})
    else:
        with pytest.raises(RuntimeError, match=error):
            exec(probe, {})
    if hip and available:
        fake_torch.ones.assert_called_once_with((4, 4), device="cuda")
        tensor.__matmul__.assert_called_once_with(tensor)
        fake_torch.cuda.synchronize.assert_called_once_with()
        result.__getitem__.assert_called_once_with((0, 0))
        assert operations == ["matmul", "synchronize", "item"]
    else:
        fake_torch.ones.assert_not_called()
        assert operations == []


def test_windows_rocm_setup_does_not_install_nvidia_cudnn(monkeypatch):
    setup = _load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", lambda: None)
    monkeypatch.setattr(setup, "_ensure_rocm_torch", lambda: None)
    installed = []
    monkeypatch.setattr(setup, "_ensure_windows_rocm_ctranslate2", lambda: installed.append(True))
    monkeypatch.setattr(setup, "_rocm_opt_in", lambda: setup.WINDOWS_ROCM_FIND_LINKS)
    monkeypatch.setattr(setup, "_find_compat_dir", lambda: (_ for _ in ()).throw(AssertionError("cuDNN installation reached")))
    setup.main()
    assert installed == [True]


def _rocm_archive(members):
    buffer = BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, contents in members:
            archive.writestr(name, contents)
    return buffer.getvalue()


def test_windows_rocm_ctranslate2_installs_verified_wheel_in_private_temp(monkeypatch):
    setup = _load_setup()
    wheel = b"small fixture wheel"
    archive = _rocm_archive([
        ("../../should-not-be-extracted", b"bad"),
        (setup.WINDOWS_ROCM_CT2_WHEEL_MEMBER, wheel),
    ])
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_ARCHIVE_SHA256", hashlib.sha256(archive).hexdigest().upper())
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_WHEEL_SHA256", hashlib.sha256(wheel).hexdigest().upper())
    downloads = []

    def download(url, timeout):
        downloads.append((url, timeout))
        return BytesIO(archive)

    monkeypatch.setattr(setup.urllib.request, "urlopen", download)
    installs = []

    def install(command):
        wheel_path = Path(command[-1])
        assert wheel_path.name == "ctranslate2-4.8.2-cp312-cp312-win_amd64.whl"
        assert wheel_path.read_bytes() == wheel
        assert sorted(path.name for path in wheel_path.parent.iterdir()) == sorted([
            "rocm-python-wheels-Windows.zip", wheel_path.name,
        ])
        installs.append((command, wheel_path))

    monkeypatch.setattr(setup.subprocess, "check_call", install)
    setup._install_windows_rocm_ctranslate2()
    assert downloads[0][0] == setup.WINDOWS_ROCM_CT2_URL
    assert installs[0][0] == [
        "uv", "--no-config", "pip", "install", "--no-deps", "--python",
        setup.sys.executable, "--reinstall-package", "ctranslate2", str(installs[0][1]),
    ]
    assert not installs[0][1].parent.exists()


@pytest.mark.parametrize("problem", ["archive_hash", "missing_wheel", "duplicate_wheel", "wheel_hash", "invalid_zip"])
def test_windows_rocm_ctranslate2_rejects_untrusted_archive(monkeypatch, problem):
    setup = _load_setup()
    members = [(setup.WINDOWS_ROCM_CT2_WHEEL_MEMBER, b"wheel")]
    if problem == "missing_wheel":
        members = [("../ctranslate2-4.8.2-cp312-cp312-win_amd64.whl", b"wheel")]
    if problem == "duplicate_wheel":
        members.append(members[0])
    if problem == "duplicate_wheel":
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive = _rocm_archive(members)
    else:
        archive = b"invalid archive" if problem == "invalid_zip" else _rocm_archive(members)
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_ARCHIVE_SHA256", (
        "0" * 64 if problem == "archive_hash" else hashlib.sha256(archive).hexdigest().upper()
    ))
    monkeypatch.setattr(setup, "WINDOWS_ROCM_CT2_WHEEL_SHA256", (
        "0" * 64 if problem == "wheel_hash" else hashlib.sha256(b"wheel").hexdigest().upper()
    ))
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda url, timeout: BytesIO(archive))
    if problem == "archive_hash":
        monkeypatch.setattr(setup.zipfile, "ZipFile", lambda *args: pytest.fail("unverified ZIP opened"))
    monkeypatch.setattr(setup.subprocess, "check_call", lambda command: pytest.fail("untrusted wheel installed"))
    with pytest.raises(RuntimeError, match="CTranslate2"):
        setup._install_windows_rocm_ctranslate2()


def test_windows_rocm_ctranslate2_validates_gpu_float16_in_isolated_interpreter(monkeypatch):
    setup = _load_setup()
    commands = []

    def probe(command, **kwargs):
        commands.append((command, kwargs))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup.subprocess, "run", probe)
    setup._check_windows_rocm_ctranslate2()
    command, kwargs = commands[0]
    assert command[:2] == [setup.sys.executable, "-c"]
    assert "ctranslate2.__version__ != '4.8.2'" in command[2]
    assert "hipblas.dll" in command[2]
    assert "amdhip64_7.dll" in command[2]
    assert "get_cuda_device_count()" in command[2]
    assert "get_supported_compute_types('cuda')" in command[2]
    assert "float16" in command[2]
    assert kwargs["capture_output"] is True


def test_windows_rocm_ctranslate2_reports_native_gpu_failure(monkeypatch):
    setup = _load_setup()
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=1, stdout="", stderr="OSError: missing amdhip64.dll",
    ))
    with pytest.raises(RuntimeError, match="amdhip64.dll"):
        setup._check_windows_rocm_ctranslate2()


@pytest.mark.parametrize("layout", ["complete", "missing_dirs", "missing_torch"])
@pytest.mark.parametrize("failure_stage", [None, "import", "compute"], ids=["success", "import_failure", "compute_failure"])
def test_windows_rocm_ctranslate2_registers_installed_dll_dirs(monkeypatch, tmp_path, layout, failure_stage):
    setup = _load_setup()
    package_dir = tmp_path / "site-packages" / "ctranslate2"
    package_dir.mkdir(parents=True)
    (package_dir / "ctranslate2.dll").write_bytes(b"hipblas.dll\0amdhip64_7.dll\0")
    torch_dir = tmp_path / "other-site-packages" / "torch"
    directories = [
        package_dir,
        package_dir.parent / "_rocm_sdk_core" / "bin",
        package_dir.parent / "_rocm_sdk_libraries_custom" / "bin",
        torch_dir / "lib",
    ]
    expected = directories if layout == "complete" else directories[:3] if layout == "missing_torch" else directories[:1]
    for directory in expected:
        directory.mkdir(parents=True, exist_ok=True)
    expected = [str(directory.resolve()) for directory in expected]
    specs = {
        "ctranslate2": SimpleNamespace(submodule_search_locations=[str(package_dir)]),
        "torch": None if layout == "missing_torch" else SimpleNamespace(submodule_search_locations=[str(torch_dir)]),
    }
    find_spec = Mock(side_effect=lambda name: specs[name])
    monkeypatch.setattr(importlib.util, "find_spec", find_spec)
    registered = []
    active = []
    closed = []
    stages = []
    original_path = os.environ.get("PATH")

    @contextmanager
    def add_directory(directory):
        registered.append(directory)
        active.append(directory)
        try:
            yield object()
        finally:
            active.remove(directory)
            closed.append(directory)

    monkeypatch.setattr(os, "add_dll_directory", add_directory, raising=False)

    def check_stage(stage):
        stages.append(stage)
        assert registered == expected
        assert active == expected
        assert closed == []
        if stage == failure_stage:
            raise RuntimeError(f"native {stage} failed")

    fake = ModuleType("ctranslate2")
    fake.__file__ = str(package_dir / "__init__.py")
    fake.__version__ = "4.8.2"
    fake.get_cuda_device_count = lambda: check_stage("device") or 1
    fake.get_supported_compute_types = lambda device: check_stage("compute") or {"float16"}
    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        if name == "ctranslate2":
            check_stage("import")
            return fake
        if name == "torch":
            pytest.fail("Package discovery must not import native torch")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)

    def run_probe(command, **kwargs):
        try:
            exec(command[2], {})
        except Exception as exc:
            return SimpleNamespace(returncode=1, stdout="", stderr=str(exc))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup.subprocess, "run", run_probe)
    result = setup._probe_windows_rocm_ctranslate2()
    assert result.returncode == (0 if failure_stage is None else 1), result.stderr
    if failure_stage is not None:
        assert result.stderr == f"native {failure_stage} failed"
    assert stages == (["import"] if failure_stage == "import" else ["import", "device", "compute"])
    assert [call.args[0] for call in find_spec.call_args_list] == ["ctranslate2", "torch"]
    assert registered == expected
    assert active == []
    assert closed == expected[::-1]
    assert os.environ.get("PATH") == original_path


@pytest.mark.parametrize("version,library,device_count,compute_types,ready", [
    ("4.8.2", b"hipblas.dll\0amdhip64_7.dll\0", 1, {"float16"}, True),
    ("4.8.1", b"hipblas.dll\0amdhip64_7.dll\0", 1, {"float16"}, False),
    ("4.8.2", b"cublas64_12.dll\0", 1, {"float16"}, False),
    ("4.8.2", b"hipblas.dll\0amdhip64_7.dll\0", 0, {"float16"}, False),
    ("4.8.2", b"hipblas.dll\0amdhip64_7.dll\0", 1, {"int8"}, False),
])
def test_windows_rocm_ctranslate2_probe_rejects_locked_or_non_native_wheels(
    monkeypatch, tmp_path, version, library, device_count, compute_types, ready,
):
    setup = _load_setup()
    (tmp_path / "ctranslate2.dll").write_bytes(library)
    fake = ModuleType("ctranslate2")
    fake.__file__ = str(tmp_path / "__init__.py")
    fake.__version__ = version
    fake.get_cuda_device_count = lambda: device_count
    fake.get_supported_compute_types = lambda device: compute_types
    monkeypatch.setitem(sys.modules, "ctranslate2", fake)
    monkeypatch.setattr(importlib.util, "find_spec", lambda name: (
        SimpleNamespace(submodule_search_locations=[str(tmp_path)]) if name == "ctranslate2" else None
    ))
    monkeypatch.setattr(os, "add_dll_directory", lambda directory: nullcontext(), raising=False)

    def run_probe(command, **kwargs):
        try:
            exec(command[2], {})
        except Exception as exc:
            return SimpleNamespace(returncode=1, stdout="", stderr=str(exc))
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(setup.subprocess, "run", run_probe)
    assert (setup._probe_windows_rocm_ctranslate2().returncode == 0) is ready


def test_windows_rocm_ctranslate2_skips_network_for_valid_install(monkeypatch):
    setup = _load_setup()
    calls = []
    monkeypatch.setattr(setup.subprocess, "run", lambda *args, **kwargs: (
        calls.append("probe") or SimpleNamespace(returncode=0, stdout="", stderr="")
    ))
    monkeypatch.setattr(setup.urllib.request, "urlopen", lambda *args, **kwargs: pytest.fail("unnecessary download"))
    monkeypatch.setattr(setup, "_install_windows_rocm_ctranslate2", lambda: pytest.fail("unnecessary install"))
    setup._ensure_windows_rocm_ctranslate2()
    assert calls == ["probe"]


def test_windows_rocm_ctranslate2_reinstalls_after_uv_sync_downgrade(monkeypatch):
    setup = _load_setup()
    monkeypatch.setattr(setup.sys, "platform", "win32")
    monkeypatch.setattr(setup, "_ensure_vcredist_windows", lambda: None)
    monkeypatch.setattr(setup, "_ensure_rocm_torch", lambda: None)
    monkeypatch.setattr(setup, "_rocm_opt_in", lambda: setup.WINDOWS_ROCM_FIND_LINKS)
    steps = []
    def probe(*args, **kwargs):
        steps.append("probe")
        return SimpleNamespace(returncode=1 if len(steps) % 3 == 1 else 0, stdout="", stderr="version mismatch")

    monkeypatch.setattr(setup.subprocess, "run", probe)
    monkeypatch.setattr(setup, "_install_windows_rocm_ctranslate2", lambda: steps.append("install"))
    setup.main()
    setup.main()
    assert steps == ["probe", "install", "probe", "probe", "install", "probe"]


def test_rocm_reinstall_targets_this_venv_with_pinned_stack():
    setup = _load_setup()
    cmd = setup.rocm_torch_reinstall_cmd("https://idx/", python="/venv/bin/python", platform="linux")
    assert cmd == [
        "uv",
        "pip",
        "install",
        "--reinstall",
        "--python",
        "/venv/bin/python",
        *setup.ROCM_TORCH_PINS,
        "--index-url",
        "https://idx/",
    ]
    assert setup.ROCM_TORCH_PINS == (
        "torch==2.8.0",
        "torchaudio==2.8.0",
        "torchvision==0.23.0",
    )


def test_dev_backend_skips_resync_when_rocm_requested():
    module_uri = Path(_DEV_BACKEND).as_uri()
    script = f"""
      const mod = await import({json.dumps(module_uri)});
      console.log(JSON.stringify({{
        base: mod.uvicornArgs({{}}),
        unset: mod.uvRunArgs({{}}),
        auto: mod.uvRunArgs({{ OMNIVOICE_TORCH_VARIANT: "auto" }}),
        rocm: mod.uvRunArgs({{ OMNIVOICE_TORCH_VARIANT: " ROCm " }}),
      }}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(completed.stdout)
    base = observed["base"]
    assert observed["unset"] == base
    assert observed["auto"] == base
    assert observed["rocm"] == [base[0], "--no-sync", *base[1:]]
