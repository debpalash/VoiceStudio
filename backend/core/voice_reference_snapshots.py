"""Track reference paths while longform resolvers can still read them."""
import os
import gc
import threading
import weakref


# Profile file swaps/deletion and resolution share this short synchronous lock.
voice_file_lock = threading.RLock()
_snapshots = weakref.WeakSet()


class VoiceReferenceSnapshot:
    """A cached resolver owns this object; registry membership is weak.

    Workers keep their resolver alive until they finish, even if their HTTP
    request was cancelled. No timers or job-status guesses govern file custody.
    """

    def __init__(self):
        self.paths = set()
        with voice_file_lock:
            _snapshots.add(self)

    def retain(self, path):
        if path:
            self.paths.add(os.path.realpath(path))


def references_in_use(paths):
    """Called under voice_file_lock before committing a profile deletion."""
    targets = {os.path.realpath(path) for path in paths}
    if not any(targets.intersection(snapshot.paths) for snapshot in _snapshots):
        return False
    # Finished failed workers can remain in traceback/frame cycles. Collect
    # unreachable owners before refusing deletion; live workers stay rooted.
    gc.collect()
    return any(targets.intersection(snapshot.paths) for snapshot in _snapshots)
