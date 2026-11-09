"""Point the bundled espeak-ng at a data directory it can actually open.

#3367: KittenTTS (and misaki's English fallback) phonemize through
``phonemizer`` + the ``espeakng_loader`` wheel. misaki hands espeak the
wheel's own ``espeak-ng-data`` path, but espeak-ng copies that path into a
fixed-size buffer (``N_PATH_HOME``: 230 bytes on Windows, 160 elsewhere) and
opens it with the narrow C runtime. A path that is too long, or that the
Windows ANSI code page cannot represent (a non-ASCII user name), is not seen
as a directory, so espeak silently falls back to the path baked in when the
wheel was built (``D:/a/espeakng-loader/...``). It then cannot read
``phontab`` and ``espeak_Initialize`` calls ``exit(1)``: no Python exception,
the whole backend dies mid-request.

``ensure_espeak_data_path()`` asks the library itself whether it can load
each candidate: ``espeak_ng_InitializePath`` + ``espeak_ng_Initialize`` load
the phoneme data and *return* a status (only the legacy ``espeak_Initialize``
that phonemizer calls turns a failure into ``exit(1)``), and ``espeak_Info``
confirms espeak kept the candidate rather than its build-time fallback. Each
candidate is probed in the exact form phonemizer passes on, which is
``Path(path).resolve()`` (that also expands Windows 8.3 short names and
symlinks, so neither can stand in for a long path). It uses the first
candidate espeak accepts: the wheel's own directory, then a copy in a short
cache directory. When none works it raises ``EspeakDataError``, an ordinary
engine-load failure, before phonemizer can initialize espeak and take the
process down.

Leaf module: stdlib only at import time.
"""
from __future__ import annotations

import ctypes
import hashlib
import logging
import os
import shutil
import stat
import sys
import tempfile
import threading
from pathlib import Path

logger = logging.getLogger("omnivoice.espeak")

_LOCK = threading.Lock()
_RESOLVED: str | None = None


class EspeakDataError(RuntimeError):
    """espeak-ng cannot open any copy of its data directory."""


def _same_path(a: str, b: str) -> bool:
    def norm(p: str) -> str:
        return os.path.normcase(os.path.normpath(p.rstrip("/\\")))

    return norm(a) == norm(b)


_ENS_OK = 0


def espeak_resolves(library: str, candidate: str) -> bool | None:
    """Whether espeak-ng can load its data from ``candidate``.

    ``True`` only when ``espeak_ng_Initialize`` loads the phoneme data from
    that directory with ``ENS_OK`` and espeak reports it as its data path, so
    a missing, unreadable or corrupt ``phontab``/``phondata`` is rejected here
    instead of making phonemizer's ``espeak_Initialize`` exit the process.
    ``None`` means the library could not be asked (missing symbol, load
    failure); callers treat that as "unknown" and let phonemizer report
    its own, catchable, load error.
    """
    try:
        lib = ctypes.CDLL(library)
        init_path = lib.espeak_ng_InitializePath
        initialize = lib.espeak_ng_Initialize
        info = lib.espeak_Info
    except (OSError, AttributeError):
        return None
    init_path.argtypes = [ctypes.c_char_p]
    init_path.restype = None
    initialize.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
    initialize.restype = ctypes.c_int
    info.argtypes = [ctypes.POINTER(ctypes.c_char_p)]
    info.restype = ctypes.c_char_p
    init_path(str(candidate).encode("utf-8"))
    context = ctypes.c_void_p()
    status = initialize(ctypes.byref(context))
    clear = getattr(lib, "espeak_ng_ClearErrorContext", None)
    if clear is not None and context.value:
        clear.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        clear.restype = None
        clear(ctypes.byref(context))
    if status != _ENS_OK:
        return False
    resolved = ctypes.c_char_p()
    info(ctypes.byref(resolved))
    if not resolved.value:
        return False
    return _same_path(resolved.value.decode("utf-8", "replace"), str(candidate))


def _cache_roots() -> list[Path]:
    roots: list[str | None] = [os.environ.get("OMNIVOICE_ESPEAK_DATA_CACHE")]
    if sys.platform == "win32":
        roots.append(os.environ.get("PROGRAMDATA"))
        roots.append(os.environ.get("PUBLIC"))
    else:
        roots.append(os.environ.get("XDG_CACHE_HOME") or str(Path.home() / ".cache"))
    roots.append(tempfile.gettempdir())
    if sys.platform != "win32":
        roots.append("/tmp")
    out: list[Path] = []
    for root in roots:
        if root and Path(root) not in out:
            out.append(Path(root))
    return out


