"""Native model-free probe. Execute ONLY as a bounded subprocess."""

import json
import os
import sys
import sysconfig
from importlib import metadata

from .artifacts import sha256
from .failures import VerificationFailure, failure_report
from .native import import_native
from .recipe import ct2_supported, expected_versions, load_recipe, validate_target
from .runtime import initialize_dll_search, package_root
from .verify import validate_report


def probe():
    validate_target()
    recipe = load_recipe()
    if (any(arch in recipe["rocm10"]["devices"] for arch in recipe["gpu_architectures"])
            and os.environ.get("HSA_OVERRIDE_GFX_VERSION")):
        raise VerificationFailure(
            "device_runtime", "Remove HSA_OVERRIDE_GFX_VERSION; the ROCm 10 recipe uses native kernels"
        )
    try:
        packages = {name: metadata.version(name) for name in expected_versions(recipe)}
    except metadata.PackageNotFoundError as exc:
        raise VerificationFailure("package_missing", exc) from exc
    if packages != expected_versions(recipe):
        raise VerificationFailure(
            "package_version", "Installed packages differ from the pinned Windows ROCm recipe; rerun setup"
        )
    # A recipe without a verified HIP CTranslate2 (ROCm 10) pins none, so its
    # probe must not demand one either — it would fail a healthy PyTorch overlay
    # over an ASR path this recipe deliberately leaves on CPU.
    binary_hash = None
    if ct2_supported(recipe):
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
    ctranslate2 = import_native("ctranslate2") if ct2_supported(recipe) else None

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
            # TheTorch's AMD wheels report HIP 7.15 for ROCm 10; the field stays
            # torch's own string so verification compares like with like.
            "hip": torch.version.hip,
            "torch_gpu": True,
            "kernel_result": result[0, 0].item(),
            "gpu_architecture": architecture,
            "ct2_supported": ct2_supported(recipe),
            "ct2_gpu_count": ctranslate2.get_cuda_device_count() if ctranslate2 else 0,
            "ct2_compute_types": sorted(ctranslate2.get_supported_compute_types("cuda")) if ctranslate2 else [],
            "ct2_binary_sha256": binary_hash,
        }
        # A second kernel family from the same wheel: TTS reaches convolutions
        # long before it reaches a matmul-only path, so a matmul-only probe can
        # pass on a GPU that cannot run the engines at all (verified: the ROCm
        # 7.2.1 HIP CT2 binary launches no kernel on ROCm 10 while matmul works).
        conv = torch.nn.Conv1d(8, 8, 3, padding=1, groups=2).cuda().to(torch.float16)
        samples = torch.ones((1, 8, 16), device="cuda", dtype=torch.float16)
        output = conv(samples)
        torch.cuda.synchronize()
        if output.shape != (1, 8, 16) or not bool(torch.isfinite(output).all()):
            raise VerificationFailure("device_runtime", "HIP grouped-convolution kernel failed")
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
