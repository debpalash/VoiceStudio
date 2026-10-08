"""Media tools — ffmpeg / ffprobe / yt-dlp as an invisible internal concern.

Most users should never learn what ffmpeg is. This module makes the media
engine self-contained: it reports where each tool comes from, acquires a
bundled static build in the background when no tier of the resolution chain
(``services.ffmpeg_utils``) resolves, and gives power users explicit
control (custom path / system copy / restore bundled) through the
``/media-tools`` router — persisted via the same ``env.FFMPEG_PATH`` /
``env.FFPROBE_PATH`` prefs convention the Settings env writer already uses,
so there is exactly one override mechanism.

Bundled-binary source (decision record)
---------------------------------------
The gap: ``imageio-ffmpeg`` (already a locked dep) ships a static *ffmpeg*
inside its platform wheels but **no ffprobe**, so source installs without a
system ffmpeg lose ``/tools/probe``, Smart-Fit duration checks, and VFR
detection. Two options were audited:

(a) the ``static-ffmpeg`` pip package — ships BOTH binaries per platform via
    lazy download. **Rejected**: it downloads from a *mutable* URL
    (``.../ffmpeg_bins/raw/main/...`` — the branch tip, not a pinned
    release), performs **no checksum validation**, extracts into its own
    ``site-packages`` directory (read-only / non-existent in the frozen
    PyInstaller backend), and drags in ``requests``/``filelock``/``progress``
    plus a stdout spinner.

(b) fetch the same upstream static builds ourselves, pinned to an immutable
    commit. **Chosen**: we download the platform zip from
    ``github.com/zackees/ffmpeg_bins`` at a pinned commit SHA (immutable
    URL), verify size + SHA-256 against constants recorded from that
    commit's git-LFS pointers, extract only ffmpeg/ffprobe into a
    user-writable, update-surviving dir under ``DATA_DIR``, and trust a
    binary only after the existing ``_binary_runs`` ``-version`` probe.
    Stdlib-only (urllib honors HTTP(S)_PROXY), identical behavior on
    macOS (arm64 + x86_64), Windows x64, and Linux (x64 + arm64), and zero
    new Python dependencies.

yt-dlp updates (decision record)
--------------------------------
yt-dlp is an importable locked dep — never a user-installed requirement.
But site support rots faster than app releases, so Settings offers a
user-triggered "Update". A plain in-venv upgrade was audited and rejected:
the app venv is uv-managed (no pip module), and the updater's drift sync
(#1029/#1030, ``uv sync --frozen --inexact``) preserves only packages *not*
in the lockfile — yt-dlp IS locked, so an in-venv upgrade would be silently
reverted on the next app update, and the frozen build has no installer at
all. Instead we install the new wheel (pure-python, no required deps —
matching the plain ``yt-dlp`` spec pinned in pyproject) into an **overlay
directory** under ``DATA_DIR`` — SHA-256-verified against PyPI's own
metadata — and prepend it to ``sys.path`` at startup. It survives app
updates and drift syncs, works identically in source and frozen builds, and
"Restore tested version" is simply deleting the overlay: the locked wheel
underneath was never touched.
"""
from __future__ import annotations

import errno
import hashlib
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from collections.abc import Callable
import zipfile

from core.config import DATA_DIR
from core import prefs
from services.ffmpeg_utils import _binary_runs, _BINARY_OK, windows_tool_candidates as _windows_tool_candidates

logger = logging.getLogger("omnivoice.media_tools")

# ── Pinned bundled build ────────────────────────────────────────────────────
# Immutable commit of github.com/zackees/ffmpeg_bins (the upstream the
# static-ffmpeg pip package also consumes, but pinned + checksummed here).
# SHA-256 values are the git-LFS oids of the v8.0 platform zips at this
# commit, independently verified by downloading and hashing.
_FFBIN_REPO = "zackees/ffmpeg_bins"
_FFBIN_COMMIT = "df95abcb0ce6efff710dda5ef28a2f6f1dc21493"  # 2026-01-16
_FFBIN_TREE = "v8.0"

