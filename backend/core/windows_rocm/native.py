"""Confirm wheel damage before retrying an ambiguous native loader failure."""

import base64
import hashlib
from importlib import metadata

from .failures import VerificationFailure
from .recipe import expected_versions, load_recipe

# These are the only separately pinned SDK roots used by runtime.dll_directories.
_SDK_ROOTS = {
    "rocm-sdk-core": "_rocm_sdk_core",
    "rocm-sdk-libraries-custom": "_rocm_sdk_libraries_custom",
}


def package_damage(package, root=None):
    """Inspect only recorded loadable package files, never external DLLs.

    This executes only after a failed import, inside the bounded native child.
    Missing/unreadable RECORD evidence cannot authorize a download.
    """
    root = root or package
    try:
        distribution = metadata.distribution(package)
        for recorded in distribution.files or ():
            if recorded.parts[0] not in {root, root + ".libs"} or recorded.suffix.lower() not in {
                ".py",
                ".pyd",
                ".dll",
            }:
                continue
            path = distribution.locate_file(recorded)
            if not path.is_file():
                return True
            expected = recorded.hash
            if expected and expected.mode in {"sha256", "sha384", "sha512"}:
                with path.open("rb") as source:
                    digest = hashlib.file_digest(source, expected.mode).digest()
                actual = base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")
                if actual != expected.value:
                    return True
    except (OSError, ValueError, metadata.PackageNotFoundError):
        return False
    return False


def native_package_damage(package):
    """Check the importing wheel and only recipe-pinned native SDK components."""
    if package_damage(package):
        return True
    pins = expected_versions(load_recipe())
    return any(package_damage(name, root) for name, root in _SDK_ROOTS.items() if name in pins)


def import_native(package):
    """An import failure alone may mean a missing driver/VC runtime, not damage."""
    try:
        return __import__(package)
    except Exception as exc:  # Import-time faults need evidence; BaseException control flow escapes.
        if isinstance(exc, ModuleNotFoundError) and exc.name == package:
            raise VerificationFailure("package_missing", exc) from exc
        category = "native_import" if native_package_damage(package) else "native_runtime"
        raise VerificationFailure(category, exc) from exc
