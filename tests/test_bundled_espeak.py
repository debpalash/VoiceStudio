import importlib.util
from pathlib import Path
from unittest.mock import Mock

import pytest


def module():
    spec = importlib.util.spec_from_file_location("bundled_espeak", Path(__file__).resolve().parents[1] / "backend/core/bundled_espeak.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_instances_are_isolated_reused_after_termination_and_never_written(tmp_path):
    paths = [tmp_path / f"instance-{index}.dylib" for index in range(2)]
    for path in paths:
        path.write_bytes(b"signed image")
        path.chmod(0o444)
    source = tmp_path / "source.dylib"
    pool = module().NativeInstances(paths, source)
    first, second = pool.claim(source), pool.claim(source)
    assert first.path != second.path
    with pytest.raises(RuntimeError, match="currently in use"):
        pool.claim(source)
    first.library = Mock()
    native = first.library
    first.close()
    first.close()
    native.espeak_Terminate.assert_called_once()
    third = pool.claim(source)
    assert third.path == first.path
    third.close()
    second.close()
    assert [path.read_bytes() for path in paths] == [b"signed image"] * 2
    with pytest.raises(RuntimeError, match="not the bundled"):
        pool.claim(tmp_path / "unbundled.dylib")


def test_failed_termination_still_releases_the_instance(tmp_path):
    path = tmp_path / "signed.dylib"
    path.write_bytes(b"signed image")
    pool = module().NativeInstances([path], path)
    lease = pool.claim(path)
    lease.library = Mock()
    lease.library.espeak_Terminate.side_effect = RuntimeError("termination failed")
    with pytest.raises(RuntimeError, match="termination failed"):
        lease.close()
    assert pool.claim(path).path == path