#: platform key → (sha256, size in bytes) of the zip at the pinned commit.
_FFBIN_SHA256 = {
    "darwin": ("70fd5b21cb37b6ea97c8b584cf76b3cc6a90179831c9c269811b9716c28605fb", 53079896),
    "darwin_arm64": ("b2da44a8169c4d09a97db996250690c3346f72e4795521d23d3dbb1e72421207", 41925556),
    "linux": ("ca75b05e887c7a97676632f673031875847be83daa9794298fed9cef8cac14ad", 142008975),
    "linux_arm64": ("e03efe471c03b999f10988d5db62ae3bd94837463291b3c7755528b100e97d6f", 131816005),
    "win32": ("92662c2241e93fe71b3f3a01e94a0b0dc8cfad726019f96b83bc109ce44c5d0b", 72065209),
}

_PYPI_YTDLP_URL = "https://pypi.org/pypi/yt-dlp/json"

_DOWNLOAD_TIMEOUT_S = 30  # per-read socket timeout; downloads stream in chunks
_CHUNK = 256 * 1024

#: tool → env keys honored by the resolution chain, in precedence order.
_ENV_KEYS = {
    "ffmpeg": ("FFMPEG_PATH",),
    "ffprobe": ("OMNIVOICE_FFPROBE_PATH", "FFPROBE_PATH"),
}
#: tool → the env key the *user override* is persisted under (prefs `env.<KEY>`).
_PREF_ENV_KEY = {"ffmpeg": "FFMPEG_PATH", "ffprobe": "FFPROBE_PATH"}

TOOLS = ("ffmpeg", "ffprobe")

# ── Background-operation state (poll via status()) ─────────────────────────

_lock = threading.Lock()
_ops: dict[str, dict] = {
    "acquire": {"state": "idle", "progress": 0.0, "error": None},
    "ytdlp_update": {"state": "idle", "progress": 0.0, "error": None, "version": None},
}
_version_cache: dict[str, str] = {}


def _set_op(op: str, **fields) -> None:
    with _lock:
        _ops[op].update(fields)


def _op_snapshot() -> dict:
    with _lock:
        return {k: dict(v) for k, v in _ops.items()}


# ── Platform / paths ────────────────────────────────────────────────────────

def _platform_key() -> str:
    import platform as _p
    is_arm = _p.machine().lower() in ("arm64", "aarch64")
    if sys.platform == "win32":
        return "win32"
    if sys.platform == "darwin":
        return "darwin_arm64" if is_arm else "darwin"
    if sys.platform.startswith("linux"):
        return "linux_arm64" if is_arm else "linux"
    return sys.platform


def media_tools_dir() -> str:
    """User-writable root for acquired binaries + the yt-dlp overlay.

    Lives in DATA_DIR so it survives app updates (the app bundle / venv are
    replaced wholesale on update; DATA_DIR is user state) and is writable in
    frozen installs.
    """
    return os.path.join(DATA_DIR, "media_tools")


def bundled_dir() -> str:
    # Versioned by the pin so a future pin bump lands in a fresh dir and
    # "Update" is a plain re-acquire — no in-place mutation of a live binary.
    return os.path.join(media_tools_dir(), f"ffbin-{_FFBIN_COMMIT[:12]}", _platform_key())


def _publish_bundled_on_path() -> None:
    """Make a newly validated bundle visible to bare-name subprocess calls."""
    directory = os.path.abspath(bundled_dir())
    current = os.environ.get("PATH", "")
    entries = current.split(os.pathsep) if current else []
    if os.path.normcase(directory) in {os.path.normcase(entry) for entry in entries if entry}:
        return
    os.environ["PATH"] = os.pathsep.join([directory, *entries])
    logger.info("Published the acquired media-tool directory on PATH")


def _exe(name: str) -> str:
    return f"{name}.exe" if sys.platform == "win32" else name


def bundled_tool_path(tool: str) -> str | None:
    """Path of an already-acquired bundled binary, or None. Never downloads."""
    p = os.path.join(bundled_dir(), _exe(tool))
    return p if os.path.isfile(p) else None


def _bundle_url() -> str:
    # github.com/<repo>/raw/<commit> redirects to the LFS media host and
    # serves the real zip (raw.githubusercontent.com would return the
    # 133-byte LFS pointer instead).
    return f"https://github.com/{_FFBIN_REPO}/raw/{_FFBIN_COMMIT}/{_FFBIN_TREE}/{_platform_key()}.zip"


