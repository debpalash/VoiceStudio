import importlib.util
import base64
import json
from pathlib import Path
from unittest.mock import Mock


def module():
    spec = importlib.util.spec_from_file_location("sandbox_bookmarks", Path(__file__).resolve().parents[1] / "backend/core/sandbox_bookmarks.py")
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


def test_backend_refreshes_new_folder_grants_without_restarting_or_double_starting(tmp_path):
    source = module()
    stop = Mock()
    start = Mock(return_value=stop)
    grants = source.FolderGrants(start)
    file = tmp_path / "grants.json"
    file.write_text(json.dumps([{"id": "a" * 64, "bookmark": "first"}]))
    assert grants.refresh(file)
    assert grants.refresh(file)
    start.assert_called_once_with("first")
    file.write_text(json.dumps([{"id": "a" * 64, "bookmark": "first"}, {"id": "b" * 64, "bookmark": "second"}]))
    assert grants.refresh(file)
    assert start.call_count == 2
    grants.close()
    grants.close()
    assert stop.call_count == 2


def test_stale_and_corrupt_bookmarks_keep_existing_grants_and_do_not_expose_paths(tmp_path):
    source = module()
    start = Mock(return_value=Mock())
    grants = source.FolderGrants(start)
    file = tmp_path / "grants.json"
    file.write_text(json.dumps([{"id": "a" * 64, "bookmark": "first"}]))
    assert grants.refresh(file)
    start.side_effect = PermissionError("stale bookmark")
    file.write_text(json.dumps([{"id": "a" * 64, "bookmark": "changed"}]))
    assert not grants.refresh(file)
    assert grants.leases["a" * 64][0] == "first"
    file.write_text('{corrupt')
    assert not grants.refresh(file)
    assert "a" * 64 in grants.leases
    grants.close()


def test_core_foundation_access_balances_native_url_lifetime(monkeypatch):
    source = module()
    native = Mock()
    native.CFDataCreate.return_value = 101
    native.CFURLCreateByResolvingBookmarkData.return_value = 202
    native.CFURLStartAccessingSecurityScopedResource.return_value = 1
    monkeypatch.setattr(source.ctypes, "CDLL", Mock(return_value=native))
    close = source.access_bookmark(base64.b64encode(b"opaque bookmark").decode())
    native.CFURLStartAccessingSecurityScopedResource.assert_called_once_with(202)
    close()
    close()
    native.CFURLStopAccessingSecurityScopedResource.assert_called_once_with(202)
    assert [call.args[0] for call in native.CFRelease.call_args_list] == [101, 202]
