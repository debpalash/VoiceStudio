"""Whether a standard stream is a person's terminal, the same way on every OS.

Windows reports the NUL device (stdin of CI jobs, services and scheduled
tasks, `< NUL`, `> NUL`) as a TTY, so `isatty()` alone would prompt with
nobody to answer or print progress into the void. A real Windows console
also answers GetConsoleMode.
"""
import os


def _windows_console(fd):
    import ctypes
    import msvcrt

    try:
        handle = msvcrt.get_osfhandle(fd)
    except OSError:
        return False
    mode = ctypes.c_uint32()
    return bool(ctypes.windll.kernel32.GetConsoleMode(handle, ctypes.byref(mode)))


def is_terminal(stream):
    try:
        if not stream.isatty():
            return False
        fd = stream.fileno()
    except (AttributeError, OSError, ValueError):
        return False
    return _windows_console(fd) if os.name == 'nt' else True