def _expected_bundle() -> tuple[str, str, int]:
    """(url, sha256, size) for this platform. Raises on unsupported platform."""
    key = _platform_key()
    if key not in _FFBIN_SHA256:
        raise RuntimeError(f"no bundled media-engine build for platform '{key}'")
    sha, size = _FFBIN_SHA256[key]
    return _bundle_url(), sha, size


# ── Download helper ─────────────────────────────────────────────────────────

def _download(url: str, dest_path: str, expected_sha256: str,
              expected_size: int | None, op: str) -> None:
    """Stream *url* to *dest_path*, hashing on the fly; raise on mismatch.

    Progress is reported into ``_ops[op]["progress"]``. urllib honors the
    HTTP(S)_PROXY env vars, so restricted-network users' proxy settings apply.
    """
    import urllib.request

    if not url.startswith("https://"):
        raise ValueError("media-tools downloads must be https")
    req = urllib.request.Request(url, headers={"User-Agent": "VoiceStudio"})
    hasher = hashlib.sha256()
    done = 0
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
        total = expected_size or int(resp.headers.get("Content-Length") or 0)
        with open(dest_path, "wb") as f:
            while True:
                chunk = resp.read(_CHUNK)
                if not chunk:
                    break
                f.write(chunk)
                hasher.update(chunk)
                done += len(chunk)
                if total:
                    _set_op(op, progress=min(done / total, 1.0))
    digest = hasher.hexdigest()
    if expected_size is not None and done != expected_size:
        raise RuntimeError(f"download size mismatch: got {done}, expected {expected_size}")
    if digest != expected_sha256:
        raise RuntimeError("download checksum mismatch — refusing to install")


# ── Bundled acquisition ─────────────────────────────────────────────────────

def acquire_bundled(wait: bool = False) -> dict:
    """Fetch + verify + install the pinned static ffmpeg/ffprobe build.

    Idempotent: a no-op when the binaries are already present and runnable,
    or when an acquisition is already running. Runs in a daemon thread so it
    never blocks the caller (``wait=True`` is for tests/CLI use).
    Returns the op-state snapshot.
    """
    with _lock:
        if _ops["acquire"]["state"] == "running":
            return dict(_ops["acquire"])
        _ops["acquire"].update(state="running", progress=0.0, error=None)

    if all(bundled_tool_path(t) and _binary_runs(bundled_tool_path(t)) for t in TOOLS):
        # The bundle may have arrived after startup's one-time PATH publish
        # (first-run acquisition is asynchronous). Make it visible to pydub
        # and other dependencies that launch ffmpeg/ffprobe by bare name now,
        # without requiring a backend restart (#1677).
        _publish_bundled_on_path()
        _set_op("acquire", state="done", progress=1.0)
        return _op_snapshot()["acquire"]

    def _worker():
        try:
            _do_acquire()
            # Startup cannot publish binaries which do not exist yet. The
            # background worker must complete that second half atomically with
            # installation so the very next synthesis can use the tools.
            _publish_bundled_on_path()
            _set_op("acquire", state="done", progress=1.0, error=None)
            logger.info("media-tools: bundled ffmpeg/ffprobe installed at %s", bundled_dir())
        except Exception as e:
            logger.warning("media-tools: bundled acquisition failed: %s", e)
            _set_op("acquire", state="error", error=str(e)[:300])

    if wait:
        _worker()
    else:
        threading.Thread(target=_worker, name="media-tools-acquire", daemon=True).start()
    return _op_snapshot()["acquire"]


def _retry_tool_filesystem(operation: Callable[[], None]) -> None:
    """Allow bounded time for probe images / virus scanners to release files."""
    delays = (0.1, 0.25, 0.5, 1.0, 2.0)
    for attempt in range(len(delays) + 1):
        try:
            operation()
            return
        except OSError as exc:
            transient = (
                exc.errno in (errno.EACCES, errno.EPERM, errno.EBUSY, errno.ENOTEMPTY)
                or getattr(exc, "winerror", None) in (5, 32, 33)
            )
            if not transient or attempt == len(delays):
                raise
            time.sleep(delays[attempt])


def _tool_path_exists(path: str) -> bool:
    """Only missing paths are absent; permission failures must reach the UI."""
    try:
        os.stat(path)
        return True
    except (FileNotFoundError, NotADirectoryError):
        return False


