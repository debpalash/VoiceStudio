"""Explicit Windows HIP sidecar provisioning and fail-closed device selection."""

import importlib.util
import io
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


ROOT = Path(__file__).resolve().parents[1]
WORKER = ROOT / "backend/engines/voxcpm2_subprocess/main.py"


@pytest.fixture
def installer(monkeypatch, tmp_path):
    from services import sidecar_install
    from core.windows_rocm import voxcpm2

    monkeypatch.setattr(sidecar_install, "DATA_DIR", tmp_path)
    monkeypatch.delenv("OMNIVOICE_ENGINES_DIR", raising=False)
    monkeypatch.delenv("OMNIVOICE_VOXCPM2_DIR", raising=False)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm")
    monkeypatch.setattr(sidecar_install.sys, "platform", "win32")
    monkeypatch.setattr(voxcpm2.sysconfig, "get_platform", lambda: "win-amd64")
    monkeypatch.setattr(sidecar_install, "_host_family", lambda: "rocm")
    monkeypatch.setattr(sidecar_install, "_locate_uv", lambda: "test-uv")
    return sidecar_install


def report(python):
    recipe_bytes = (ROOT / "scripts/windows-rocm-recipe.json").read_bytes()
    import hashlib

    recipe = json.loads(recipe_bytes)
    packages = dict(pin.split("==") for pin in recipe["torch"]["packages"] + recipe["runtime"]["packages"])
    packages.update({"voxcpm": "2.0.3", "torchcodec": "0.8.1"})
    return {
        "ready": True, "scope": "voxcpm2-model-free", "python": str(python),
        "architecture": "win-amd64", "abi": "cp312", "packages": packages,
        "recipe_sha256": hashlib.sha256(recipe_bytes).hexdigest(),
        "hip": "7.2.53211", "torch_gpu": True, "gpu_architecture": "gfx1201",
        "kernel_result": 4.0, "kernel_device": "cuda", "voxcpm_device": "cuda",
    }


def test_new_explicit_rocm_install_uses_cp312_sdk_and_no_cpu_wheels(installer, monkeypatch):
    spec = installer.get_spec("voxcpm2")
    calls = []

    def run(job, arguments, **kwargs):
        calls.append(arguments)
        python = installer._venv_python(installer.managed_checkout(spec) / ".venv")
        python.parent.mkdir(parents=True, exist_ok=True)
        python.write_text("fixture interpreter", encoding="utf-8")
        return 0

    monkeypatch.setattr(installer, "_run_logged", run)
    job = installer._new_job(spec.engine_id)
    installer._step_create_venv(spec, job)
    installer._step_install_deps(spec, job)
    assert calls[0][-2:] == ["--python", "3.12"]
    arguments = calls[1]
    assert "voxcpm==2.0.3" in arguments and "torchcodec==0.8.1" in arguments
    assert any("rocm_sdk_devel-7.2.1" in argument for argument in arguments)
    assert any("torch-2.9.1%2Brocm7.2.1-cp312-cp312-win_amd64.whl" in argument for argument in arguments)
    assert not any("+cpu" in argument or "+cu128" in argument or "ctranslate2" in argument for argument in arguments)


@pytest.mark.parametrize("change", [
    {"ready": 1}, {"kernel_result": True}, {"kernel_device": "cpu"},
    {"voxcpm_device": "cpu"}, {"gpu_architecture": "gfx1102"},
    {"packages": {}}, {"python": "different-python.exe"}, {"recipe_sha256": "stale"},
])
def test_probe_rejects_malformed_or_cpu_success_before_marker(installer, monkeypatch, change):
    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    python = installer._venv_python(checkout / ".venv")
    result = report(python)
    result.update(change)
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(result).encode(), stderr=b""))
    with pytest.raises(installer._StepError):
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert not (checkout / installer._INSTALL_COMPLETE_MARKER).exists()


