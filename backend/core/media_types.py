"""File extensions accepted for uploaded media.

Uploads keep the client's extension on disk, so an unrestricted extension lets
a caller store arbitrary file types (configuration files, scripts) in the data
directory. Media uploads accept only these containers; ffmpeg still verifies
the content.
"""

from __future__ import annotations

import os

# Every container the app's file pickers can offer (they accept ``audio/*`` and
# ``video/*``) and ffmpeg can demux — including VoiceStudio's own ``.m4b``
# audiobook export. The point is to keep configuration and script files out of
# the data directory, not to second-guess real media; ffmpeg checks content.
AUDIO_EXTS = frozenset({
    ".wav", ".wave", ".w64", ".rf64", ".bwf", ".mp3", ".mp2", ".mp1", ".mpa",
    ".m4a", ".m4b", ".m4r", ".aac", ".adts", ".flac", ".ogg", ".oga", ".opus",
    ".spx", ".wma", ".aif", ".aiff", ".aifc", ".caf", ".amr", ".awb", ".3ga",
    ".weba", ".mka", ".ac3", ".eac3", ".ec3", ".dts", ".mlp", ".thd", ".ape",
    ".wv", ".tta", ".tak", ".au", ".snd", ".voc", ".gsm", ".ra", ".dsf", ".dff",
    ".qcp",
})
VIDEO_EXTS = frozenset({
    ".mp4", ".m4v", ".mov", ".qt", ".mkv", ".mk3d", ".webm", ".avi", ".divx",
    ".wmv", ".asf", ".flv", ".f4v", ".mpg", ".mpeg", ".mpe", ".mpv", ".m1v",
    ".m2v", ".m2p", ".ts", ".tp", ".trp", ".m2t", ".mts", ".m2ts", ".3gp",
    ".3gpp", ".3g2", ".ogv", ".ogm", ".vob", ".vro", ".mod", ".tod", ".mxf",
    ".dv", ".rm", ".rmvb", ".nut", ".nsv", ".amv", ".wtv", ".ismv",
})
MEDIA_EXTS = AUDIO_EXTS | VIDEO_EXTS


def media_extension(filename: str | None, allowed: frozenset[str], default: str) -> str | None:
    """The lower-cased extension of ``filename`` if allowed, else None.

    A missing filename uses ``default``; a filename without an extension is
    refused, so the stored name never depends on unvalidated input.
    """
    if not filename:
        return default
    ext = os.path.splitext(os.path.basename(filename.replace("\\", "/")))[1].lower()
    return ext if ext in allowed else None


def unsupported_media_detail(kind: str, allowed: frozenset[str], ext: str) -> str:
    return (
        f"Unsupported {kind} file type '{ext or 'no extension'}'. "
        f"Use one of: {', '.join(sorted(allowed))}."
    )


def media_upload_suffix(filename: str | None, default: str = "") -> str | None:
    """Stored suffix for an uploaded media file, or None to refuse it.

    Like :func:`media_extension` with :data:`MEDIA_EXTS`, except a name without
    an extension keeps *default* (callers that probe content with ffmpeg do
    not need one).
    """
    name = str(filename or "")
    if "." not in name:
        return default
    # Everything after the last dot, separators included, so a name that
    # smuggles a path (``a.m\\..\\x``) is refused the same way on every OS.
    ext = "." + name.rsplit(".", 1)[1].lower()
    return ext if ext in MEDIA_EXTS else None