def _install_staged_directory(staged: str, target: str) -> None:
    # Keep the working installation until publication succeeds. The backup lives
    # beside the target so both renames stay on the same filesystem.
    backup_root = tempfile.mkdtemp(prefix=".media-backup-", dir=os.path.dirname(target))
    backup = os.path.join(backup_root, "previous")
    keep_backup = False

    def remove_backup() -> None:
        shutil.rmtree(backup_root)

    try:
        if _tool_path_exists(target):
            _retry_tool_filesystem(lambda: os.replace(target, backup))
            keep_backup = True
        try:
            _retry_tool_filesystem(lambda: os.replace(staged, target))
        except OSError as publish_error:
            if keep_backup:
                try:
                    _retry_tool_filesystem(lambda: os.replace(backup, target))
                    keep_backup = False
                except OSError as rollback_error:
                    keep_backup = True
                    raise OSError(
                        f"{publish_error}; rollback failed: {rollback_error}. "
                        f"Previous installation preserved at {backup}"
                    ) from publish_error
            raise
        keep_backup = False
    finally:
        if not keep_backup:
            try:
                _retry_tool_filesystem(remove_backup)
            except OSError:
                # A locked old binary must not turn a successful update into a
                # reported failure, or mask the original publication error.
                logger.warning("media-tools: old backup retained: %s", os.path.basename(backup_root))