def test_probe_requires_valid_json_instead_of_exit_zero(installer, monkeypatch):
    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=b"not a readiness report", stderr=b""))
    with pytest.raises(installer._StepError):
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert not (checkout / installer._INSTALL_COMPLETE_MARKER).exists()


def installed_fixture(installer):
    from core.windows_rocm import voxcpm2

    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    python = checkout / ".venv/Scripts/python.exe"
    python.parent.mkdir(parents=True)
    python.write_text("fixture interpreter", encoding="utf-8")
    (checkout / ".venv/pyvenv.cfg").write_text("version_info = 3.12.10\n", encoding="utf-8")
    packages = checkout / ".venv/Lib/site-packages"
    for name, version in voxcpm2.expected_versions().items():
        distribution = packages / f"{name.replace('-', '_')}-{version}.dist-info"
        distribution.mkdir(parents=True)
        (distribution / "METADATA").write_text(f"Name: {name}\nVersion: {version}\n", encoding="utf-8")
    torch = packages / "torch"
    torch.mkdir()
    (torch / "version.py").write_text("__version__ = '2.9.1+rocm7.2.1'\nhip = '7.2.53211'\n", encoding="utf-8")
    result = report(python)
    (checkout / installer._INSTALL_COMPLETE_MARKER).write_text(voxcpm2.marker_text(result, python), encoding="utf-8")
    return checkout, result


@pytest.mark.parametrize(
    "configuration,expected",
    [
        ("version_info = 3.12\n", True),
        ("version_info = 3.12.14\n", True),
        ("version = 3.12\n", True),
        ("version = 3.12.14\n", True),
        ("version_info = 3.11.14\n", False),
        ("version_info = 3.120\n", False),
        ("version_info = 3.12.invalid\n", False),
        ("version_info = 3.12.14-extra\n", False),
        ("version_info = 3.12.-1\n", False),
        ("version_info = 3.11\nversion = 3.12.14\n", False),
    ],
)
def test_gpu_receipt_accepts_uv_minor_version_and_rejects_malformed_versions(
    installer, configuration, expected
):
    from core.windows_rocm import voxcpm2

    checkout, result = installed_fixture(installer)
    (checkout / ".venv/pyvenv.cfg").write_text(configuration, encoding="utf-8")

    verified = voxcpm2.read_verified_install(checkout)

    assert (verified == result) is expected
    assert installer._install_marker_valid(installer.get_spec("voxcpm2"), checkout) is expected


