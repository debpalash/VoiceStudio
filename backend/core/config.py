import os
import re
import sys

def get_app_data_dir():
    custom_dir = os.environ.get("OMNIVOICE_DATA_DIR")
    if custom_dir:
        return custom_dir
        
    if sys.platform == "darwin":
        return os.path.expanduser("~/Library/Application Support/OmniVoice")
    elif sys.platform == "win32":
        return os.path.join(os.environ.get("APPDATA", ""), "OmniVoice")
    else:
        return os.path.expanduser("~/.omnivoice")


def _configured_hf_token_path():
    """Match Hub's token location without importing or refreshing credentials."""
    default_cache = os.path.join(os.path.expanduser("~"), ".cache")
    hf_home = os.environ.get("HF_HOME", os.path.join(os.environ.get("XDG_CACHE_HOME", default_cache), "huggingface"))
    return os.path.expandvars(os.path.expanduser(os.environ.get("HF_TOKEN_PATH", os.path.join(hf_home, "token"))))


# Snapshot recognized locations before automatic model-cache redirection.
# Explicit cache/token overrides restrict clearing to their selected location.
HF_CLI_TOKEN_PATHS = (_configured_hf_token_path(),)


def _ensure_short_hf_cache_on_windows():
    """Redirect HuggingFace cache to a short path on Windows.

    The default ``~/.cache/huggingface/hub/models--org--name/snapshots/<hash>/…``
    path regularly exceeds the 260-char MAX_PATH limit on NTFS, causing
    ``FileNotFoundError`` or truncated downloads on first install.  We shorten
    it to ``%LOCALAPPDATA%\\OmniVoice\\hf_cache`` (~40 chars) so even the
    deepest blob path stays well under the limit.

    Respects any explicit override the user already set via
    ``OMNIVOICE_CACHE_DIR``, ``HF_HOME``, or ``HF_HUB_CACHE``.
    """
    if sys.platform != "win32":
        return
    # Don't override if the user (or main.py's OMNIVOICE_CACHE_DIR block)
    # already pointed the cache somewhere specific.
    if os.environ.get("OMNIVOICE_CACHE_DIR") or os.environ.get("HF_HOME") or os.environ.get("HF_HUB_CACHE"):
        return
    local_app = os.environ.get("LOCALAPPDATA", "")
    if not local_app:
        return
    short_cache = os.path.join(local_app, "OmniVoice", "hf_cache")
    os.makedirs(short_cache, exist_ok=True)
    if "HF_TOKEN_PATH" not in os.environ:
        global HF_CLI_TOKEN_PATHS
        canonical = HF_CLI_TOKEN_PATHS[0]
        legacy = os.path.join(short_cache, "token")
        HF_CLI_TOKEN_PATHS = tuple(dict.fromkeys((canonical, legacy)))
        # Keep existing app-written logins usable without copying credentials.
        selected = canonical if os.path.exists(canonical) or not os.path.exists(legacy) else legacy
        os.environ.setdefault("HF_TOKEN_PATH", selected)
    os.environ["HF_HOME"] = short_cache
    os.environ["HF_HUB_CACHE"] = short_cache

_ensure_short_hf_cache_on_windows()


DATA_DIR = get_app_data_dir()


def get_engines_dir(data_dir=None):
    """Root of the managed engine installs (venvs, checkouts, audio.cpp runtime).

    Defaults to ``DATA_DIR/engines`` so existing installs keep working. Set
    ``OMNIVOICE_ENGINES_DIR`` to put these multi-GB trees on another drive
    (e.g. keep a small C: drive free) while voices and projects stay in
    ``DATA_DIR`` (#2683). Read at call time, so tests and late env restores work.
    """
    custom = (os.environ.get("OMNIVOICE_ENGINES_DIR") or "").strip()
    if custom:
        return os.path.abspath(os.path.expanduser(custom))
    return os.path.join(DATA_DIR if data_dir is None else data_dir, "engines")
VOICES_DIR = os.path.join(DATA_DIR, "voices")       # Reference audio for profiles
OUTPUTS_DIR = os.path.join(DATA_DIR, "outputs")      # Generated audio files
DUB_DIR = os.path.join(DATA_DIR, "dub_jobs")
DB_PATH = os.path.join(DATA_DIR, "omnivoice.db")


def dub_seg_path(job_id, seg_id):
    """Per-segment dub WAV path keyed by the STABLE segment id (not its list
    index), so partial regeneration reuses the right audio after a
    delete/merge/split shifts positions (#185). A numeric index `i` sanitises to
    `seg_{i}.wav`, i.e. the legacy index-based name, so old jobs keep resolving
    via the same helper.

    Both `job_id` and `seg_id` come from the request, so the result is sanitised
    (separators stripped) AND verified to stay inside DUB_DIR via realpath
    containment — raises ValueError on any attempt to escape the dub directory.
    """
    safe_job = re.sub(r"[^A-Za-z0-9._-]", "_", str(job_id))
    safe_seg = re.sub(r"[^A-Za-z0-9._-]", "_", str(seg_id))
    base = os.path.realpath(DUB_DIR)
    full = os.path.realpath(os.path.join(base, safe_job, f"seg_{safe_seg}.wav"))
    if full != base and not full.startswith(base + os.sep):
        raise ValueError(f"dub segment path escapes DUB_DIR: {job_id!r}/{seg_id!r}")
    return full
PREVIEW_DIR = os.path.join(DATA_DIR, "preview")
CRASH_LOG_PATH = os.path.join(DATA_DIR, "crash_log.txt")   # only written on unhandled exceptions
LOG_PATH = os.path.join(DATA_DIR, "omnivoice.log")          # rolling runtime log — what the Settings UI reads

IDLE_TIMEOUT_SECONDS = int(os.environ.get("OMNIVOICE_IDLE_TIMEOUT", "900"))
CPU_POOL_WORKERS = int(os.environ.get("OMNIVOICE_CPU_POOL", "0")) or min(8, (os.cpu_count() or 4))

def ensure_dirs():
    for d in [DATA_DIR, VOICES_DIR, OUTPUTS_DIR, DUB_DIR, PREVIEW_DIR]:
        os.makedirs(d, exist_ok=True)

ensure_dirs()

# Ensure ffmpeg is on PATH for Whisper and other subprocesses (mostly relevant for Mac/Linux)
if sys.platform != "win32":
    for _fpath in ["/opt/homebrew/bin", "/usr/local/bin"]:
        if _fpath not in os.environ.get("PATH", "") and os.path.exists(_fpath):
            os.environ["PATH"] = _fpath + os.pathsep + os.environ.get("PATH", "")
