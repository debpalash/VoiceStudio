"""Lifetime DLL search handles for actual native imports and isolated probes."""
import ctypes
import logging
import os
import sys
import sysconfig
from importlib import metadata
from pathlib import Path
from threading import RLock

logger = logging.getLogger(__name__)

_DLL_HANDLES = {}
_DLL_LOCK = RLock()
_PRELOADED = {}

#: Set to 0/false/off to keep the wheel's bundled HIP runtime (never preload the driver's).
DRIVER_HIP_ENV = "OMNIVOICE_ROCM_DRIVER_HIP"

#: Import package -> distribution that installs it.
_DISTRIBUTIONS = {
    "torch": "torch",
    "ctranslate2": "ctranslate2",
    "_rocm_sdk_core": "rocm-sdk-core",
    "_rocm_sdk_libraries_custom": "rocm-sdk-libraries-custom",
    "_rocm_sdk_libraries": "rocm-sdk-libraries",
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
        torch_version = metadata.version("torch").lower()
        if "rocm" not in torch_version:
            return []
    except metadata.PackageNotFoundError:
        return []
    # Old and new SDK packages may coexist after switching recipes. Never put
    # the other recipe's DLLs on the search path.
    libraries = "_rocm_sdk_libraries" if "+rocm10." in torch_version else "_rocm_sdk_libraries_custom"
    directories = []
    for package, suffix in (("torch", "lib"), ("ctranslate2", ""),
                            ("_rocm_sdk_core", "bin"), (libraries, "bin")):
        root = package_root(package)
        if root is not None:
            directory = root / suffix
            if directory.is_dir():
                directories.append(directory.resolve())
    return directories


def _file_version(path):
    """Windows file version of ``path`` as a tuple of ints, or None."""
    version = ctypes.windll.version
    size = version.GetFileVersionInfoSizeW(str(path), None)
    if not size:
        return None
    data = ctypes.create_string_buffer(size)
    if not version.GetFileVersionInfoW(str(path), 0, size, data):
        return None
    info = ctypes.c_void_p()
    length = ctypes.c_uint()
    if not version.VerQueryValueW(data, "\\", ctypes.byref(info), ctypes.byref(length)) or length.value < 52:
        return None
    # VS_FIXEDFILEINFO: dwFileVersionMS / dwFileVersionLS follow signature, struct version.
    fixed = (ctypes.c_uint32 * 13).from_address(info.value)
    ms, ls = fixed[4], fixed[5]
    return (ms >> 16, ms & 0xFFFF, ls >> 16, ls & 0xFFFF)


def driver_hip_choice(bundled_dir, system_dir=None, read_version=None):
    """Return (driver_dll_path or None, reason).

    The wheel's ``amdhip64_*.dll`` can crash during device enumeration on
    dGPU + Ryzen iGPU systems (AMD ROCm/TheRock#9181, issue #4200); the HIP
    runtime shipped with the Adrenalin driver in System32 is newer and works.
    Prefer it only when it has the same file name (same soname/major) and a
    strictly newer file version; otherwise keep the bundled runtime.
    """
    if os.environ.get(DRIVER_HIP_ENV, "").strip().lower() in ("0", "false", "off", "no"):
        return None, f"{DRIVER_HIP_ENV} disables the driver HIP runtime"
    read_version = read_version or _file_version
    system_dir = Path(system_dir or os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32"))
    bundled = sorted(Path(bundled_dir).glob("amdhip64*.dll"))
    if not bundled:
        return None, "no bundled HIP runtime found"
    bundled = bundled[0]
    driver = system_dir / bundled.name
    if not driver.is_file():
        return None, f"driver has no {bundled.name}"
    try:
        theirs, ours = read_version(driver), read_version(bundled)
    except Exception as exc:  # noqa: BLE001 - version probing must never block startup
        return None, f"could not read HIP runtime versions: {exc}"
    if not theirs or not ours:
        return None, "HIP runtime version unreadable"
    if theirs <= ours:
        return None, f"driver {bundled.name} {'.'.join(map(str, theirs))} is not newer than bundled {'.'.join(map(str, ours))}"
    return driver, f"driver {bundled.name} {'.'.join(map(str, theirs))} is newer than bundled {'.'.join(map(str, ours))}"


def preload_driver_hip(directories):
    """Load the driver's newer HIP runtime before Torch so it wins by module name.

    Fully automatic fallback: any refusal or load error keeps the wheel's
    bundled runtime, and the reason is logged. Idempotent.
    """
    if "hip" in _PRELOADED:
        return _PRELOADED["hip"]
    handle = None
    core = next((d for d in directories if d.parent.name == "_rocm_sdk_core"), None)
    if core is None:
        return None
    driver, reason = driver_hip_choice(core)
    if driver is None:
        logger.info("Windows ROCm: using bundled HIP runtime (%s)", reason)
    else:
        try:
            handle = ctypes.WinDLL(str(driver))
            logger.info("Windows ROCm: preloaded %s (%s)", driver, reason)
        except OSError as exc:
            logger.warning("Windows ROCm: driver HIP runtime load failed (%s); using bundled runtime", exc)
    _PRELOADED["hip"] = handle
    return handle


def initialize_dll_search():
    """Keep each AddDllDirectory handle alive for the process; idempotent."""
    if sys.platform != "win32":
        return
    with _DLL_LOCK:
        for directory in dll_directories():
            key = str(directory)
            if key not in _DLL_HANDLES:
                _DLL_HANDLES[key] = os.add_dll_directory(key)
        preload_driver_hip(dll_directories())