@pytest.mark.parametrize("damage", ["none", "missing-sdk", "wrong-version", "cpu-torch", "wrong-python", "empty", "unknown-revision"])
def test_gpu_marker_requires_current_sidecar_files(installer, monkeypatch, damage):
    from core.device_caps import HostCaps
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    checkout, result = installed_fixture(installer)
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    if damage == "missing-sdk":
        (checkout / ".venv/Lib/site-packages/rocm_sdk_devel-7.2.1.dist-info/METADATA").unlink()
    elif damage == "wrong-version":
        (checkout / ".venv/Lib/site-packages/voxcpm-2.0.3.dist-info/METADATA").write_text("Name: voxcpm\nVersion: 2.0.2\n", encoding="utf-8")
    elif damage == "cpu-torch":
        (checkout / ".venv/Lib/site-packages/torch/version.py").write_text("__version__ = '2.9.1+rocm7.2.1'\nhip = None\n", encoding="utf-8")
    elif damage == "wrong-python":
        (checkout / ".venv/pyvenv.cfg").write_text("version_info = 3.11.10\n", encoding="utf-8")
    elif damage == "empty":
        marker.write_text("voxcpm\nwindows-rocm-voxcpm2-v1\n", encoding="utf-8")
    elif damage == "unknown-revision":
        marker.write_text("voxcpm\nwindows-rocm-voxcpm2-v2\n{}\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(checkout))
    expected = damage == "none"
    assert installer.voxcpm2_rocm_verified() is expected
    assert installer._install_marker_valid(installer.get_spec("voxcpm2"), checkout) is expected
    profile = VoxCPM2SubprocessBackend.runtime_compute_profile(HostCaps(family="rocm", available_families=("rocm", "cpu")))
    assert profile["routing_status"] == ("accelerated" if expected else "cpu_fallback")
    assert profile["effective_device"] == ("rocm" if expected else "cpu")
    cpu = VoxCPM2SubprocessBackend.runtime_compute_profile(HostCaps(family="cpu", available_families=("rocm", "cpu"), requested_family="cpu"))
    assert cpu["effective_device"] == "cpu"


@pytest.mark.parametrize("missing_sdk", [False, True])
def test_missing_distribution_metadata_cannot_crash_receipt_validation(installer, monkeypatch, missing_sdk):
    from core.windows_rocm import voxcpm2

    checkout, result = installed_fixture(installer)
    if missing_sdk:
        (checkout / ".venv/Lib/site-packages/rocm_sdk_devel-7.2.1.dist-info/METADATA").unlink()
    distributions = list(voxcpm2.metadata.distributions(path=[str(checkout / ".venv/Lib/site-packages")]))
    distributions.append(SimpleNamespace(metadata=None, version=None))
    monkeypatch.setattr(voxcpm2.metadata, "distributions", lambda **options: iter(distributions))

    verified = voxcpm2.read_verified_install(checkout)

    assert (verified == result) is (not missing_sdk)


@pytest.mark.parametrize("platform,family,variant", [
    ("win32", "rocm", ""), ("win32", "cuda", ""), ("win32", "cpu", ""),
    ("linux", "rocm", "rocm"), ("linux", "cuda", ""), ("darwin", "mps", ""),
])
def test_other_host_recipes_remain_unchanged(installer, monkeypatch, platform, family, variant):
    monkeypatch.setattr(installer.sys, "platform", platform)
    monkeypatch.setattr(installer, "_host_family", lambda: family)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    calls = []
    monkeypatch.setattr(installer, "_run_logged", lambda job, argv, **kwargs: calls.append(argv) or 0)
    spec = installer.get_spec("voxcpm2")
    installer._step_install_deps(spec, installer._new_job(spec.engine_id))
    arguments = calls[0]
    assert "voxcpm==2.0.3" in arguments
    assert all(argument in arguments for argument in installer._torch_pin_args(spec))
    assert not any("rocm_sdk" in argument or "torchcodec==0.8.1" == argument for argument in arguments)


@pytest.mark.parametrize("external", [False, True])
def test_healthy_cpu_and_user_managed_installs_are_not_migrated(installer, monkeypatch, tmp_path, external):
    spec = installer.get_spec("voxcpm2")
    checkout = tmp_path / "external" if external else installer.managed_checkout(spec)
    python = installer._venv_python(checkout / ".venv")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"existing CPU interpreter")
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker.write_bytes(b"voxcpm\n")
    monkeypatch.setenv(spec.env_var, str(checkout))
    monkeypatch.setattr(installer, "_safe_installed", lambda current: True)
    monkeypatch.setattr(installer, "_run_logged", lambda *args, **kwargs: pytest.fail("must not provision"))
    assert installer.start_install(spec.engine_id)["status"] == "already_installed"
    assert not installer.voxcpm2_rocm_verified()
    assert python.read_bytes() == b"existing CPU interpreter" and marker.read_bytes() == b"voxcpm\n"


