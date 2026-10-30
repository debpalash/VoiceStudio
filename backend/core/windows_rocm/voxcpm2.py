"""Standalone, model-free Windows HIP checks for the private VoxCPM2 venv."""

import contextlib
import hashlib
import json
import re
import sys
import sysconfig
from importlib import import_module, metadata, util
from pathlib import Path


RECIPE_PATH = Path(__file__).resolve().parents[3] / "scripts/windows-rocm-recipe.json"
MARKER_REVISION = "windows-rocm-voxcpm2-v1"
MARKER_NAME = ".voicestudio_install_complete"
_SHARED_RUNTIME = None
_SHARED_SIDECAR = None


def _shared_sidecar():
    global _SHARED_SIDECAR
    if _SHARED_SIDECAR is None:
        specification = util.spec_from_file_location(
            "_voxcpm2_shared_windows_rocm_sidecar", Path(__file__).with_name("sidecar.py"),
        )
        _SHARED_SIDECAR = util.module_from_spec(specification)
        specification.loader.exec_module(_SHARED_SIDECAR)
    return _SHARED_SIDECAR


def load_recipe() -> dict:
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    if not isinstance(recipe, dict) or recipe.get("schema_version") != 1:
        raise RuntimeError("invalid_report")
    for section in ("runtime", "torch"):
        artifacts = recipe.get(section)
        if not isinstance(artifacts, dict):
            raise RuntimeError("invalid_report")
        filenames = artifacts.get("wheel_files")
        hashes = artifacts.get("wheel_sha256")
        if not isinstance(filenames, list) or not filenames or not isinstance(hashes, dict):
            raise RuntimeError("invalid_report")
        for filename in filenames:
            if not isinstance(filename, str):
                raise RuntimeError("invalid_report")
            digest = hashes.get(filename)
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise RuntimeError("invalid_report")
    return recipe


def recipe_identity() -> str:
    return hashlib.sha256(RECIPE_PATH.read_bytes()).hexdigest()


def expected_versions() -> dict[str, str]:
    recipe = load_recipe()
    pins = recipe["torch"]["packages"] + recipe["runtime"]["packages"]
    return dict(pin.split("==", 1) for pin in pins) | {"voxcpm": "2.0.3", "torchcodec": "0.8.1"}


def install_arguments() -> list[str]:
    recipe = load_recipe()
    base_url = recipe["torch"]["find_links"]
    urls = [f"{base_url}{name}#sha256={recipe[section]['wheel_sha256'][name]}"
            for section in ("runtime", "torch") for name in recipe[section]["wheel_files"]]
    return ["voxcpm==2.0.3", "torchcodec==0.8.1", *urls]


def validate_host() -> None:
    if sys.platform != "win32" or sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("unsupported_target")


def validate_report(report: dict, python: Path) -> dict:
    _shared_sidecar().validate_gpu_report(
        report, python, scope="voxcpm2-model-free", recipe=load_recipe(),
        recipe_sha256=recipe_identity(), packages=expected_versions(),
    )
    if report.get("voxcpm_device") != "cuda":
        raise RuntimeError("invalid_report")
    return report


def marker_text(report: dict, python: Path) -> str:
    validate_report(report, python)
    return f"voxcpm\n{MARKER_REVISION}\n{json.dumps(report, sort_keys=True)}\n"


def read_verified_install(checkout: Path) -> dict | None:
    """Check the attestation against current files without importing native code."""
    try:
        lines = (checkout / MARKER_NAME).read_text(encoding="utf-8").splitlines()
        if len(lines) != 3 or lines[:2] != ["voxcpm", MARKER_REVISION]:
            return None
        python = checkout / ".venv/Scripts/python.exe"
        if not python.is_file():
            return None
        report = validate_report(json.loads(lines[2]), python)
        if not _shared_sidecar().environment_matches(checkout, expected_versions(), report["hip"]):
            return None
        return report
    except (OSError, UnicodeError, ValueError, SyntaxError, TypeError, KeyError, RuntimeError):
        return None


def _initialize_dll_search(*, initialize_sdk: bool = False) -> None:
    global _SHARED_RUNTIME
    if __package__:
        shared = import_module(".runtime", __package__)
    else:
        if _SHARED_RUNTIME is None:
            specification = util.spec_from_file_location(
                "_voxcpm2_shared_windows_rocm_runtime", Path(__file__).with_name("runtime.py")
            )
            shared = util.module_from_spec(specification)
            specification.loader.exec_module(shared)
            _SHARED_RUNTIME = shared
        shared = _SHARED_RUNTIME
    if initialize_sdk:
        shared.initialize_development_sdk()
    shared.initialize_dll_search()


def _runtime_torch():
    if sys.platform != "win32" or sys.version_info[:2] != (3, 12) or sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("unsupported_target")
    if any(metadata.version(name) != version for name, version in expected_versions().items()):
        raise RuntimeError("package_version")
    _initialize_dll_search()
    torch = import_module("torch")
    if not str(torch.version.hip).startswith(load_recipe()["torch"]["hip_prefix"]):
        raise RuntimeError("package_version")
    return torch


def verify_gpu() -> dict:
    """Execute the sidecar's actual Torch kernel and VoxCPM device resolver."""
    torch = _runtime_torch()
    for package in ("torchaudio", "torchvision", "librosa", "voxcpm"):
        import_module(package)
    if not torch.cuda.is_available():
        raise RuntimeError("device_runtime")
    architecture = str(torch.cuda.get_device_properties(0).gcnArchName).split(":", 1)[0]
    if architecture not in load_recipe()["gpu_architectures"]:
        raise RuntimeError("unsupported_gpu")
    tensor = torch.ones((4, 4), device="cuda")
    result = tensor @ tensor
    torch.cuda.synchronize()
    if result.device.type != "cuda" or not bool(torch.all(result == 4).item()):
        raise RuntimeError("device_runtime")
    resolver = import_module("voxcpm.model.utils").resolve_runtime_device
    report = {
        "ready": True, "scope": "voxcpm2-model-free", "python": sys.executable,
        "architecture": sysconfig.get_platform(), "abi": "cp312",
        "packages": {name: metadata.version(name) for name in expected_versions()},
        "recipe_sha256": recipe_identity(),
        "hip": torch.version.hip, "torch_gpu": True, "gpu_architecture": architecture,
        "kernel_result": result[0, 0].item(), "kernel_device": result.device.type,
        "voxcpm_device": resolver(None),
    }
    return validate_report(report, Path(sys.executable))


def validate_model_device(model, device: str) -> None:
    """Do not let an already loaded model or an automatic fallback misreport GPU use."""
    components = getattr(model, "tts_model", None)
    parameters = list(components.parameters()) if components is not None else []
    if not parameters or any(parameter.device.type != device for parameter in parameters):
        raise RuntimeError("device_runtime")


def runtime_device(device: str) -> str:
    if device == "cpu":
        _runtime_torch()
        return device
    if device != "cuda":
        raise RuntimeError("device_runtime")
    verify_gpu()
    return device


def main() -> int:
    try:
        with contextlib.redirect_stdout(sys.stderr):
            validate_host()
            _initialize_dll_search(initialize_sdk=True)
            report = verify_gpu()
    except Exception as error:
        category = "package_missing" if isinstance(error, metadata.PackageNotFoundError) else str(error)
        if category not in {"unsupported_target", "package_version", "device_runtime", "unsupported_gpu", "invalid_report"}:
            if category != "package_missing":
                category = "native_runtime"
        print(json.dumps({"ready": False, "category": category}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
