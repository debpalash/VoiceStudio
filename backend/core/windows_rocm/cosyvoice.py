"""Pinned managed CosyVoice 3 environment, HIP and media qualification."""

import contextlib
import hashlib
import importlib.util
import json
import os
import re
import sys
import sysconfig
from importlib import import_module, metadata
from pathlib import Path

RECIPE_PATH = Path(__file__).resolve().parents[3] / "scripts/windows-rocm-recipe.json"
REQUIREMENTS_PATH = Path(__file__).resolve().parents[2] / "engines/cosyvoice_subprocess/requirements.txt"
MARKER_REVISION = "windows-rocm-cosyvoice-v1"
MARKER_NAME = ".voicestudio_install_complete"
CHILD_CHECKOUT = "VOICESTUDIO_COSYVOICE_ROCM_CHECKOUT"
SOURCES = {
    "": ("074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc", "cosyvoice/cli/cosyvoice.py"),
    "third_party/Matcha-TTS": ("dd9105b34bf2be2230f4aa1e4769fb586a3c824e", "matcha/__init__.py"),
    "third_party/PyWorld": ("f31ad88d543fdaebbda2d0c9a5e4d4f991ae0b6c", "pyworld/__init__.py"),
    "third_party/PyWorld/lib/World": ("d625e7608ca23a870018f01e7c562ac683d9847f", "src/dio.cpp"),
}
_SHARED = None
_RUNTIME = None


def _shared():
    global _SHARED
    if _SHARED is None:
        specification = importlib.util.spec_from_file_location(
            "_cosyvoice_windows_rocm_sidecar", Path(__file__).with_name("sidecar.py"),
        )
        _SHARED = importlib.util.module_from_spec(specification)
        specification.loader.exec_module(_SHARED)
    return _SHARED


def _runtime():
    global _RUNTIME
    if _RUNTIME is None:
        _RUNTIME = _shared().load_sibling("runtime")
    return _RUNTIME


def load_recipe() -> dict:
    recipe = json.loads(RECIPE_PATH.read_text(encoding="utf-8"))
    if recipe.get("schema_version") != 1:
        raise RuntimeError("invalid_report")
    for section in ("torch", "runtime"):
        for filename in recipe[section]["wheel_files"]:
            if re.fullmatch(r"[0-9a-f]{64}", recipe[section]["wheel_sha256"][filename]) is None:
                raise RuntimeError("invalid_report")
    return recipe


def dependency_versions() -> dict[str, str]:
    result = {}
    for line in REQUIREMENTS_PATH.read_text(encoding="utf-8").splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==([A-Za-z0-9_.+-]+)", line)
        if match is None:
            raise RuntimeError("invalid_report")
        result[match[1].lower().replace("_", "-")] = match[2]
    return result | {"pyworld": "0.3.6"}


def expected_versions() -> dict[str, str]:
    recipe = load_recipe()
    return dependency_versions() | dict(pin.split("==", 1) for pin in recipe["torch"]["packages"] + recipe["runtime"]["packages"])


def install_arguments() -> list[str]:
    recipe = load_recipe()
    return [f"{recipe['torch']['find_links']}{name}#sha256={recipe[section]['wheel_sha256'][name]}"
            for section in ("runtime", "torch") for name in recipe[section]["wheel_files"]]


def recipe_identity() -> str:
    return hashlib.sha256(
        RECIPE_PATH.read_bytes() + REQUIREMENTS_PATH.read_bytes()
        + Path(__file__).read_bytes() + Path(__file__).with_name("sidecar.py").read_bytes()
    ).hexdigest()


def child_environment(environment, checkout: Path) -> dict[str, str]:
    result = dict(environment)
    for key in ("HIP_PATH", "ROCM_PATH", "ROCM_HOME"):
        result.pop(key, None)
    result[CHILD_CHECKOUT] = str(checkout)
    if (result.get("OMNIVOICE_DEVICE", "").strip().lower() == "cpu"
            or result.get("OMNIVOICE_TORCH_VARIANT", "").strip().lower() == "cpu"):
        result["HIP_VISIBLE_DEVICES"] = "-1"
        result["CUDA_VISIBLE_DEVICES"] = "-1"
    return result


def source_identity(checkout: Path) -> str:
    files = {}
    for directory, (revision, required) in SOURCES.items():
        root = checkout / directory
        if (root / ".voicestudio_source_revision").read_text(encoding="utf-8").strip() != revision:
            raise RuntimeError("invalid_report")
        files[f"{directory}/{required}"] = hashlib.sha256((root / required).read_bytes()).hexdigest()
    return hashlib.sha256(json.dumps(files, sort_keys=True).encode()).hexdigest()


def validate_host() -> None:
    if sys.platform != "win32" or sysconfig.get_platform() != "win-amd64":
        raise RuntimeError("unsupported_target")


def validate_report(report: dict, python: Path) -> dict:
    _shared().validate_gpu_report(report, python, scope="cosyvoice-model-free", recipe=load_recipe(),
                                  recipe_sha256=recipe_identity(), packages=expected_versions())
    if (report.get("cosyvoice_imports") is not True or report.get("media_backend") != "soundfile"
            or report.get("source_sha256") != source_identity(python.parents[2])):
        raise RuntimeError("invalid_report")
    return report


def marker_text(report: dict, python: Path) -> str:
    validate_report(report, python)
    return f"cosyvoice\n{MARKER_REVISION}\n{json.dumps(report, sort_keys=True)}\n"