def test_cold_sdk_probe_has_its_own_bounded_setup_budget(installer, monkeypatch):
    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    python = installer._venv_python(checkout / ".venv")
    calls = []

    def cold_probe(arguments, **options):
        calls.append((arguments, options))
        if options["timeout"] < 180:
            raise installer.subprocess.TimeoutExpired(arguments, options["timeout"])
        return SimpleNamespace(returncode=0, stdout=json.dumps(report(python)).encode(), stderr=b"")

    monkeypatch.setattr(installer.subprocess, "run", cold_probe)

    installer._step_verify(spec, installer._new_job(spec.engine_id))

    assert calls[0][1]["timeout"] == 600
    assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert calls[0][1]["env"]["TRANSFORMERS_OFFLINE"] == "1"
    assert (checkout / installer._INSTALL_COMPLETE_MARKER).is_file()


@pytest.mark.parametrize("platform,variant,engine", [
    ("win32", "cpu", "voxcpm2"),
    ("win32", "cuda", "voxcpm2"),
    ("linux", "rocm", "voxcpm2"),
    ("darwin", "rocm", "voxcpm2"),
    ("win32", "rocm", "indextts2"),
])
def test_ordinary_import_probe_retains_short_budget(installer, monkeypatch, platform, variant, engine):
    monkeypatch.setattr(installer.sys, "platform", platform)
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", variant)
    spec = installer.get_spec(engine)
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    calls = []
    monkeypatch.setattr(installer.subprocess, "run", lambda arguments, **options: calls.append(
        (arguments, options)
    ) or SimpleNamespace(returncode=0, stdout=b"", stderr=b""))

    installer._step_verify(spec, installer._new_job(spec.engine_id))

    assert calls[0][1]["timeout"] == 120
    assert calls[0][0][1] == "-c"


@pytest.mark.parametrize("failure", ["exit", "timeout", "crash"])
def test_failed_probe_never_publishes_marker(installer, monkeypatch, failure):
    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    calls = []

    def run(arguments, **kwargs):
        calls.append((arguments, kwargs))
        if failure == "timeout":
            raise installer.subprocess.TimeoutExpired(arguments, kwargs["timeout"])
        if failure == "crash":
            raise OSError("fixture failure")
        return SimpleNamespace(returncode=1, stdout=b"{}", stderr=b"device_runtime")

    monkeypatch.setattr(installer.subprocess, "run", run)
    with pytest.raises(installer._StepError):
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert calls[0][0][0] == str(installer._venv_python(checkout / ".venv"))
    assert calls[0][1]["timeout"] == 600
    assert calls[0][1]["env"]["HF_HUB_OFFLINE"] == "1"
    assert not (checkout / installer._INSTALL_COMPLETE_MARKER).exists()


def load_worker():
    specification = importlib.util.spec_from_file_location("_voxcpm2_rocm_worker_test", WORKER)
    worker = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(worker)
    return worker


@pytest.mark.parametrize("device", ["cuda", "cpu"])
def test_worker_validates_model_and_honors_explicit_device(monkeypatch, device):
    from core.windows_rocm import voxcpm2

    worker = load_worker()
    validations = []
    monkeypatch.setattr(worker, "_windows_rocm_support", lambda: voxcpm2)
    monkeypatch.setattr(voxcpm2, "runtime_device", lambda selected: validations.append(selected) or selected)
    calls = []
    model = SimpleNamespace(tts_model=SimpleNamespace(parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type=device))])))
    monkeypatch.setitem(sys.modules, "voxcpm", SimpleNamespace(VoxCPM=SimpleNamespace(
        from_pretrained=lambda *args, **kwargs: calls.append(kwargs) or model)))
    assert worker._load_model(io.BytesIO(), device) is model
    assert worker._load_model(io.BytesIO(), device) is model
    assert calls == [{"load_denoiser": False, "optimize": False, "device": device}]
    assert validations == [device, device]
    model.tts_model.parameters = lambda: iter([SimpleNamespace(device=SimpleNamespace(type="mps"))])
    with pytest.raises(RuntimeError, match="device_runtime"):
        worker._load_model(io.BytesIO(), device)


