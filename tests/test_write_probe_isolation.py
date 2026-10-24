"""Writable-directory probes must not overwrite existing filesystem entries."""
import os

import pytest

from core import diagnose, user_env


@pytest.mark.parametrize("probe", ["diagnostics", "saved_environment"])
def test_writability_probe_preserves_existing_marker(tmp_path, monkeypatch, probe):
    if probe == "diagnostics":
        marker = tmp_path / ".diagnose_write_probe"
        monkeypatch.setattr(diagnose, "DATA_DIR", str(tmp_path))
    else:
        marker = tmp_path / f".omnivoice-write-probe-{os.getpid()}"
        monkeypatch.setattr(user_env, "_PATH_KEYS", ("OMNIVOICE_TEST_PROBE_DIR",))
        monkeypatch.setenv("OMNIVOICE_TEST_PROBE_DIR", str(tmp_path))
    marker.write_text("existing file contents", encoding="utf-8")
    if probe == "diagnostics":
        assert diagnose._check_data_dir()["status"] == diagnose.OK
    else:
        user_env._drop_invalid_path_keys()
        assert os.environ["OMNIVOICE_TEST_PROBE_DIR"] == str(tmp_path)
    assert marker.read_text(encoding="utf-8") == "existing file contents"
    assert sorted(path.name for path in tmp_path.iterdir()) == [marker.name]


@pytest.mark.parametrize("probe", ["diagnostics", "saved_environment"])
def test_writability_probe_leaves_no_files(tmp_path, monkeypatch, probe):
    if probe == "diagnostics":
        monkeypatch.setattr(diagnose, "DATA_DIR", str(tmp_path))
        assert diagnose._check_data_dir()["status"] == diagnose.OK
    else:
        monkeypatch.setattr(user_env, "_PATH_KEYS", ("OMNIVOICE_TEST_PROBE_DIR",))
        monkeypatch.setenv("OMNIVOICE_TEST_PROBE_DIR", str(tmp_path))
        user_env._drop_invalid_path_keys()
        assert os.environ["OMNIVOICE_TEST_PROBE_DIR"] == str(tmp_path)
    assert list(tmp_path.iterdir()) == []
