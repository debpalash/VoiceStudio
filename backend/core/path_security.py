"""Filesystem trust-boundary helpers.

Paths persisted in SQLite are still untrusted: older clients and imported job
records can contain absolute paths, traversal components, or symlink escapes.
Keep containment checks at the filesystem boundary instead of relying on the
route or database layer to have sanitised a value earlier.
"""

from __future__ import annotations

import ntpath
import os
import re
from pathlib import Path

_WINDOWS_RESERVED_NAMES = frozenset({"CON", "PRN", "AUX", "NUL"}) | frozenset(
    f"{prefix}{number}" for prefix in ("COM", "LPT") for number in "123456789¹²³"
)

# Both separator families, so a stored sub-path splits into the same components
# on every host. Windows accepts ``/`` as a real separator, so splitting on
# ``os.sep`` alone left ``"job/out.mp4"`` as a single component there while the
# identical value split cleanly on POSIX. POSIX input never reaches this with a
# backslash — it is rejected as a foreign separator before the split.
_PATH_SEPARATORS = re.compile(r"[\\/]")


class UnsafePath(ValueError):
    """Raised when a path crosses its allowed filesystem boundary."""


def safe_filename(value: object) -> str:
    """Return a portable bare filename, rejecting traversal and drive paths."""
    name = str(value or "")
    if (
        not name
        or name in {".", ".."}
        or "/" in name
        or "\\" in name
        or os.path.isabs(name)
        or ntpath.isabs(name)
        or ntpath.basename(name) != name
        or name.endswith((" ", "."))
        or _PORTABLE_INVALID_CHARS.search(name)
        or name.split(".", 1)[0].rstrip().upper() in _WINDOWS_RESERVED_NAMES
        or len(name.encode("utf-8")) > 240
    ):
        raise UnsafePath("expected a bare filename")
    return name


_PORTABLE_INVALID_CHARS = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]')


def portable_filename(value: object, default: str = "file", max_bytes: int = 200) -> str:
    """Turn arbitrary text (a video title, a voice name) into a filename every
    desktop OS accepts. ``safe_filename`` *validates*; this *repairs*.

    Windows rejects ``< > : " / \\ | ? *``, control characters, trailing
    dots/spaces and device names (``CON``, ``NUL``...) with ``[Errno 22] Invalid
    argument``; titles like ``"How to X: a guide?"`` hit that on the first
    export. The extension survives truncation, which is by UTF-8 bytes so a CJK
    title cannot overrun the 255-byte name limit of ext4/APFS/NTFS.
    """
    name = _PORTABLE_INVALID_CHARS.sub("_", str(value or "")).strip(" .")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 16:
        stem, ext = name, ""
    else:
        ext = "." + ext
    budget = max(2, max_bytes - len(ext.encode("utf-8")))

    def _fit(text: str) -> str:
        return text.encode("utf-8")[:budget].decode("utf-8", "ignore").rstrip(" .")

    stem = _fit(stem) or default
    if not stem.strip("_ "):
        stem = default
    # Check device names AFTER truncation: cutting a long stem can expose "CON".
    if stem.split(".", 1)[0].rstrip().upper() in _WINDOWS_RESERVED_NAMES:
        stem = _fit("_" + stem)
    return stem + ext


def safe_relative_path(value: object) -> str:
    """Validate a remote-supplied relative path such as a Hub ``rfilename``.

    Accepts forward-slash separated names (``subdir/model.safetensors``) and
    rejects anything that could name a location outside the directory it is
    joined to on any desktop OS: absolute, drive or UNC paths, backslashes,
    ``.``/``..`` and empty segments, ``:`` (drives, NTFS streams) and control
    characters. Returns the value unchanged when it is safe.
    """
    if not isinstance(value, str) or not value:
        raise UnsafePath("path is empty")
    if (
        "\\" in value
        or ":" in value
        or value.startswith("/")
        or os.path.isabs(value)
        or ntpath.isabs(value)
        or ntpath.splitdrive(value)[0]
        or re.search(r"[\x00-\x1f\x7f]", value)
    ):
        raise UnsafePath("path must be relative")
    if any(part in {"", ".", ".."} for part in value.split("/")):
        raise UnsafePath("path contains an unsafe component")
    return value