def test_worker_rejects_model_cpu_fallback_before_caching(monkeypatch):
    from core.windows_rocm import voxcpm2

    worker = load_worker()
    monkeypatch.setattr(worker, "_windows_rocm_support", lambda: voxcpm2)
    monkeypatch.setattr(voxcpm2, "runtime_device", lambda selected: selected)
    model = SimpleNamespace(tts_model=SimpleNamespace(parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type="cpu"))])))
    monkeypatch.setitem(sys.modules, "voxcpm", SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=lambda *args, **kwargs: model)))
    with pytest.raises(RuntimeError, match="device_runtime"):
        worker._load_model(io.BytesIO(), "cuda")
    assert worker._MODEL is None


@pytest.mark.parametrize("change", [{"python": None}, {"packages": []}, {"hip": None}, {"kernel_result": float("nan")}, {"ready": "true"}])
def test_report_parser_is_fail_closed(change, tmp_path):
    from core.windows_rocm import voxcpm2

    result = report(tmp_path / "python.exe")
    result.update(change)
    with pytest.raises(RuntimeError, match="invalid_report"):
        voxcpm2.validate_report(result, tmp_path / "python.exe")


@pytest.mark.parametrize("failure", [None, "cpu-kernel", "wrong-result", "partial-result", "cpu-resolver", "unsupported-gpu", "gpu-unavailable"])
def test_native_probe_executes_and_validates_gpu_result(monkeypatch, failure):
    from core.windows_rocm import voxcpm2

    operations = []
    expected = voxcpm2.expected_versions()
    monkeypatch.setattr(voxcpm2.metadata, "version", lambda name: expected[name])

    class Tensor:
        device = SimpleNamespace(type="cpu" if failure == "cpu-kernel" else "cuda")

        def __matmul__(self, other):
            operations.append("matmul")
            return self

        def __getitem__(self, index):
            return SimpleNamespace(item=lambda: 3.0 if failure == "wrong-result" else 4.0)

        def __eq__(self, value):
            return failure not in {"wrong-result", "partial-result"}

    def allocate(shape, device):
        assert device == "cuda" and shape == (4, 4)
        operations.append("allocate")
        return Tensor()

    torch = SimpleNamespace(version=SimpleNamespace(hip="7.2.53211"), ones=allocate,
        all=lambda value: SimpleNamespace(item=lambda: value), cuda=SimpleNamespace(
        is_available=lambda: failure != "gpu-unavailable",
        get_device_properties=lambda index: SimpleNamespace(gcnArchName="gfx1102" if failure == "unsupported-gpu" else "gfx1201"),
        synchronize=lambda: operations.append("synchronize"),
    ))
    monkeypatch.setattr(voxcpm2, "_runtime_torch", lambda: torch)
    monkeypatch.setattr(voxcpm2.sysconfig, "get_platform", lambda: "win-amd64")
    monkeypatch.setattr(voxcpm2, "import_module", lambda name: SimpleNamespace(resolve_runtime_device=lambda device: "cpu" if failure == "cpu-resolver" else "cuda"))
    if failure:
        with pytest.raises(RuntimeError):
            voxcpm2.verify_gpu()
    else:
        result = voxcpm2.verify_gpu()
        assert result["kernel_device"] == "cuda" and result["voxcpm_device"] == "cuda"
    assert operations == ([] if failure in {"unsupported-gpu", "gpu-unavailable"} else ["allocate", "matmul", "synchronize"])


