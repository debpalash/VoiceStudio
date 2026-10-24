"""Stale dub preview uploads are swept instead of accumulating forever."""
import os
import time


def _sweep(*args, **kwargs):
    # Resolved at call time: another test may replace core.file_cleanup in
    # sys.modules, which would leave a module-level import pointing at a stale copy.
    from core.file_cleanup import sweep_stale_files

    return sweep_stale_files(*args, **kwargs)


def _age(path, seconds):
    t = time.time() - seconds
    os.utime(path, (t, t))


def test_removes_only_old_regular_files(tmp_path):
    old = tmp_path / "old.mp4"
    new = tmp_path / "new.mp4"
    old.write_bytes(b"x")
    new.write_bytes(b"x")
    _age(old, 3 * 3600)
    sub = tmp_path / "sub"
    sub.mkdir()
    _age(sub, 3 * 3600)

    assert _sweep(tmp_path, 3600) == 1
    assert not old.exists()
    assert new.exists()
    assert sub.is_dir()


def test_missing_directory_is_a_noop(tmp_path):
    assert _sweep(tmp_path / "nope", 1) == 0


def test_symlink_is_not_followed(tmp_path):
    target = tmp_path / "keep.bin"
    target.write_bytes(b"x")
    _age(target, 10_000)
    scratch = tmp_path / "scratch"
    scratch.mkdir()
    link = scratch / "link.mp4"
    try:
        link.symlink_to(target)
    except OSError:
        return
    assert _sweep(scratch, 1) == 0
    assert target.exists()