def _do_acquire() -> None:
    url, sha, size = _expected_bundle()
    target = bundled_dir()
    os.makedirs(os.path.dirname(target), exist_ok=True)

    with tempfile.TemporaryDirectory(dir=os.path.dirname(target)) as tmp:
        zip_path = os.path.join(tmp, "bundle.zip")
        _download(url, zip_path, sha, size, op="acquire")

        # Extract only the two binaries, flattened by basename — layout-agnostic
        # and immune to zip-slip (we never honor archive paths).
        wanted = {_exe(t): t for t in TOOLS}
        staged = os.path.join(tmp, "staged")
        os.makedirs(staged, exist_ok=True)
        found: dict[str, str] = {}
        with zipfile.ZipFile(zip_path) as zf:
            for member in zf.infolist():
                base = os.path.basename(member.filename)
                if base in wanted and not member.is_dir():
                    out = os.path.join(staged, base)
                    with zf.open(member) as src, open(out, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    # Owner-only rwx — the backend process is the sole consumer
                    # of these binaries (least privilege; py/overly-permissive-file).
                    os.chmod(out, 0o700)
                    found[base] = out
        missing = set(wanted) - set(found)
        if missing:
            raise RuntimeError(f"bundle is missing {sorted(missing)}")

        # Probe BEFORE trusting — a corrupt / wrong-arch binary must never
        # be installed (same contract as ffmpeg_utils._binary_runs at
        # resolution time, applied at install time).
        for base, path in found.items():
            _BINARY_OK.pop(path, None)
            if not _binary_runs(path):
                raise RuntimeError(f"downloaded {base} failed its -version probe")

        # Finalize: swap the staged dir into place.
        _install_staged_directory(staged, target)

    # Resolution caches may hold negative verdicts for the old paths.
    for t in TOOLS:
        p = os.path.join(target, _exe(t))
        _BINARY_OK.pop(p, None)
        _version_cache.pop(p, None)


# ── Status / origin classification ─────────────────────────────────────────

def _tool_version(path: str) -> str | None:
    cached = _version_cache.get(path)
    if cached:
        return cached
    try:
        out = subprocess.run(
            [path, "-version"], capture_output=True, text=True, timeout=10, check=False,
        ).stdout
        m = re.match(r"^(?:ffmpeg|ffprobe) version (\S+)", out or "")
        if m:
            _version_cache[path] = m.group(1)
            return m.group(1)
    except Exception as e:
        logger.debug("version probe failed for %s: %s", os.path.basename(path), e)
    return None


def _imageio_pkg_dir() -> str | None:
    try:
        import imageio_ffmpeg
        return os.path.dirname(os.path.abspath(imageio_ffmpeg.__file__))
    except Exception:
        return None


def _classify_origin(tool: str, path: str) -> str:
    """sidecar | bundled | system | custom — where the resolved binary lives."""
    rp = os.path.realpath(path)
    for root in filter(None, (media_tools_dir(), _imageio_pkg_dir())):
        if rp.startswith(os.path.realpath(root) + os.sep):
            return "bundled"
    for key in _ENV_KEYS[tool]:
        v = os.environ.get(key)
        if not v:
            continue
        if v == path or os.path.realpath(v) == rp or shutil.which(v) == path:
            # The same env var serves two masters: the Tauri sidecar injects
            # it at spawn; a user override persists it via prefs `env.<KEY>`.
            return "custom" if prefs.get(f"env.{key}") else "sidecar"
    return "system"


def _resolve(tool: str) -> str | None:
    from services import ffmpeg_utils
    if tool == "ffmpeg":
        return ffmpeg_utils.find_ffmpeg()
    return ffmpeg_utils.find_ffprobe()


def _ytdlp_status() -> dict:
    """yt-dlp is a python module, not a binary — status reads its version
    without paying the full package import."""
    info: dict = {"tool": "yt-dlp", "ok": False, "path": None, "version": None,
                  "origin": "bundled", "overlay_version": None,
                  "baseline_version": prefs.get("media_tools.ytdlp_baseline")}
    try:
        import importlib.util
        spec = importlib.util.find_spec("yt_dlp")
        origin = getattr(spec, "origin", None)
        if origin:
            pkg_dir = os.path.dirname(origin)
            info["path"] = pkg_dir
            info["ok"] = True
            info["version"] = _read_ytdlp_version(pkg_dir)
            if os.path.realpath(pkg_dir).startswith(
                    os.path.realpath(_ytdlp_overlay_dir()) + os.sep):
                info["origin"] = "custom"
    except Exception as e:
        logger.debug("yt_dlp spec lookup failed: %s", e)
    ov = _read_ytdlp_version(os.path.join(_ytdlp_overlay_dir(), "yt_dlp"))
    info["overlay_version"] = ov
    return info


def _read_ytdlp_version(pkg_dir: str) -> str | None:
    try:
        with open(os.path.join(pkg_dir, "version.py"), encoding="utf-8") as f:
            m = re.search(r"__version__\s*=\s*['\"]([^'\"]+)['\"]", f.read())
        return m.group(1) if m else None
    except OSError:
        return None


def status() -> dict:
    """Full media-tools report: per-tool {ok, path, version, origin} + op states."""
    tools = {}
    for tool in TOOLS:
        path = _resolve(tool)
        tools[tool] = {
            "tool": tool,
            "ok": bool(path),
            "path": path,
            "version": _tool_version(path) if path else None,
            "origin": _classify_origin(tool, path) if path else None,
        }
    tools["ytdlp"] = _ytdlp_status()
    ops = _op_snapshot()
    return {
        "ready": tools["ffmpeg"]["ok"] and tools["ffprobe"]["ok"],
        "tools": tools,
        "ops": ops,
        "platform_key": _platform_key(),
    }


def summary(auto_acquire: bool = False) -> dict:
    """Small preflight-embeddable verdict. With ``auto_acquire``, kicks off
    the bundled download in the background when nothing resolves (first-run
    self-heal) — but never re-fires after a failed attempt (the wizard's
    failure card owns the Retry)."""
    st = status()
    op = st["ops"]["acquire"]
    if auto_acquire and not st["ready"] and op["state"] == "idle":
        op = acquire_bundled()
    return {
        "ready": st["ready"],
        "acquire": {"state": op["state"], "progress": op["progress"], "error": op["error"]},
    }


# ── User overrides (persisted via the existing env-prefs convention) ───────

def _validate_binary_path(path: str) -> None:
    # Same defense-in-depth as /system/set-env: no control chars, must be an
    # existing file, and must actually run before we trust it.
    if any(ord(c) < 0x20 or ord(c) == 0x7F for c in path):
        raise ValueError("Invalid path: control characters are not allowed")
    if not os.path.isfile(path):
        raise ValueError(f"File not found: {path}")
    _BINARY_OK.pop(path, None)
    if not _binary_runs(path):
        raise ValueError(
            "That file exists but does not run as a media tool "
            "(its `-version` probe failed) — wrong architecture or not executable."
        )


def set_custom_path(tool: str, path: str) -> dict:
    """Pin *tool* to an explicit binary. Persists via prefs `env.<KEY>` —
    the exact mechanism /system/set-env uses, so there is one override store."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool '{tool}'")
    path = path.strip()
    _validate_binary_path(path)
    key = _PREF_ENV_KEY[tool]
    os.environ[key] = path
    prefs.set_(f"env.{key}", path)
    _version_cache.pop(path, None)
    logger.info("media-tools: %s pinned to user path (origin=%s)",
                tool, _classify_origin(tool, path))
    return status()["tools"][tool]


def use_system(tool: str) -> dict:
    """Auto-detect a system-installed copy and pin it."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool '{tool}'")
    candidate = _detect_system(tool)
    if not candidate:
        raise LookupError(
            f"No system {tool} found on PATH or in the usual install locations."
        )
    return set_custom_path(tool, candidate)