@pytest.mark.parametrize("wrong", [None, "target", "version", "hip"])
def test_native_import_starts_with_torch_and_checks_exact_packages(monkeypatch, wrong):
    from core.windows_rocm import voxcpm2

    monkeypatch.setattr(voxcpm2.sys, "platform", "linux" if wrong == "target" else "win32")
    monkeypatch.setattr(voxcpm2.sys, "version_info", (3, 12, 10))
    monkeypatch.setattr(voxcpm2.sysconfig, "get_platform", lambda: "win-amd64")
    expected = voxcpm2.expected_versions()
    monkeypatch.setattr(voxcpm2.metadata, "version", lambda name: "0.0" if wrong == "version" and name == "rocm-sdk-devel" else expected[name])
    imports = []
    monkeypatch.setattr(voxcpm2, "_initialize_dll_search", lambda: imports.append("dll"))
    torch = SimpleNamespace(version=SimpleNamespace(hip=None if wrong == "hip" else "7.2.53211"))
    monkeypatch.setattr(voxcpm2, "import_module", lambda name: imports.append(name) or torch)
    if wrong:
        with pytest.raises(RuntimeError):
            voxcpm2._runtime_torch()
    else:
        assert voxcpm2._runtime_torch() is torch
    assert imports == ([] if wrong in {"target", "version"} else ["dll", "torch"])


def test_cpu_override_requires_no_gpu_probe(monkeypatch):
    from core.windows_rocm import voxcpm2

    monkeypatch.setattr(voxcpm2, "_runtime_torch", lambda: object())
    monkeypatch.setattr(voxcpm2, "verify_gpu", lambda: pytest.fail("explicit CPU must not require GPU"))
    assert voxcpm2.runtime_device("cpu") == "cpu"


def test_worker_reloads_when_explicit_device_changes(monkeypatch):
    from core.windows_rocm import voxcpm2

    worker = load_worker()
    monkeypatch.setattr(worker, "_windows_rocm_support", lambda: voxcpm2)
    monkeypatch.setattr(voxcpm2, "runtime_device", lambda selected: selected)
    devices = []

    def load(*args, **kwargs):
        device = kwargs["device"]
        devices.append(device)
        return SimpleNamespace(tts_model=SimpleNamespace(parameters=lambda: iter([SimpleNamespace(device=SimpleNamespace(type=device))])))

    monkeypatch.setitem(sys.modules, "voxcpm", SimpleNamespace(VoxCPM=SimpleNamespace(from_pretrained=load)))
    worker._load_model(io.BytesIO(), "cuda")
    worker._load_model(io.BytesIO(), "cpu")
    worker._load_model(io.BytesIO(), "cuda")
    assert devices == ["cuda", "cpu", "cuda"]


@pytest.mark.parametrize("failure", [None, "write", "replace"])
def test_gpu_marker_is_atomic_and_retryable(installer, monkeypatch, failure):
    spec = installer.get_spec("voxcpm2")
    checkout, result = installed_fixture(installer)
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker.unlink()
    monkeypatch.setattr(installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
        returncode=0, stdout=json.dumps(result).encode(), stderr=b""))
    original_write = Path.write_text
    original_replace = Path.replace

    def write(path, text, *args, **kwargs):
        if failure == "write" and path.name.startswith(marker.name):
            original_write(path, "", *args, **kwargs)
            raise OSError("interrupted write")
        return original_write(path, text, *args, **kwargs)

    def replace(path, target):
        if failure == "replace" and target == marker:
            raise PermissionError("interrupted replace")
        return original_replace(path, target)

    with monkeypatch.context() as publication:
        publication.setattr(Path, "write_text", write)
        publication.setattr(Path, "replace", replace)
        if failure:
            with pytest.raises(OSError):
                installer._step_verify(spec, installer._new_job(spec.engine_id))
            assert not marker.exists()
        else:
            installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert not list(checkout.glob(marker.name + ".*"))
    if failure:
        installer._step_verify(spec, installer._new_job(spec.engine_id))
    assert installer._install_marker_valid(spec, checkout)


