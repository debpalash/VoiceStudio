"""A flat directory must not defeat the storage report's category budget."""
import os

from services import storage_report


def test_directory_budget_is_checked_between_files(tmp_path, monkeypatch):
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
    file = tmp_path / "remaining.bin"
    file.write_bytes(b"1234")
    monkeypatch.setattr(storage_report.time, "monotonic", lambda: 3.0)
    assert storage_report._dir_size(str(file), deadline=2.0) == (0, False, None)


def test_loose_data_files_report_partial_usage_after_deadline(tmp_path, monkeypatch):
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
