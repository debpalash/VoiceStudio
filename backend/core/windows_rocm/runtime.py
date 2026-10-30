"""Lifetime DLL search handles for actual native imports and isolated probes."""
import os
import stat
import sys
import sysconfig
import tempfile
from importlib import import_module, metadata
from pathlib import Path
from threading import RLock

_DLL_HANDLES = {}
_DLL_LOCK = RLock()

#: Import package -> distribution that installs it.
_DISTRIBUTIONS = {
    "torch": "torch",
    "ctranslate2": "ctranslate2",
    "_rocm_sdk_core": "rocm-sdk-core",
    "_rocm_sdk_devel": "rocm-sdk-devel",
    "_rocm_sdk_libraries_custom": "rocm-sdk-libraries-custom",
}


def package_root(package):
    """The installed package directory, found through its distribution record.

    ``importlib.util.find_spec`` searches ``sys.path``, which in a ``-m`` child
    starts with the working directory: a stray ``ctranslate2/`` folder there
    would decide which DLL directories get registered. Distribution records are
    looked up only in this interpreter's site-packages.
    """
    paths = sysconfig.get_paths()
    search = list(dict.fromkeys(paths[key] for key in ("purelib", "platlib") if paths.get(key)))
    dist = next(iter(metadata.distributions(name=_DISTRIBUTIONS.get(package, package), path=search)), None)
    if dist is None:
        return None
    root = Path(dist.locate_file(package))
    return root if root.is_dir() else None


def dll_directories():
    """Discover package locations without importing (and locking) Torch/CT2."""
    try:
        if "rocm" not in metadata.version("torch").lower():
            return []
    except metadata.PackageNotFoundError:
        return []
    directories = []
    for package, suffix in (("torch", "lib"), ("ctranslate2", ""),
                            ("_rocm_sdk_core", "bin"), ("_rocm_sdk_libraries_custom", "bin")):
        root = package_root(package)
        if root is not None:
            directory = root / suffix
            if directory.is_dir():
                directories.append(directory.resolve())
    return directories


def initialize_dll_search():
    """Keep each AddDllDirectory handle alive for the process; idempotent."""
    if sys.platform != "win32":
        return
    with _DLL_LOCK:
        _prepare_miopen_cache()
        _prepare_miopen_headers()
        for directory in dll_directories():
            key = str(directory)
            if key not in _DLL_HANDLES:
                _DLL_HANDLES[key] = os.add_dll_directory(key)


def _prepare_miopen_cache():
    """Avoid the pinned MIOpen SQLite failure with Unicode custom cache paths."""
    cache_path = os.environ.get("MIOPEN_CUSTOM_CACHE_DIR", "")
    if cache_path.isascii() or "MIOPEN_DISABLE_CACHE" in os.environ:
        return
    try:
        if metadata.version("torch") != "2.9.1+rocm7.2.1":
            return
    except metadata.PackageNotFoundError:
        return
    os.environ.setdefault("MIOPEN_DISABLE_CACHE", "1")


def _pinned_development_sdk():
    if sys.platform != "win32":
        return False
    try:
        return (
            metadata.version("torch") == "2.9.1+rocm7.2.1"
            and metadata.version("rocm-sdk-devel") == "7.2.1"
        )
    except metadata.PackageNotFoundError:
        return False


def _prepare_miopen_headers():
    """Point HIPRTC at already expanded headers without changing package files."""
    if "ROCM_PATH" in os.environ or not _pinned_development_sdk():
        return
    root = package_root("_rocm_sdk_devel")
    if root is not None:
        if (root / "include/rocrand/rocrand_xorwow.h").is_file():
            os.environ.setdefault("ROCM_PATH", str(root))


def initialize_development_sdk():
    """Expand pinned development files only during explicitly started setup."""
    if not _pinned_development_sdk():
        return
    root = Path(import_module("rocm_sdk._devel").get_devel_root())
    if not (root / "include/rocrand/rocrand_xorwow.h").is_file():
        raise RuntimeError("Pinned ROCm SDK expansion did not provide required headers")
    _prepare_miopen_headers()