def read_verified_install(checkout: Path) -> dict | None:
    try:
        if (checkout.resolve() != checkout.parent.resolve() / checkout.name
                or (checkout / ".venv").resolve() != checkout.resolve() / ".venv"):
            return None
        lines = (checkout / MARKER_NAME).read_text(encoding="utf-8").splitlines()
        if len(lines) != 3 or lines[:2] != ["cosyvoice", MARKER_REVISION]:
            return None
        python = checkout / ".venv/Scripts/python.exe"
        if not python.is_file():
            return None
        report = validate_report(json.loads(lines[2]), python)
        return report if _shared().environment_matches(checkout, expected_versions(), report["hip"]) else None
    except (OSError, UnicodeError, ValueError, SyntaxError, TypeError, KeyError, RuntimeError):
        return None


def load_audio(uri, frame_offset=0, num_frames=-1, normalize=True, channels_first=True,
               format=None, buffer_size=4096, backend=None):
    """The normalized, local soundfile path used by the pinned CosyVoice source."""
    if backend not in (None, "soundfile") or not normalize:
        raise ValueError("Unsupported CosyVoice audio backend or normalization")
    if isinstance(uri, (str, os.PathLike)) and re.match(r"^[a-z][a-z0-9+.-]*://", os.fspath(uri), re.I):
        raise ValueError("CosyVoice audio must be a local file or stream")
    if frame_offset < 0 or num_frames < -1:
        raise ValueError("Invalid audio frame range")
    import soundfile
    import torch

    with soundfile.SoundFile(uri) as audio:
        audio.seek(frame_offset)
        samples = audio.read(frames=num_frames, dtype="float32", always_2d=True)
        sample_rate = audio.samplerate
    samples = samples.T.copy() if channels_first else samples.copy()
    return torch.from_numpy(samples), sample_rate


def prepare_imports(checkout: Path) -> None:
    """Initialize this private SDK and adapt only the two upstream I/O namespaces."""
    _runtime().initialize_dll_search()
    import_module("torch")
    for path in (checkout, checkout / "third_party/Matcha-TTS"):
        if str(path) not in sys.path:
            sys.path.insert(0, str(path))
    for name in ("cosyvoice.utils.file_utils", "cosyvoice.dataset.processor"):
        module = import_module(name)
        original = module.torchaudio
        if getattr(original, "_voicestudio_cosyvoice_audio", False) is True:
            continue
        class AudioModule:
            load = staticmethod(load_audio)
            _voicestudio_cosyvoice_audio = True

            def __getattr__(self, key):
                return getattr(self.original, key)

        proxy = AudioModule()
        proxy.original = original
        module.torchaudio = proxy


def verify_gpu(checkout: Path) -> dict:
    validate_host()
    if sys.version_info[:2] != (3, 12):
        raise RuntimeError("unsupported_target")
    expected = expected_versions()
    if any(metadata.version(name) != version for name, version in expected.items()):
        raise RuntimeError("package_version")
    prepare_imports(checkout)
    for name in ("torchaudio", "torchvision", "pyworld", "onnxruntime", "matcha.utils"):
        import_module(name)
    import_module("cosyvoice.cli.cosyvoice").AutoModel
    torch = import_module("torch")
    if not torch.cuda.is_available():
        raise RuntimeError("device_runtime")
    recipe = load_recipe()
    architecture = str(torch.cuda.get_device_properties(0).gcnArchName).split(":", 1)[0]
    if architecture not in recipe["gpu_architectures"]:
        raise RuntimeError("unsupported_gpu")
    result = torch.ones((4, 4), device="cuda") @ torch.ones((4, 4), device="cuda")
    torch.cuda.synchronize()
    if result.device.type != "cuda" or not bool(torch.all(result == 4).item()):
        raise RuntimeError("device_runtime")
    audio, rate = load_audio(checkout / "asset/zero_shot_prompt.wav")
    if rate <= 0 or audio.numel() == 0 or not bool(torch.isfinite(audio).all()):
        raise RuntimeError("native_runtime")
    return validate_report({
        "ready": True, "scope": "cosyvoice-model-free", "python": sys.executable,
        "architecture": sysconfig.get_platform(), "abi": "cp312", "packages": expected,
        "recipe_sha256": recipe_identity(), "source_sha256": source_identity(checkout),
        "hip": torch.version.hip, "torch_gpu": True, "gpu_architecture": architecture,
        "kernel_result": result[0, 0].item(), "kernel_device": result.device.type,
        "cosyvoice_imports": True, "media_backend": "soundfile",
    }, Path(sys.executable))


def validate_model_device(model, device: str) -> None:
    components = getattr(model, "model", None)
    for name in ("llm", "flow", "hift"):
        component = getattr(components, name, None)
        parameters = list(component.parameters()) if component is not None else []
        if not parameters or any(parameter.device.type != device for parameter in parameters):
            raise RuntimeError("device_runtime")


def main() -> int:
    try:
        checkout = Path(sys.argv[1])
        with contextlib.redirect_stdout(sys.stderr):
            validate_host()
            _runtime().initialize_development_sdk()
            report = verify_gpu(checkout)
    except Exception as error:
        category = "package_missing" if isinstance(error, metadata.PackageNotFoundError) else str(error)
        if category not in {"unsupported_target", "package_missing", "package_version", "unsupported_gpu", "device_runtime", "invalid_report"}:
            category = "native_runtime"
        print(json.dumps({"ready": False, "category": category}))
        return 1
    print(json.dumps(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