def _detect_system(tool: str) -> str | None:
    roots = [r for r in (media_tools_dir(), _imageio_pkg_dir()) if r]

    def _is_bundled(p: str) -> bool:
        rp = os.path.realpath(p)
        return any(rp.startswith(os.path.realpath(r) + os.sep) for r in roots)

    candidates = [
        f"/opt/homebrew/bin/{tool}",
        f"/usr/local/bin/{tool}",
        f"/usr/bin/{tool}",
        *_windows_tool_candidates(tool),
        tool,
    ]
    for c in candidates:
        resolved = shutil.which(c)
        if resolved and not _is_bundled(resolved) and _binary_runs(resolved):
            return resolved
    return None


def restore_bundled(tool: str) -> dict:
    """Clear the user override so the chain resolves sidecar → bundled →
    system again; kick acquisition if no bundled build is present. Always safe."""
    if tool not in TOOLS:
        raise ValueError(f"unknown tool '{tool}'")
    for key in _ENV_KEYS[tool]:
        if prefs.get(f"env.{key}"):
            prefs.delete(f"env.{key}")
            os.environ.pop(key, None)
    _version_cache.clear()
    if not (bundled_tool_path(tool) and _binary_runs(bundled_tool_path(tool))):
        # No local bundled build to fall back to (imageio may still cover
        # ffmpeg) — fetch ours in the background so the revert lands somewhere.
        if not _resolve(tool):
            acquire_bundled()
    return status()["tools"][tool]


# ── yt-dlp overlay ──────────────────────────────────────────────────────────

def _ytdlp_overlay_dir() -> str:
    return os.path.join(media_tools_dir(), "ytdlp_overlay")


def activate_ytdlp_overlay() -> bool:
    """Prepend the user-updated yt-dlp overlay to sys.path. Called once at
    backend startup, before anything imports yt_dlp."""
    overlay = _ytdlp_overlay_dir()
    if os.path.isdir(os.path.join(overlay, "yt_dlp")) and overlay not in sys.path:
        sys.path.insert(0, overlay)
        logger.info("media-tools: yt-dlp overlay active (%s)",
                    _read_ytdlp_version(os.path.join(overlay, "yt_dlp")) or "?")
        return True
    return False


def _fetch_pypi_ytdlp() -> tuple[str, str, str]:
    """(version, wheel_url, sha256) of the latest yt-dlp wheel on PyPI."""
    import json
    import urllib.request
    req = urllib.request.Request(_PYPI_YTDLP_URL, headers={"User-Agent": "VoiceStudio"})
    with urllib.request.urlopen(req, timeout=_DOWNLOAD_TIMEOUT_S) as resp:
        meta = json.load(resp)
    version = meta["info"]["version"]
    for artifact in meta.get("urls", []):
        if artifact.get("packagetype") == "bdist_wheel" and \
                artifact["filename"].endswith("py3-none-any.whl"):
            return version, artifact["url"], artifact["digests"]["sha256"]
    raise RuntimeError(f"no universal wheel found for yt-dlp {version}")


def update_ytdlp(wait: bool = False) -> dict:
    """Install the newest yt-dlp into the overlay dir (background thread).

    The wheel is verified against PyPI's own sha256 digest before a single
    byte lands in the overlay; the swap is atomic (staged dir + os.replace).
    Takes effect on the next backend start (the running process already
    imported the old module) — the UI shows the restart affordance.
    """
    with _lock:
        if _ops["ytdlp_update"]["state"] == "running":
            return dict(_ops["ytdlp_update"])
        _ops["ytdlp_update"].update(state="running", progress=0.0, error=None, version=None)

    def _worker():
        try:
            version = _do_update_ytdlp()
            _set_op("ytdlp_update", state="done", progress=1.0, version=version)
            logger.info("media-tools: yt-dlp overlay updated to %s", version)
        except Exception as e:
            logger.warning("media-tools: yt-dlp update failed: %s", e)
            _set_op("ytdlp_update", state="error", error=str(e)[:300])

    if wait:
        _worker()
    else:
        threading.Thread(target=_worker, name="media-tools-ytdlp", daemon=True).start()
    return _op_snapshot()["ytdlp_update"]