@pytest.mark.parametrize("device", ["cpu", "rocm"])
def test_parent_forwards_the_selected_device_for_managed_hip(installer, monkeypatch, device):
    from core.device_caps import HostCaps
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend
    from services import audio_dsp, tts_backend

    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(checkout))
    capabilities = HostCaps(family=device, available_families=("rocm", "cpu"), requested_family=device)
    monkeypatch.setattr("core.device_caps.detect_host_caps", lambda: capabilities)
    monkeypatch.setattr(tts_backend, "prepare_voxcpm_reference", lambda options: None)
    monkeypatch.setattr(audio_dsp, "trim_trailing_silence", lambda waveform, rate: waveform)
    calls = []
    waveform = object()
    monkeypatch.setattr(VoxCPM2SubprocessBackend.__mro__[1], "generate", lambda instance, text, **options: calls.append(options) or waveform)
    backend = VoxCPM2SubprocessBackend()
    assert backend.generate("hello", _voxcpm2_runtime_device="mps") is waveform
    assert calls[0]["_voxcpm2_runtime_device"] == ("cpu" if device == "cpu" else "cuda")


@pytest.mark.parametrize("same_interpreter", [False, True])
def test_worker_receipt_is_bound_to_its_actual_interpreter(installer, monkeypatch, same_interpreter):
    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(checkout))
    monkeypatch.setattr(sys, "executable", result["python"] if same_interpreter else "other-python.exe")
    worker = load_worker()
    if same_interpreter:
        assert worker._windows_rocm_support().read_verified_install(checkout) == result
    else:
        with pytest.raises(RuntimeError, match="invalid_report"):
            worker._windows_rocm_support()


@pytest.mark.parametrize("phase", ["create", "deps"])
def test_wrong_architecture_stops_before_environment_modification(installer, monkeypatch, phase):
    from core.windows_rocm import voxcpm2

    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    checkout.mkdir(parents=True)
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    marker.write_bytes(b"voxcpm\n")
    monkeypatch.setattr(voxcpm2.sysconfig, "get_platform", lambda: "win-arm64")
    monkeypatch.setattr(installer, "_run_logged", lambda *args, **kwargs: pytest.fail("must not modify environment"))
    operation = installer._step_create_venv if phase == "create" else installer._step_install_deps
    with pytest.raises(RuntimeError, match="unsupported_target"):
        operation(spec, installer._new_job(spec.engine_id))
    assert marker.read_bytes() == b"voxcpm\n"


