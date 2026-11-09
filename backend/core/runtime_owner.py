"""Allow one backend process per data directory.

The lock file is never removed: replacing it would let two processes lock
different inodes. Kernel locks, rather than saved PIDs, establish ownership, so
a crashed backend releases the directory with its process.
"""
from __future__ import annotations

import errno
import os
from pathlib import Path
import time

_CONTENDED = {errno.EAGAIN, errno.EACCES, getattr(errno, 'EWOULDBLOCK', errno.EAGAIN),
              getattr(errno, 'EDEADLK', 36), 36}


class DataDirectoryBusy(RuntimeError):
    pass


class DataDirectoryLease:
    def __init__(self, directory: str | os.PathLike[str], timeout: float = 0) -> None:
        self.directory = Path(directory)
        self.timeout = timeout
        self.stream = None

    def __enter__(self) -> DataDirectoryLease:
        self.directory.mkdir(parents=True, exist_ok=True)
        fd = os.open(self.directory / '.runtime.lock', os.O_RDWR | os.O_CREAT, 0o600)
        self.stream = os.fdopen(fd, 'r+b', buffering=0)
        if os.fstat(fd).st_size == 0:
            self.stream.write(b'\0')
        deadline = time.monotonic() + self.timeout
        while True:
            try:
                if os.name == 'nt':
                    import msvcrt
                    self.stream.seek(0)
                    msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return self
            except OSError as exc:
                if exc.errno not in _CONTENDED:
                    self.stream.close()
                    raise
                if time.monotonic() >= deadline:
                    self.stream.close()
                    raise DataDirectoryBusy(
                        f'Another VoiceStudio backend is already using {self.directory}. '
                        'Quit VoiceStudio (or run "voicestudio stop"), or set OMNIVOICE_DATA_DIR '
                        'to a different folder, then start this backend again.') from None
                time.sleep(.05)

    def __exit__(self, *args: object) -> None:
        try:
            if os.name == 'nt':
                import msvcrt
                self.stream.seek(0)
                msvcrt.locking(self.stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.stream.fileno(), fcntl.LOCK_UN)
        finally:
            self.stream.close()