def _do_update_ytdlp() -> str:
    version, url, sha = _fetch_pypi_ytdlp()

    # Record the locked ("tested") version once, before the first overlay
    # ever activates — that's what "Restore tested version" reverts to.
    if prefs.get("media_tools.ytdlp_baseline") is None:
        current = _ytdlp_status()
        if current["origin"] == "bundled" and current["version"]:
            prefs.set_("media_tools.ytdlp_baseline", current["version"])

    overlay = _ytdlp_overlay_dir()
    os.makedirs(media_tools_dir(), exist_ok=True)
    with tempfile.TemporaryDirectory(dir=media_tools_dir()) as tmp:
        whl = os.path.join(tmp, "yt_dlp.whl")
        _download(url, whl, sha, None, op="ytdlp_update")
        staged = os.path.join(tmp, "staged")
        with zipfile.ZipFile(whl) as zf:
            for member in zf.infolist():
                name = member.filename
                # Only the package itself; wheels carry no absolute paths but
                # guard against traversal anyway.
                if not name.startswith("yt_dlp/") or ".." in name:
                    continue
                zf.extract(member, staged)
        got = _read_ytdlp_version(os.path.join(staged, "yt_dlp"))
        if not got:
            raise RuntimeError("downloaded wheel has no readable yt_dlp version")
        _install_staged_directory(staged, overlay)
    return version


_GUARDED_YTDLP_BOOTSTRAP = (
    "import sys; sys.path.insert(0, sys.argv[1]); "
    "from core.url_safety import run_guarded_ytdlp; "
    "sys.exit(run_guarded_ytdlp(sys.argv[2:]))"
)


def guarded_ytdlp_invocation() -> "tuple[list[str], dict[str, str] | None]":
    """Like :func:`ytdlp_invocation`, with the URL-import policy enforced.

    The subprocess refuses connections to private-network addresses (unless
    the user opted in), downloads only through yt-dlp's native downloaders and
    refuses live streams — see ``core.url_safety``. Use it for every yt-dlp run whose input
    comes from a caller. A build that cannot re-invoke the interpreter falls
    back to the plain CLI; callers still validate the URL up front.
    """
    argv, env = ytdlp_invocation()
    if argv[:3] != [sys.executable, "-m", "yt_dlp"]:
        return argv, env
    backend_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return [sys.executable, "-c", _GUARDED_YTDLP_BOOTSTRAP, backend_dir], env


def ytdlp_invocation() -> "tuple[list[str], dict[str, str] | None]":
    """(argv prefix, env-or-None) for running the yt-dlp CLI.

    Prefers ``[sys.executable, -m, yt_dlp]`` so the CLI always matches the
    module the app ships (or the user's overlay — propagated via PYTHONPATH),
    with no PATH requirement: yt-dlp is never something the user installs.
    Frozen builds can't re-invoke an interpreter, so they keep the historical
    PATH lookup as a last resort.
    """
    if not getattr(sys, "frozen", False):
        try:
            import importlib.util
            if importlib.util.find_spec("yt_dlp") is not None:
                env = None
                overlay = _ytdlp_overlay_dir()
                if os.path.isdir(os.path.join(overlay, "yt_dlp")):
                    env = dict(os.environ)
                    env["PYTHONPATH"] = overlay + os.pathsep + env.get("PYTHONPATH", "")
                return [sys.executable, "-m", "yt_dlp"], env
        except Exception as e:
            logger.debug("yt_dlp module CLI unavailable: %s", e)
    exe = shutil.which("yt-dlp")
    return ([exe] if exe else ["yt-dlp"]), None


def restore_ytdlp() -> dict:
    """Delete the overlay — the locked, tested yt-dlp underneath takes over on
    next start. Always safe: the locked install was never modified."""
    overlay = _ytdlp_overlay_dir()
    if _tool_path_exists(overlay):
        _retry_tool_filesystem(lambda: shutil.rmtree(overlay))
    _set_op("ytdlp_update", state="idle", progress=0.0, error=None, version=None)
    return _ytdlp_status()