@pytest.mark.parametrize("opted", [False, True])
def test_constraint_isolation_changes_only_the_opted_install(installer, monkeypatch, opted):
    for variable in ("UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "PIP_CONSTRAINT"):
        monkeypatch.setenv(variable, "parent-constraints.txt")
    monkeypatch.setenv("OMNIVOICE_TORCH_VARIANT", "rocm" if opted else "")
    calls = []
    monkeypatch.setattr(installer, "_run_logged", lambda job, argv, **kwargs: calls.append(kwargs["env"]) or 0)
    spec = installer.get_spec("voxcpm2")
    installer._step_install_deps(spec, installer._new_job(spec.engine_id))
    for variable in ("UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "PIP_CONSTRAINT"):
        assert (variable in calls[0]) is not opted
    assert calls[0]["UV_NO_CONFIG"] == "1"


def test_installed_hip_keeps_guarded_runtime_when_install_optin_is_unset(installer, monkeypatch):
    from core.device_caps import HostCaps
    from engines.voxcpm2_subprocess import VoxCPM2SubprocessBackend

    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(checkout))
    monkeypatch.delenv("OMNIVOICE_TORCH_VARIANT")
    monkeypatch.setattr(sys, "executable", result["python"])
    assert not installer._voxcpm2_windows_rocm(installer.get_spec("voxcpm2"))
    assert installer.voxcpm2_rocm_verified()
    profile = VoxCPM2SubprocessBackend.runtime_compute_profile(HostCaps(family="rocm", available_families=("rocm", "cpu")))
    assert profile["effective_device"] == "rocm"
    assert load_worker()._windows_rocm_support() is not None


@pytest.mark.parametrize("phase", ["create", "deps"])
def test_linked_environment_is_not_followed_even_when_python_is_compatible(installer, monkeypatch, tmp_path, phase):
    spec = installer.get_spec("voxcpm2")
    checkout = installer.managed_checkout(spec)
    venv = checkout / ".venv"
    python = installer._venv_python(venv)
    python.parent.mkdir(parents=True)
    python.write_bytes(b"external compatible interpreter")
    original_resolve = Path.resolve
    monkeypatch.setattr(Path, "resolve", lambda path, *args, **kwargs: tmp_path / "external" if path == venv else original_resolve(path, *args, **kwargs))
    monkeypatch.setattr(installer, "_run_logged", lambda *args, **kwargs: pytest.fail("must not modify linked environment"))
    operation = installer._step_create_venv if phase == "create" else installer._step_install_deps
    with pytest.raises(installer._StepError, match="windows_rocm_voxcpm2_layout"):
        operation(spec, installer._new_job(spec.engine_id))
    assert python.read_bytes() == b"external compatible interpreter"


def test_hip_recipe_cannot_silently_keep_an_obsolete_voxcpm_pin(installer):
    from core.windows_rocm import voxcpm2

    package = "voxcpm==" + voxcpm2.expected_versions()["voxcpm"]
    assert package in installer.get_spec("voxcpm2").install_args
    assert package in voxcpm2.install_arguments()


@pytest.mark.parametrize("damage", ["missing", "legacy", "truncated", "unreadable"])
def test_started_hip_worker_cannot_downgrade_when_its_receipt_changes(installer, monkeypatch, damage):
    checkout, result = installed_fixture(installer)
    monkeypatch.setenv("OMNIVOICE_VOXCPM2_DIR", str(checkout))
    monkeypatch.setattr(sys, "executable", result["python"])
    worker = load_worker()
    assert worker._windows_rocm_support() is not None
    marker = checkout / installer._INSTALL_COMPLETE_MARKER
    if damage == "missing":
        marker.unlink()
    elif damage == "legacy":
        marker.write_text("voxcpm\n", encoding="utf-8")
    elif damage == "truncated":
        marker.write_bytes(b"")
    else:
        marker.write_bytes(b"\xff")
    with pytest.raises(RuntimeError, match="invalid_report"):
        worker._windows_rocm_support()


@pytest.mark.parametrize("failure", [False, True])
def test_packaged_helper_delegates_to_shared_runtime_initializer(monkeypatch, failure):
    from core.windows_rocm import runtime, voxcpm2

    calls = []
    monkeypatch.setattr(voxcpm2.util, "find_spec", lambda name: None)

    def initialize():
        calls.append("shared")
        if failure:
            raise RuntimeError("device_runtime")

    monkeypatch.setattr(runtime, "initialize_dll_search", initialize)
    if failure:
        with pytest.raises(RuntimeError, match="device_runtime"):
            voxcpm2._initialize_dll_search()
    else:
        voxcpm2._initialize_dll_search()
    assert calls == ["shared"]


def test_standalone_helper_retains_shared_runtime_and_calls_it_again(monkeypatch, tmp_path):
    from core.windows_rocm import voxcpm2

    runtime_path = tmp_path / "runtime.py"
    runtime_path.write_text("calls = 0\ndef initialize_dll_search():\n    global calls\n    calls += 1\n", encoding="utf-8")
    monkeypatch.setattr(voxcpm2, "__file__", str(tmp_path / "voxcpm2.py"))
    monkeypatch.setattr(voxcpm2, "__package__", "")
    monkeypatch.setattr(voxcpm2, "_SHARED_RUNTIME", None, raising=False)
    monkeypatch.setattr(voxcpm2.util, "find_spec", lambda name: None)
    voxcpm2._initialize_dll_search()
    shared = voxcpm2._SHARED_RUNTIME
    assert shared is not None and shared.calls == 1
    voxcpm2._initialize_dll_search()
    assert voxcpm2._SHARED_RUNTIME is shared and shared.calls == 2
