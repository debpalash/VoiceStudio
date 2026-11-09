"""Repair CTranslate2 shared libraries that ask the kernel for an executable stack.

CTranslate2 wheels up to 4.4.0 ship ``libctranslate2*.so`` files whose ELF
``PT_GNU_STACK`` program header has the execute flag (``PF_X``) set. Kernels
and C libraries that refuse executable stacks then fail ``dlopen`` with
"cannot enable executable stack", surfacing as ``OSError``/``ImportError`` and
taking down whisperx, faster-whisper and Argos translation (#692). The last
whisperx release for Python 3.11 pins ``ctranslate2<4.5.0``, so upgrading is
not an option.

The library never needs an executable stack, so the fix is to clear that one
flag bit in the header in place. This is done with plain file I/O: no
``patchelf`` and, crucially, no import of ctranslate2 (the import is what
fails). Nothing is cached; each call re-inspects the files, so a fresh install
or a manual repair is noticed without restarting the backend.
"""
from __future__ import annotations

import importlib.util
import logging
import os
import struct
import sys
import threading

logger = logging.getLogger("omnivoice.execstack")

PT_GNU_STACK = 0x6474E551
PF_X = 0x1

# Process-wide guard; re-entrant so helpers can nest. Cross-process safety
# comes from flock where the host supports it.
_LOCK = threading.RLock()

_ELF_MAGIC = b"\x7fELF"


def _locate_stack_flags(f):
    """Find the PT_GNU_STACK ``p_flags`` field in an open binary file.

    Returns ``(offset, endian_prefix, value)`` or None when the file is not an
    ELF we understand or has no such segment. Never raises for malformed data.
    """
    try:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.seek(0)
        ident = f.read(16)
        if len(ident) < 16 or ident[:4] != _ELF_MAGIC:
            return None
        ei_class, ei_data = ident[4], ident[5]
        if ei_class == 2:
            hdr_len, min_ent, flags_at = 64, 56, 4
        elif ei_class == 1:
            hdr_len, min_ent, flags_at = 52, 32, 24
        else:
            return None
        if ei_data == 1:
            e = "<"
        elif ei_data == 2:
            e = ">"
        else:
            return None
        if size < hdr_len:
            return None
        f.seek(0)
        hdr = f.read(hdr_len)
        if len(hdr) < hdr_len:
            return None
        if ei_class == 2:
            (phoff,) = struct.unpack_from(e + "Q", hdr, 0x20)
            phentsize, phnum = struct.unpack_from(e + "HH", hdr, 0x36)
        else:
            (phoff,) = struct.unpack_from(e + "I", hdr, 0x1C)
            phentsize, phnum = struct.unpack_from(e + "HH", hdr, 0x2A)
        if phnum == 0 or phoff < hdr_len or phentsize < min_ent:
            return None
        if phoff + phentsize * phnum > size:
            return None
        for i in range(phnum):
            base = phoff + i * phentsize
            f.seek(base)
            raw = f.read(4)
            if len(raw) < 4:
                return None
            (p_type,) = struct.unpack(e + "I", raw)
            if p_type != PT_GNU_STACK:
                continue
            f.seek(base + flags_at)
            raw = f.read(4)
            if len(raw) < 4:
                return None
            (flags,) = struct.unpack(e + "I", raw)
            return base + flags_at, e, flags
        return None
    except Exception:  # noqa: BLE001 — inspection must never raise
        return None


def has_execstack(path) -> bool | None:
    """True if the library requests an executable stack, False if it does not,
    None when the file is unreadable, not a supported ELF, or lacks the header."""
    try:
        with open(path, "rb") as f:
            found = _locate_stack_flags(f)
    except Exception:  # noqa: BLE001
        return None
    if found is None:
        return None
    return bool(found[2] & PF_X)