def contained_child(root: os.PathLike[str] | str, rel: object) -> Path:
    """``root/rel`` for a remote-supplied ``rel``, proven to stay under ``root``.

    The parent directories are resolved (following any symlinks already on
    disk) and must remain inside the resolved root. The final component is not
    followed, so an existing cache pointer that links to its blob elsewhere is
    still recognised as living in this directory.
    """
    parts = safe_relative_path(rel).split("/")
    root_path = Path(os.path.realpath(root))
    parent = Path(os.path.realpath(root_path.joinpath(*parts[:-1])))
    try:
        if os.path.commonpath((str(root_path), str(parent))) != str(root_path):
            raise UnsafePath("path escapes its allowed root")
    except ValueError as exc:  # Windows paths on different drives
        raise UnsafePath("path escapes its allowed root") from exc
    return parent / parts[-1]


def resolve_within(root: os.PathLike[str] | str, value: os.PathLike[str] | str) -> Path:
    """Resolve *value* beneath *root*, rejecting traversal and symlink escapes.

    Absolute values are accepted only when they already resolve inside the
    root. This preserves existing database rows, which historically stored a
    mixture of relative filenames and absolute job-artifact paths.
    """
    raw = os.fspath(value) if value is not None else ""
    if not isinstance(raw, str) or not raw:
        raise UnsafePath("path is empty")
    # Treat both separator families as structural on every host while still
    # rejecting Windows drive paths before rebuilding relative components.
    if os.sep != "\\" and bool(ntpath.splitdrive(raw)[0]):
        raise UnsafePath("path uses a drive")
    root_path = Path(root).expanduser().resolve(strict=False)
    root_text = str(root_path)
    if os.path.isabs(raw):
        prefix = root_text.rstrip(os.sep) + os.sep
        if not os.path.normcase(raw).startswith(os.path.normcase(prefix)):
            raise UnsafePath("path escapes its allowed root")
        raw = raw[len(prefix):]

    # Rebuild from individually sanitized basenames. Besides making the
    # containment proof explicit to static analysis, this rejects empty,
    # dot, parent, drive, and separator-bearing components before Path sees
    # any persisted/request-derived string.
    parts = _PATH_SEPARATORS.split(raw)
    clean_parts: list[str] = []
    for part in parts:
        clean = os.path.basename(part)
        if not clean or clean in {".", ".."} or clean != part:
            raise UnsafePath("path contains an unsafe component")
        clean_parts.append(clean)
    candidate = root_path.joinpath(*clean_parts)
    resolved = candidate.resolve(strict=False)
    try:
        if os.path.commonpath((str(root_path), str(resolved))) != str(root_path):
            raise UnsafePath("path escapes its allowed root")
    except ValueError as exc:  # Windows paths on different drives
        raise UnsafePath("path escapes its allowed root") from exc
    if resolved == root_path:
        raise UnsafePath("path must name an item below its allowed root")
    return resolved


def contained_join(root: os.PathLike[str] | str, value: object) -> str | None:
    """``os.path.join(root, value)`` for a persisted name, or None if unsafe.

    Database rows hold either a bare filename relative to *root* or, for older
    rows, an absolute path that was built from *root*. Both keep their exact
    historical spelling (render caches key on it); a value that is empty or
    resolves outside *root* returns None so callers treat it as absent.
    """
    if not isinstance(value, (str, os.PathLike)):
        return None
    raw = os.fspath(value)
    if not isinstance(raw, str) or not raw:
        return None
    root_text = os.fspath(root)
    relative = raw
    if os.path.isabs(raw):
        # Absolute rows were written from the unresolved root; strip that
        # spelling first so a symlinked data folder keeps matching.
        prefix = os.path.normcase(root_text.rstrip("\\/") + os.sep)
        if os.path.normcase(raw).startswith(prefix):
            relative = raw[len(prefix):]
    try:
        resolve_within(root_text, relative)
    except UnsafePath:
        return None
    return os.path.join(root_text, raw)


_UPLOAD_SUFFIX = re.compile(r"\.[A-Za-z0-9]{1,16}\Z")


def upload_suffix(filename: object, default: str = "") -> str | None:
    """Extension of a client-supplied upload name, safe to append to a
    server-generated stem; *default* when the name has none.

    Returns None when the extension is anything but letters and digits (a
    path separator, an NTFS ``:stream``, control characters), so callers
    decide between a 415 and a neutral fallback.
    """
    name = str(filename or "")
    if "." not in name:
        return default
    # Everything after the last dot, separators included, so a name that
    # smuggles a path (``a.w\\..\\x``) is refused the same way on every OS:
    # ``os.path.splitext`` treats a backslash as a separator only on Windows.
    ext = "." + name.rsplit(".", 1)[1]
    return ext if _UPLOAD_SUFFIX.fullmatch(ext) else None