def _tree_digest(root: Path) -> str | None:
    """Content hash of every file under ``root`` (relative path + bytes)."""
    h = hashlib.sha256()
    try:
        for path in sorted(p for p in root.rglob("*") if p.is_file()):
            h.update(path.relative_to(root).as_posix().encode("utf-8", "replace"))
            h.update(b"\0")
            with open(path, "rb") as fh:
                for chunk in iter(lambda: fh.read(1 << 20), b""):
                    h.update(chunk)
            h.update(b"\0")
    except OSError:
        return None
    return h.hexdigest()


def _windows_owned_by_me(path: Path) -> bool | None:
    """Whether the current user owns ``path`` (Windows); ``None`` if unknown."""
    try:
        import ctypes.wintypes

        wintypes = ctypes.wintypes

        advapi32 = ctypes.WinDLL("advapi32", use_last_error=True)  # type: ignore[attr-defined]
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)  # type: ignore[attr-defined]
        LPVOID, PVOID_P = ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)
        advapi32.GetNamedSecurityInfoW.argtypes = [
            wintypes.LPCWSTR, ctypes.c_int, wintypes.DWORD,
            PVOID_P, PVOID_P, PVOID_P, PVOID_P, PVOID_P,
        ]
        advapi32.GetNamedSecurityInfoW.restype = wintypes.DWORD
        kernel32.GetCurrentProcess.argtypes = []
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        advapi32.OpenProcessToken.argtypes = [
            wintypes.HANDLE, wintypes.DWORD, ctypes.POINTER(wintypes.HANDLE),
        ]
        advapi32.OpenProcessToken.restype = wintypes.BOOL
        advapi32.GetTokenInformation.argtypes = [
            wintypes.HANDLE, ctypes.c_int, LPVOID, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]
        advapi32.GetTokenInformation.restype = wintypes.BOOL
        advapi32.EqualSid.argtypes = [LPVOID, LPVOID]
        advapi32.EqualSid.restype = wintypes.BOOL
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel32.CloseHandle.restype = wintypes.BOOL
        kernel32.LocalFree.argtypes = [LPVOID]
        kernel32.LocalFree.restype = LPVOID
        owner = ctypes.c_void_p()
        descriptor = ctypes.c_void_p()
        # SE_FILE_OBJECT = 1, OWNER_SECURITY_INFORMATION = 1
        if advapi32.GetNamedSecurityInfoW(
            str(path), 1, 1, ctypes.byref(owner), None, None, None, ctypes.byref(descriptor),
        ) != 0:
            return None
        try:
            token = wintypes.HANDLE()
            # TOKEN_QUERY = 0x0008
            if not advapi32.OpenProcessToken(
                kernel32.GetCurrentProcess(), 0x0008, ctypes.byref(token)
            ):
                return None
            try:
                size = wintypes.DWORD()
                # TokenUser = 1; the first call reports the buffer size.
                advapi32.GetTokenInformation(token, 1, None, 0, ctypes.byref(size))
                buf = ctypes.create_string_buffer(size.value)
                if not size.value or not advapi32.GetTokenInformation(
                    token, 1, buf, size, ctypes.byref(size)
                ):
                    return None
                # TOKEN_USER starts with SID_AND_ATTRIBUTES, whose first field is the SID pointer.
                me = ctypes.c_void_p.from_buffer(buf).value
                return bool(advapi32.EqualSid(owner, me))
            finally:
                kernel32.CloseHandle(token)
        finally:
            kernel32.LocalFree(descriptor)
    except Exception:  # pragma: no cover - best effort
        return None


def _private_dir(path: Path) -> bool:
    """Whether ``path`` is a directory this user owns and others cannot write.

    The cache roots include shared folders (``/tmp``, ``%PROGRAMDATA%``,
    ``%PUBLIC%``), where another local user could pre-create the predictable
    cache directory. POSIX checks the owner and group/other write bits;
    Windows checks the owner (its inherited ACLs let other users add files
    but not change ours), falling back to the content check in
    ``_cached_copy`` when the owner cannot be read.
    """
    if sys.platform == "win32":
        return path.is_dir() and _windows_owned_by_me(path) is not False
    try:
        st = path.lstat()
    except OSError:
        return False
    return (
        stat.S_ISDIR(st.st_mode)
        and st.st_uid == os.getuid()
        and not st.st_mode & (stat.S_IWGRP | stat.S_IWOTH)
    )


