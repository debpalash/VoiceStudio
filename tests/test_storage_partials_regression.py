import os

import pytest



def test_partial_managed_engines_remain_in_data_footprint(tmp_path):
    from services.storage_report import build_report
    data = tmp_path / "data"
    engines = data / "engines"
    installed = engines / "installed"
    (installed / ".venv").mkdir(parents=True)
    (installed / "weights.bin").write_bytes(b"i" * 70)
    partial = engines / "interrupted" / "checkpoints"
    partial.mkdir(parents=True)
    (partial / "weights.bin").write_bytes(b"p" * 110)
    (engines / "install.log").write_bytes(b"log")
    report = build_report(data_dir=str(data), engines_dir=str(engines),
                          hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
    categories = {c["id"]: c for c in report["categories"]}
    assert categories["engine_venvs"]["bytes"] == 70
    other = next(c for c in categories["data"]["children"] if c["id"] == "other")
    assert other["bytes"] == 113
    assert sum(c["bytes"] for c in categories.values()) == 183


def test_unreadable_managed_engines_are_reported(tmp_path, monkeypatch):
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    engines.mkdir(parents=True)
    real_scandir = storage_report.os.scandir
    def scandir(path):
        if str(path) == str(engines):
            raise PermissionError("unreadable engines")
        return real_scandir(path)
    monkeypatch.setattr(storage_report.os, "scandir", scandir)
    report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
        hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
    warned = {w["category_id"] for w in report["warnings"] if w["kind"] == "unreadable"}
    assert {"data", "engine_venvs"} <= warned
    assert all(not c["complete"] for c in report["categories"] if c["id"] in warned)


@pytest.mark.parametrize("failing_call", [1, 2])
def test_engine_entry_inspection_errors_do_not_abort_report(tmp_path, monkeypatch, failing_call):
    from contextlib import contextmanager
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    partial = engines / "partial"
    partial.mkdir(parents=True)
    (partial / "weights.bin").write_bytes(b"saved")
    real_scandir = storage_report.os.scandir
    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.calls = 0
            self.path, self.name = entry.path, entry.name
        def is_dir(self, **kwargs):
            self.calls += 1
            if self.calls == failing_call:
                raise PermissionError("entry inaccessible")
            return self.entry.is_dir(**kwargs)
        def stat(self, **kwargs):
            return self.entry.stat(**kwargs)
    @contextmanager
    def engine_scan():
        with real_scandir(engines) as entries:
            yield iter([Entry(e) for e in entries])
    def scandir(path):
        return engine_scan() if str(path) == str(engines) else real_scandir(path)
    monkeypatch.setattr(storage_report.os, "scandir", scandir)
    report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
        hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
    assert any(w["kind"] == "unreadable" and w["path"] == str(partial) for w in report["warnings"])


@pytest.mark.skipif(os.name != "posix", reason="native POSIX permission reproduction")
def test_native_unreadable_engines_emit_partial_report(tmp_path):
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    engines.mkdir(parents=True)
    (engines / "partial.bin").write_bytes(b"unseen")
    engines.chmod(0)
    try:
        try:
            with os.scandir(engines):
                pass
        except PermissionError:
            pass
        else:
            pytest.skip("current user bypasses directory permissions")
        report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
            hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
        warnings = [w for w in report["warnings"] if w["kind"] == "unreadable"]
        assert {w["category_id"] for w in warnings} == {"data", "engine_venvs"}
        assert all(not c["complete"] for c in report["categories"] if c["id"] in {"data", "engine_venvs"})
    finally:
        engines.chmod(0o700)


def test_transient_installed_entry_error_preserves_category_ownership(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    installed = engines / "installed"
    (installed / ".venv").mkdir(parents=True)
    (installed / "weights.bin").write_bytes(b"installed weights")
    real_scandir = storage_report.os.scandir
    class Entry:
        def __init__(self, entry):
            self.entry = entry
            self.path, self.name = entry.path, entry.name
            self.calls = 0
        def is_dir(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                raise PermissionError("transient entry inspection failure")
            return self.entry.is_dir(**kwargs)
        def stat(self, **kwargs):
            return self.entry.stat(**kwargs)
    @contextmanager
    def scan_engines():
        with real_scandir(engines) as entries:
            yield iter([Entry(e) for e in entries])
    def scandir(path):
        return scan_engines() if str(path) == str(engines) else real_scandir(path)
    monkeypatch.setattr(storage_report.os, "scandir", scandir)
    report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
        hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
    categories = {c["id"]: c for c in report["categories"]}
    assert categories["engine_venvs"]["bytes"] == len(b"installed weights")
    other = next(c for c in categories["data"]["children"] if c["id"] == "other")
    assert other["bytes"] == 0
    assert any(w["kind"] == "unreadable" and w["path"] == str(installed) for w in report["warnings"])


def test_unreadable_venv_is_not_treated_as_missing(tmp_path, monkeypatch):
    from services import storage_report
    data = tmp_path / "data"
    engines = data / "engines"
    installed = engines / "installed"
    (installed / ".venv").mkdir(parents=True)
    (installed / "model.bin").write_bytes(b"installed bytes")
    venv = str(installed / ".venv")
    real_stat = storage_report.os.stat

    def stat(path, *args, **kwargs):
        if str(path) == venv:
            raise PermissionError("traversal denied")
        return real_stat(path, *args, **kwargs)

    monkeypatch.setattr(storage_report.os, "stat", stat)
    report = storage_report.build_report(data_dir=str(data), engines_dir=str(engines),
        hf_cache_dir=str(tmp_path / "hf"), temp_root=str(tmp_path / "tmp"))
    categories = {c["id"]: c for c in report["categories"]}
    assert not categories["engine_venvs"]["complete"]
    other = next(c for c in categories["data"]["children"] if c["id"] == "other")
    assert other["bytes"] == 0  # not silently reclassified as application data
    assert any(w["kind"] == "unreadable" and w["path"] == str(installed) for w in report["warnings"])