def clear_execstack(path) -> tuple[bool, str]:
    """Clear PF_X of the PT_GNU_STACK header in place.

    Returns ``(changed, detail)``. ``changed`` is False when there was nothing
    to do or the write failed.
    """
    name = os.fspath(path)
    with _LOCK:
        try:
            f = open(path, "r+b")
        except Exception as e:  # noqa: BLE001
            return False, f"{name} unreadable or not writable ({type(e).__name__}: {e})"
        try:
            locked = False
            if os.name == "posix":
                try:
                    import fcntl

                    fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                    locked = True
                except Exception as e:  # noqa: BLE001
                    logger.debug("flock unavailable for %s: %s", name, e)
            try:
                # Re-inspect through the locked descriptor: another process may
                # have repaired the file while we waited.
                found = _locate_stack_flags(f)
                if found is None:
                    return False, f"{name}: no PT_GNU_STACK segment"
                offset, endian, flags = found
                if not flags & PF_X:
                    return False, f"{name}: stack already non-executable"
                f.seek(offset)
                f.write(struct.pack(endian + "I", flags & ~PF_X))
                f.flush()
                os.fsync(f.fileno())
                return True, f"{name}: cleared the executable-stack flag"
            except Exception as e:  # noqa: BLE001
                return False, f"{name} unreadable or not writable ({type(e).__name__}: {e})"
            finally:
                if locked:
                    try:
                        import fcntl

                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                    except Exception:  # noqa: BLE001
                        pass
        finally:
            try:
                f.close()
            except Exception:  # noqa: BLE001
                pass


def _collect(dirs: list[str]) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for d in dirs:
        try:
            names = sorted(os.listdir(d))
        except Exception:  # noqa: BLE001 — missing or unreadable dir
            continue
        for n in names:
            if not n.startswith("libctranslate2"):
                continue
            if ".so" not in n and not n.endswith(".dylib"):
                continue
            p = os.path.join(d, n)
            if p not in seen:
                seen.add(p)
                out.append(p)
    return out


def ctranslate2_library_paths() -> list[str]:
    """Locate installed CTranslate2 native libraries without importing it."""
    dirs: list[str] = []
    try:
        spec = importlib.util.find_spec("ctranslate2")
    except (ImportError, ValueError):
        spec = None
    if spec is not None:
        roots = list(spec.submodule_search_locations or [])
        if not roots and spec.origin:
            roots = [os.path.dirname(spec.origin)]
        for root in roots:
            dirs.append(root)
            dirs.append(os.path.join(os.path.dirname(root), "ctranslate2.libs"))
    meipass = getattr(sys, "_MEIPASS", None)
    if getattr(sys, "frozen", False) and meipass:
        dirs.append(meipass)
        dirs.append(os.path.join(meipass, "ctranslate2.libs"))
    return _collect(dirs)


def ensure_ctranslate2_loadable() -> tuple[bool, str]:
    """Make sure CTranslate2's libraries can be loaded; repair them if needed."""
    from core.windows_rocm.runtime import initialize_dll_search

    # Required on every platform; also covers indirect imports such as Argos.
    initialize_dll_search()
    if sys.platform != "linux":
        return True, "exec-stack check not applicable on this platform"
    libs = ctranslate2_library_paths()
    if not libs:
        return True, "no CTranslate2 libraries found"
    repaired: list[str] = []
    blocked: list[tuple[str, str]] = []
    for path in libs:
        if has_execstack(path) is not True:
            continue
        changed, detail = clear_execstack(path)
        if changed:
            logger.warning(
                "Cleared the executable-stack flag on %s to keep CTranslate2 loadable (#692)",
                path,
            )
            repaired.append(os.path.basename(path))
        elif has_execstack(path) is not False:
            blocked.append((path, detail))
    if blocked:
        names = ", ".join(os.path.basename(p) for p, _ in blocked)
        reasons = "; ".join(d for _, d in blocked)
        first = blocked[0][0]
        return False, (
            f"CTranslate2 library requests an executable stack and could not be "
            f"repaired: {names} ({reasons}). Either reinstall the backend on "
            f"Python 3.12+ (ctranslate2 4.8+ has no such request), or run "
            f"`patchelf --clear-execstack {first}` once."
        )
    if repaired:
        return True, "repaired " + ", ".join(repaired)
    return True, "no executable-stack request"


def reset_ctranslate2_cache() -> None:
    """Compatibility no-op: results are never cached."""
    return None
