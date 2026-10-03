"""A flat directory must not defeat the storage report's category budget."""
import os

import pytest


def test_directory_budget_is_checked_between_files(tmp_path, monkeypatch):
    from services import storage_report
    root = tmp_path / "wide"
    root.mkdir()
    for index in range(30):
        (root / f"{index}.bin").write_bytes(b"1234")
    now = [0.0]
    visited = []
    original = os.lstat

    def slow_stat(path, *args, **kwargs):
        if os.path.dirname(os.fspath(path)) == str(root):
            visited.append(path)
            now[0] += 1.0
        return original(path, *args, **kwargs)

    monkeypatch.setattr(storage_report.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(storage_report.os, "lstat", slow_stat)
    size, complete, unreadable = storage_report._dir_size(str(root), deadline=3.0)
    assert len(visited) == 3
    assert size == 12
    assert complete is False
    assert unreadable is None


def test_expired_budget_does_not_stat_a_remaining_file(tmp_path, monkeypatch):
    from services import storage_report
    file = tmp_path / "remaining.bin"
    file.write_bytes(b"1234")
    monkeypatch.setattr(storage_report.time, "monotonic", lambda: 3.0)
    assert storage_report._dir_size(str(file), deadline=2.0) == (0, False, None)


def test_loose_data_files_report_partial_usage_after_deadline(tmp_path, monkeypatch):
    from services import storage_report
    data = tmp_path / "data"
    data.mkdir()
    for index in range(30):
        (data / f"{index}.bin").write_bytes(b"1234")
    now = [0.0]
    visited = []
    original = os.scandir

    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.name, self.path = entry.name, entry.path

        def is_dir(self, **kwargs):
            return self.entry.is_dir(**kwargs)

        def stat(self, **kwargs):
            visited.append(self.path)
            now[0] += 1.0
            return self.entry.stat(**kwargs)

    class Scan:
        def __enter__(self):
            self.scan = original(data)
            return (Entry(entry) for entry in self.scan)

        def __exit__(self, *args):
            self.scan.close()

    monkeypatch.setattr(storage_report.os, "scandir", lambda path: Scan() if os.fspath(path) == str(data) else original(path))
    monkeypatch.setattr(storage_report.time, "monotonic", lambda: now[0])
    report = storage_report.build_report(data_dir=str(data), hf_cache_dir=str(tmp_path / "hf"),
        engines_dir=str(tmp_path / "engines"), app_venv=None, temp_root=str(tmp_path / "temp"), category_timeout=3.0)
    category = next(c for c in report["categories"] if c["id"] == "data")
    other = next(c for c in category["children"] if c["id"] == "other")
    assert len(visited) == 3
    assert category["bytes"] == other["bytes"] == 12
    assert category["complete"] is False
    assert other["complete"] is False
    assert any(w.get("category_id") == "data" and w.get("reason") == "timeout" for w in report["warnings"])


@pytest.mark.parametrize("failure", ["enumeration", "classification"])
def test_other_is_incomplete_when_managed_engine_ownership_is_unknown(tmp_path, monkeypatch, failure):
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    installed = engines / "installed"
    (installed / ".venv").mkdir(parents=True)
    (installed / "weights.bin").write_bytes(b"not measured")
    original_scandir, original_stat = os.scandir, os.stat

    def scandir(path):
        if failure == "enumeration" and os.fspath(path) == str(engines):
            raise PermissionError("engine listing unavailable")
        return original_scandir(path)

    def stat(path, *args, **kwargs):
        if failure == "classification" and os.fspath(path) == str(installed / ".venv"):
            raise PermissionError("venv ownership unavailable")
        return original_stat(path, *args, **kwargs)

    monkeypatch.setattr(storage_report.os, "scandir", scandir)
    monkeypatch.setattr(storage_report.os, "stat", stat)
    report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
        hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "temp"))
    category = next(c for c in report["categories"] if c["id"] == "data")
    other = next(c for c in category["children"] if c["id"] == "other")
    assert category["complete"] is False
    assert other["complete"] is False
    assert any(w.get("category_id") == "data" and w.get("reason") == "permission" for w in report["warnings"])