def _cached_copy(source: Path, root: Path) -> Path | None:
    """A copy of ``source`` under ``root``; reused only while it matches the source.

    Returns ``None`` (try the next root) when the cache directory is not
    private to this user or an existing copy differs from the source.
    """
    phontab = source / "phontab"
    try:
        stamp = f"{source}|{phontab.stat().st_size}|{phontab.stat().st_mtime_ns}"
    except OSError:
        return None
    digest = hashlib.sha256(stamp.encode("utf-8", "replace")).hexdigest()[:10]
    parent = root / "voicestudio-espeak"
    target = parent / digest
    try:
        parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    except OSError as exc:
        logger.debug("espeak data cache root not writable: %s", type(exc).__name__)
        return None
    if not _private_dir(parent):
        logger.debug("espeak data cache directory is shared with other users; skipped")
        return None
    if (target / "phontab").is_file():
        if _private_dir(target) and _tree_digest(target) == _tree_digest(source):
            return target
        logger.debug("existing espeak data cache does not match the bundled data; skipped")
        return None
    staging = target.with_name(f"{digest}.tmp-{os.getpid()}")
    try:
        shutil.rmtree(staging, ignore_errors=True)
        shutil.copytree(source, staging)
        # copytree copies the source folder's mode; a group-writable wheel
        # folder would make the cache fail _private_dir on every reuse.
        os.chmod(staging, 0o700)
        try:
            os.replace(staging, target)
        except OSError:
            # Another process won the race; its copy is equivalent.
            shutil.rmtree(staging, ignore_errors=True)
    except OSError as exc:
        logger.debug("espeak data copy failed: %s", type(exc).__name__)
        shutil.rmtree(staging, ignore_errors=True)
        return None
    return target if (target / "phontab").is_file() else None


def _as_phonemizer_passes(path: Path) -> Path:
    """The form phonemizer hands to espeak: ``EspeakWrapper.data_path`` resolves it."""
    try:
        return Path(path).resolve()
    except OSError:
        return Path(path)


def _candidates(source: Path):
    """Candidate data directories, each in the exact form phonemizer will pass."""
    seen: set[str] = set()

    def fresh(path: Path) -> Path | None:
        effective = _as_phonemizer_passes(path)
        key = os.path.normcase(str(effective))
        if key in seen:
            return None
        seen.add(key)
        return effective

    first = fresh(source)
    if first is not None:
        yield first
    for root in _cache_roots():
        copy = _cached_copy(source, root)
        if copy is not None:
            effective = fresh(copy)
            if effective is not None:
                yield effective


def _apply(library: str, data_path: str) -> None:
    # misaki sets the wheel path at import time; import it first so it cannot
    # overwrite the path chosen here afterwards.
    try:
        import misaki.espeak  # noqa: F401
    except Exception:
        pass
    try:
        from phonemizer.backend.espeak.wrapper import EspeakWrapper

        EspeakWrapper.set_library(library)
        EspeakWrapper.set_data_path(data_path)
    except ImportError:
        pass
    os.environ["PHONEMIZER_ESPEAK_DATA_PATH"] = data_path
    os.environ["ESPEAK_DATA_PATH"] = data_path


def ensure_espeak_data_path(*, _loader=None) -> str | None:
    """Make phonemizer use an espeak-ng data directory espeak can open.

    Returns the chosen directory, or ``None`` when ``espeakng_loader`` is not
    installed (the engine then uses whatever espeak the system provides).
    Raises ``EspeakDataError`` instead of letting espeak ``exit(1)``.
    """
    global _RESOLVED
    if _loader is None:
        try:
            import espeakng_loader as _loader
        except ImportError:
            return None
    with _LOCK:
        if _RESOLVED is not None and (Path(_RESOLVED) / "phontab").is_file():
            _apply(_loader.get_library_path(), _RESOLVED)
            return _RESOLVED
        library = _loader.get_library_path()
        try:
            source = Path(_loader.get_data_path())
        except RuntimeError as exc:
            raise EspeakDataError(
                "The espeak-ng data bundled with this engine is missing. "
                "Reinstall the engine from Model Catalogue."
            ) from exc
        if not (source / "phontab").is_file():
            raise EspeakDataError(
                "The espeak-ng data bundled with this engine is incomplete "
                "(no phontab). Reinstall the engine from Model Catalogue."
            )
        for candidate in _candidates(source):
            verdict = espeak_resolves(library, str(candidate))
            if verdict is None:
                # Cannot ask the library; keep the wheel path and let
                # phonemizer raise its own catchable load error.
                _apply(library, str(source))
                return str(source)
            if verdict:
                if not _same_path(str(candidate), str(_as_phonemizer_passes(source))):
                    logger.info(
                        "espeak-ng cannot open the bundled data folder (path "
                        "of %d characters is too long or not representable); "
                        "using a short cached copy",
                        len(str(source)),
                    )
                _apply(library, str(candidate))
                _RESOLVED = str(candidate)
                return _RESOLVED
        raise EspeakDataError(
            "espeak-ng cannot open its data directory (the path is "
            "too long, contains characters it cannot read, or the data is "
            "unreadable) and no usable shorter copy could be made. Set OMNIVOICE_ESPEAK_DATA_CACHE to a short, "
            "writable ASCII folder (for example C:\\espeak) and retry."
        )
