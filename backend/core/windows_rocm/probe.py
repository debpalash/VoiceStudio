"""Native model-free probe. Execute ONLY as a bounded subprocess."""

import json
import sys
import sysconfig
from importlib import metadata

from .artifacts import sha256
from .failures import VerificationFailure, failure_report
from .native import import_native
from .recipe import expected_versions, load_recipe, validate_target
from .runtime import initialize_dll_search, package_root
from .verify import validate_report


def probe():
    validate_target()
    recipe = load_recipe()
    try:
        packages = {name: metadata.version(name) for name in expected_versions(recipe)}
    except metadata.PackageNotFoundError as exc:
        raise VerificationFailure("package_missing", exc) from exc
    if packages != expected_versions(recipe):
        raise VerificationFailure(
            "package_version", "Installed packages differ from the pinned Windows ROCm recipe; rerun setup"
        )
    root = package_root("ctranslate2")
    if root is None:
        raise VerificationFailure("package_missing", "Pinned HIP CTranslate2 is missing")
    binary = root / "ctranslate2.dll"
    try:
        binary_hash = sha256(binary)
    except FileNotFoundError as exc:
        raise VerificationFailure("binary_integrity", exc) from exc
    if binary_hash != recipe["ctranslate2"]["binary_sha256"]:
        raise VerificationFailure("binary_integrity", "Installed CTranslate2 is not the pinned HIP binary; rerun setup")
    initialize_dll_search()
    # Keep Torch loaded before CT2; classify failed imports from wheel evidence.
    torch = import_native("torch")
    import_native("torchaudio")
    import_native("torchvision")
    ctranslate2 = import_native("ctranslate2")

    if not str(torch.version.hip).startswith(recipe["torch"]["hip_prefix"]):
        raise VerificationFailure("package_version", "Torch is not the required HIP build")
    try:
        if not torch.cuda.is_available():
            raise VerificationFailure(
                "device_runtime", "HIP cannot access a GPU; check AMD's Windows hardware/driver matrix"
            )
        architecture = str(torch.cuda.get_device_properties(0).gcnArchName).split(":", 1)[0]
        if architecture not in recipe["gpu_architectures"]:
            raise VerificationFailure(
                "unsupported_gpu", f"AMD GPU {architecture} is outside the pinned Windows ROCm hardware matrix"
            )
        tensor = torch.ones((4, 4), device="cuda")
        result = tensor @ tensor
        torch.cuda.synchronize()
        if result.device.type != "cuda" or not bool(torch.all(result == 4).item()):
            raise VerificationFailure("device_runtime", "HIP GPU arithmetic failed or fell back to CPU")

        report = {
            "ready": True,
            "scope": "model-free",
            "python": sys.executable,
            "architecture": sysconfig.get_platform(),
            "abi": "cp312",
            "packages": packages,
            "hip": torch.version.hip,
            "torch_gpu": True,
            "kernel_result": result[0, 0].item(),
            "gpu_architecture": architecture,
            "ct2_gpu_count": ctranslate2.get_cuda_device_count(),
            "ct2_compute_types": sorted(ctranslate2.get_supported_compute_types("cuda")),
            "ct2_binary_sha256": binary_hash,
        }
    except VerificationFailure:
        raise
    except Exception as exc:  # Device errors never establish package damage.
        raise VerificationFailure("device_runtime", exc) from exc
    return validate_report(report, recipe)


if __name__ == "__main__":
    try:
        print(json.dumps(probe()))
    except Exception as exc:  # noqa: BLE001 - native probe CLI must fail closed for any import/kernel error.
        print(json.dumps(failure_report(exc)))
        raise SystemExit(1)
