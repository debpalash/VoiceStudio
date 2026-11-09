"""Offline, process-contained runtime checks before preset selections are saved."""
from concurrent.futures import Future
import json
import os
from importlib import metadata
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import time

from core.contained_subprocess import spawn_owned

_BACKEND = Path(__file__).resolve().parents[1]
_TIMEOUT = 120
_MANAGED = frozenset({"asr", "tts", "translation", "dictation"})
_AUTO_ASR = frozenset({"mlx-whisper", "whisperx", "faster-whisper", "pytorch-whisper"})
_CACHE = {}
_INFLIGHT = {}
_CACHE_LOCK = threading.Lock()
_CACHE_SECONDS = 30
_PACKAGES = ("torch", "ctranslate2", "faster-whisper", "whisperx", "transformers",
             "mlx-whisper", "sherpa-onnx", "omnivoice", "kittentts", "argostranslate",
             "rocm-sdk-core", "rocm-sdk-libraries", "rocm-sdk-libraries-custom")
_DEVICE_ENV = ("OMNIVOICE_DEVICE", "OMNIVOICE_TORCH_VARIANT", "OMNIVOICE_FORCE_CUDA",
               "CUDA_VISIBLE_DEVICES", "HIP_VISIBLE_DEVICES", "ROCR_VISIBLE_DEVICES",
               "HSA_OVERRIDE_GFX_VERSION", "HSA_ENABLE_DXG_DETECTION", "ROCM_PATH",
               "HIP_PATH", "PATH", "HF_HUB_CACHE", "HF_HOME", "OMNIVOICE_DATA_DIR")


def _identity(family, engine, broken):
    """Invalidate cached results when device, interpreter or installed facts change."""
    from core import prefs

    packages = []
    for name in _PACKAGES:
        try:
            dist = metadata.distribution(name)
            root = Path(dist.locate_file(""))
            packages.append((name, dist.version, str(root), root.stat().st_mtime_ns))
        except (metadata.PackageNotFoundError, OSError):
            packages.append((name, None))
    try:
        path = Path(prefs._PREFS_PATH)
        stat = path.stat()
        preference_file = (str(path), stat.st_mtime_ns, stat.st_size)
    except OSError:
        preference_file = (prefs._PREFS_PATH, None)
    return (family, engine, sys.executable, sys.prefix, sys.platform, str(_BACKEND),
            tuple(packages), tuple((key, os.environ.get(key)) for key in _DEVICE_ENV),
            str(prefs.get("compute_device", "auto")), preference_file, broken)


def _query(family, engine):
    """Return a strictly matched child result; crashes and timeouts fail closed."""
    asr = sys.modules.get("services.asr_backend")
    broken = tuple(sorted(getattr(asr, "_DEEP_IMPORT_BROKEN", {})))
    key = _identity(family, engine, broken)
    with _CACHE_LOCK:
        cached = _CACHE.get(key)
        if cached and time.monotonic() - cached[0] < _CACHE_SECONDS:
            return dict(cached[1]) if cached[1] is not None else None
        flight = _INFLIGHT.get(key)
        owner = flight is None
        if owner:
            flight = _INFLIGHT[key] = Future()
    if not owner:
        data = flight.result()
        return dict(data) if data is not None else None
    try:
        data = _run_query(family, engine, broken)
        with _CACHE_LOCK:
            if len(_CACHE) >= 32:
                _CACHE.clear()
            _CACHE[key] = (time.monotonic(), dict(data) if data is not None else None)
        flight.set_result(dict(data) if data is not None else None)
        return data
    except BaseException as error:
        flight.set_exception(error)
        raise
    finally:
        with _CACHE_LOCK:
            _INFLIGHT.pop(key, None)


def _run_query(family, engine, broken):
    """Run one owned child and preserve negative results and transport failures."""
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1",
               HF_DATASETS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    with tempfile.TemporaryDirectory(prefix="voicestudio-readiness-") as directory:
        result = Path(directory) / "result.json"
        process = None
        try:
            process = spawn_owned(
                [sys.executable, str(_BACKEND / "services/_performance_probe.py"),
                 family, engine, json.dumps(broken), str(result)], cwd=str(_BACKEND), env=env,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL)
            if process.wait(timeout=_TIMEOUT) != 0 or result.stat().st_size > 4096:
                return None
            data = json.loads(result.read_text(encoding="utf-8"))
            if (not isinstance(data, dict) or type(data.get("schema")) is not int or data["schema"] != 1
                    or data.get("family") != family or data.get("requested") != engine
                    or type(data.get("ready")) is not bool
                    or not isinstance(data.get("engine"), str)):
                return None
            if data["engine"] != engine and not (engine == "auto" and data["engine"] in _AUTO_ASR):
                return None
            return data
        except subprocess.TimeoutExpired:
            if process is not None:
                try:
                    process.kill()
                    process.wait(timeout=5)
                except (OSError, subprocess.SubprocessError):
                    # Cleanup may race child exit; the timed-out query still fails closed.
                    pass
            return None
        except (OSError, ValueError, subprocess.SubprocessError):
            return None


def isolated_asr_import_ready(engine="faster-whisper"):
    """Prove an ASR import in a child without re-entering backend availability."""
    result = _query("asr-import", engine)
    return bool(result and result["ready"] and result["engine"] == engine)


def selection_ready(family, engine):
    """Check newly managed selections without importing their runtimes here."""
    if family not in _MANAGED:
        return True
    result = _query(family, engine)
    return bool(result and result["ready"] and result["engine"] == engine)


def active_asr_selection():
    """Preserve explicit choices and resolve automatic selection in a child."""
    from core import prefs

    explicit = os.environ.get("OMNIVOICE_ASR_BACKEND") or prefs.get("asr_backend")
    if explicit:
        return "pytorch-whisper" if explicit == "omnivoice" else explicit
    result = _query("asr", "auto")
    return result["engine"] if result and result["ready"] and result["engine"] in _AUTO_ASR else "auto"