def _ascii_physical_path(value, *, directory=False):
    """Check native compiler/cache paths without creating or expanding anything."""
    if not value or not str(value).isascii():
        return False
    path = Path(value)
    if not path.is_absolute() or str(path).startswith("\\\\"):
        return False
    try:
        for item in (*reversed(path.parents), path):
            try:
                info = item.lstat()
            except FileNotFoundError:
                continue
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT:
                return False
        resolved = path.resolve(strict=False)
        return (resolved == path and str(resolved).isascii()
                and (not directory or path.is_dir()))
    except (OSError, ValueError):
        return False


def miopen_profile_error():
    """Read-only path qualification; a Unicode SQLite workaround is not HIPRTC proof."""
    for name in ("ROCM_HOME", "HIP_PATH", "HSA_OVERRIDE_GFX_VERSION", "HIP_VISIBLE_DEVICES",
                 "CUDA_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES"):
        if name in os.environ:
            return f"Unqualified native override: {name}"
    allowed_miopen = {"MIOPEN_CUSTOM_CACHE_DIR", "MIOPEN_FIND_MODE"}
    if any(name.startswith("MIOPEN_") and name not in allowed_miopen for name in os.environ):
        return "Unqualified MIOpen override; disk-cache/ATen bypasses do not qualify this pipeline"
    if os.environ.get("MIOPEN_FIND_MODE", "FAST") != "FAST":
        return "Unqualified MIOpen search mode"
    paths = sysconfig.get_paths()
    sites = {Path(paths[key]) for key in ("purelib", "platlib") if paths.get(key)}
    roots = {package: package_root(package) for package in ("_rocm_sdk_core", "_rocm_sdk_devel")}
    for package, root in roots.items():
        if (root is None or root.name != package or root.parent not in sites
                or not _ascii_physical_path(root, directory=True)):
            return "MIOpen requires physical ASCII SDK roots managed by this interpreter"
    sdk = roots["_rocm_sdk_devel"]
    if "ROCM_PATH" in os.environ and os.environ["ROCM_PATH"] != str(sdk):
        return "ROCM_PATH must match the managed physical ASCII development SDK"
    headers = ("hip/hip_runtime.h", "hip/hip_runtime_api.h", "rocrand/rocrand.h",
               "rocrand/rocrand_xorwow.h", "miopen/miopen.h", "miopen/config.h", "rocprim/rocprim.hpp")
    for name in headers:
        header = sdk / "include" / name
        if not _ascii_physical_path(header) or not header.is_file():
            return "Required expanded HIP/MIOpen/rocRAND/rocPRIM headers are missing or redirected; rerun ROCm setup"
    temporary = [os.environ[name] for name in ("TMPDIR", "TEMP", "TMP") if name in os.environ]
    if not all(os.environ.get(name) for name in ("TEMP", "TMP")):
        return "MIOpen requires explicit compatible TEMP and TMP directories"
    if tempfile.tempdir is not None:
        temporary.append(os.fsdecode(tempfile.tempdir))
    if not temporary or not all(_ascii_physical_path(path, directory=True) for path in temporary):
        return "MIOpen requires existing physical ASCII temporary directories"
    cache_roots = [os.environ[name] for name in ("MIOPEN_CUSTOM_CACHE_DIR", "XDG_CACHE_HOME") if name in os.environ]
    if "MIOPEN_CUSTOM_CACHE_DIR" not in os.environ:
        cache_roots.append(Path.home())
        cache_roots.extend(os.environ[name] for name in ("USERPROFILE", "HOME", "LOCALAPPDATA", "APPDATA")
                           if name in os.environ)
    if not cache_roots or not all(_ascii_physical_path(path) for path in cache_roots):
        return "MIOpen requires physical ASCII cache roots; the Unicode SQLite workaround does not qualify HIPRTC"
    return None
