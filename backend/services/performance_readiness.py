"""Package inventory for presets; actual engine imports remain load-time checks."""
from contextvars import ContextVar
from functools import wraps
from importlib import metadata, util
from pathlib import Path

_INVENTORY = ContextVar("performance_inventory_only", default=False)


def inventory_only(function):
    """Limit nested availability queries to metadata during one inventory call."""
    @wraps(function)
    def wrapped(*args, **kwargs):
        """Restore the caller's probe mode even when inventory fails."""
        token = _INVENTORY.set(True)
        try:
            return function(*args, **kwargs)
        finally:
            _INVENTORY.reset(token)
    return wrapped


def inventory_active():
    """Whether this execution context is collecting installed model choices."""
    return _INVENTORY.get()


def package_available(name):
    """Top-level lookup never executes a model package's __init__."""
    try:
        return util.find_spec(name.split(".")[0]) is not None
    except (ImportError, ValueError):
        try:
            metadata.version(name)
            return True
        except metadata.PackageNotFoundError:
            return False


def tts_installed(engine):
    """Check the package for a planner-managed TTS engine without importing it."""
    package = "omnivoice" if engine.startswith("omnivoice") else "kittentts" if engine == "kittentts" else None
    return bool(package and package_available(package))


def asr_installed(cls, broken=()):
    """Honor known failures and pipeline gates using package inventory alone."""
    engine = cls.id
    if engine in broken:
        return False
    packages = {"mlx-whisper": ("mlx_whisper",), "pytorch-whisper": ("transformers",),
                "whisperx": ("whisperx", "ctranslate2"),
                "faster-whisper": ("faster_whisper", "ctranslate2"),
                "faster-whisper-isolated": ("faster_whisper", "ctranslate2")}
    if engine not in packages or not all(package_available(name) for name in packages[engine]):
        return False
    if engine == "whisperx":
        from core.windows_rocm.pipelines import whisperx_error

        if whisperx_error():
            return False
    if engine == "faster-whisper-isolated":
        return (Path(__file__).parents[1] / "engines/_asr_sidecar/main.py").is_file()
    return True
