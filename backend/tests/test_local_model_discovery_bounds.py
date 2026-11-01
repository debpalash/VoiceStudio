"""Discovery must stop on inspected entries, not only matching models."""
from pathlib import Path

import pytest

from services import local_model_paths as paths


@pytest.mark.parametrize("count,partial", [(3, False), (4, True)])
def test_candidate_limit_reports_only_actual_truncation(tmp_path, count, partial):
    for i in range(count):
        (tmp_path / str(i)).mkdir()
    result = paths.discover("tts", "omnivoice", str(tmp_path), limit=3)
    assert len(result["candidates"]) == 3
    assert result["partial"] is partial
    assert bool(result["warnings"]) is partial


def test_noise_entries_are_bounded_without_materializing_directory(tmp_path, monkeypatch):
    for i in range(12):
        (tmp_path / f"noise{i}").touch()
    monkeypatch.setattr(paths, "DISCOVERY_MAX_ENTRIES", 5, raising=False)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert result["candidates"] == []
    assert result["partial"] is True
    assert result["scanned_entries"] <= 5
    assert result["warnings"][0]["code"] == "scan_limit"


def test_directory_enumeration_stops_after_bounded_lookahead(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace
    count = 0
    def infinite_noise():
        nonlocal count
        while True:
            count += 1
            assert count <= 6, "directory was eagerly materialized"
            yield SimpleNamespace(is_dir=lambda **_: False)
    @contextmanager
    def scandir(_):
        yield infinite_noise()
    monkeypatch.setattr(paths, "DISCOVERY_MAX_ENTRIES", 5)
    monkeypatch.setattr(paths.os, "scandir", scandir)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert count == 6
    assert result["partial"] is True
    assert result["scanned_entries"] == 5


def test_deadline_is_explicit(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DISCOVERY_TIMEOUT_SECONDS", 0, raising=False)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert result["partial"] is True
    assert result["warnings"][0]["code"] == "time_limit"


def test_denied_child_does_not_hide_other_candidates(tmp_path, monkeypatch):
    (tmp_path / "denied").mkdir()
    (tmp_path / "readable").mkdir()
    original = paths.os.scandir
    def scandir(path):
        if Path(path).name == "denied":
            raise PermissionError("fixture denied")
        return original(path)
    monkeypatch.setattr(paths.os, "scandir", scandir)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert {item["name"] for item in result["candidates"]} == {"denied", "readable"}
    assert result["partial"] is True
    assert any(w["code"] == "permission_denied" and w["path"].endswith("denied") for w in result["warnings"])


def test_symlink_directories_are_not_followed(tmp_path):
    (tmp_path / "real").mkdir()
    (tmp_path / "alias").symlink_to(tmp_path / "real", target_is_directory=True)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert [item["name"] for item in result["candidates"]] == ["real"]


@pytest.mark.parametrize("error,code", [(PermissionError, "permission_denied"), (OSError, "read_error")])
def test_metadata_read_failures_are_explicit_partial_results(tmp_path, monkeypatch, error, code):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text("{}")
    original = Path.open
    def open_file(path, *args, **kwargs):
        if path == model / "config.json":
            raise error("fixture read failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "open", open_file)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert result["partial"] is True
    assert result["warnings"][0]["code"] == code
    assert result["candidates"][0]["validation"]["code"] == code


@pytest.mark.parametrize("count,partial", [(5, False), (6, True)])
def test_exact_scan_budget_is_not_truncation(tmp_path, monkeypatch, count, partial):
    for i in range(count):
        (tmp_path / str(i)).touch()
    monkeypatch.setattr(paths, "DISCOVERY_MAX_ENTRIES", 5)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert result["scanned_entries"] == 5
    assert result["partial"] is partial


def test_large_metadata_is_bounded_and_reported(tmp_path, monkeypatch):
    model = tmp_path / "model"
    model.mkdir()
    (model / "config.json").write_text(" " * 100)
    monkeypatch.setattr(paths, "DISCOVERY_MAX_METADATA_BYTES", 20)
    result = paths.discover("tts", "omnivoice", str(tmp_path))
    assert result["partial"] is True
    assert result["warnings"][0]["code"] == "read_limit"


def test_checkpoint_asset_symlink_directory_is_not_scanned(tmp_path):
    from backend.tests.test_local_model_paths import checkpoint
    model = checkpoint(tmp_path / "model")
    assets = model / "audio_tokenizer"
    assets.rename(tmp_path / "external")
    assets.symlink_to(tmp_path / "external", target_is_directory=True)
    result = paths.discover("tts", "omnivoice", str(model))
    assert result["candidates"][0]["validation"]["status"] == "invalid"
    assert any(w["code"] == "symlink_directory" for w in result["warnings"])

