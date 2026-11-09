"""Lifetime DLL search handles for actual native imports and isolated probes."""
import os
import sys
import sysconfig
from importlib import metadata
from pathlib import Path
from threading import RLock

_DLL_HANDLES = {}
_DLL_LOCK = RLock()

#: Import package -> distribution that installs it.
_DISTRIBUTIONS = {
    "torch": "torch",
    "ctranslate2": "ctranslate2",
    "_rocm_sdk_core": "rocm-sdk-core",
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
        for directory in dll_directories():
            key = str(directory)
            if key not in _DLL_HANDLES:
                _DLL_HANDLES[key] = os.add_dll_directory(key)
