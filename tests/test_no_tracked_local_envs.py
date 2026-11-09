"""Local environments must never be committed.

`.gitignore` listed `.venv/`, and a trailing-slash pattern matches only a
directory. A worktree that symlinks `.venv` to a shared environment (the usual
way to save disk) therefore showed the symlink as untracked, and `git add -A`
committed it: CI's `uv sync` then failed with "failed to create directory
`.venv`: File exists" (#3045). Ignore patterns are now slash-free; this keeps
any path under one of these names out of the index.
"""
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LOCAL_ONLY = {".venv", "node_modules"}


def test_no_local_environment_is_tracked():
    files = subprocess.run(
        ["git", "-C", str(ROOT), "ls-files", "-z"], check=True, capture_output=True, text=True,
    ).stdout.split("\0")
    tracked = [f for f in files if f and LOCAL_ONLY & set(Path(f).parts)]
    assert not tracked, f"local environments are tracked: {tracked[:5]}"
