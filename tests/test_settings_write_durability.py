"""Settings files must survive a crash, a power cut, and a concurrent writer.

Two files hold settings the user expects to keep: ``prefs.json`` (every UI
preference, including the analytics/diagnostics consents) and ``~/.config/omnivoice/env``
(``OMNIVOICE_CACHE_DIR``, ``HF_ENDPOINT``, ``OMNIVOICE_TORCH_VARIANT`` — the values
that decide where models live and which build runs).

``os.replace`` alone is atomic against a process dying mid-write but not against
the machine losing power: the rename can reach the disk before the file's data
does, so the published name holds an empty or torn file. And ``user_env`` rewrote
its file in place and unserialized, so a crash between truncate and write dropped
every other key.
"""
from __future__ import annotations

import json
import pathlib
import sys
import threading

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "backend"))

@pytest.fixture()
def prefs():
    import importlib

    return importlib.import_module("core.prefs")


@pytest.fixture()
def user_env():
    import importlib

    return importlib.import_module("core.user_env")


@pytest.fixture()
def env_file(tmp_path, monkeypatch, user_env):
    path = tmp_path / "env"
    monkeypatch.setattr(user_env, "USER_ENV_PATH", str(path))
    monkeypatch.delenv("OMNIVOICE_ENV_FILE", raising=False)
    return path


def test_the_document_file_is_published_atomically(env_file, user_env):
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/models")
    user_env.set_user_env("HF_ENDPOINT", "https://hf-mirror.com")

    assert env_file.read_text(encoding="utf-8").splitlines() == [
        "OMNIVOICE_CACHE_DIR=/models",
        "HF_ENDPOINT=https://hf-mirror.com",
    ]


def test_a_failed_publish_leaves_the_previous_file_intact(env_file, monkeypatch, user_env):
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/models")
    before = env_file.read_text(encoding="utf-8")

    def boom(_src, _dst):
        raise OSError("rename failed")

    monkeypatch.setattr(user_env.os, "replace", boom)
    with pytest.raises(OSError):
        user_env.set_user_env("HF_ENDPOINT", "https://hf-mirror.com")

    # The write is discarded and the old file still parses with its key.
    assert env_file.read_text(encoding="utf-8") == before
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR", str(env_file)) == "/models"
    # No temp file is left behind next to it.
    assert [p.name for p in env_file.parent.iterdir()] == [env_file.name]


def test_concurrent_upserts_keep_every_key(env_file, user_env):
    keys = [f"KEY_{index}" for index in range(16)]
    barrier = threading.Barrier(len(keys))

    def upsert(key: str) -> None:
        barrier.wait()
        user_env.set_user_env(key, f"value-{key}", str(env_file))

    threads = [threading.Thread(target=upsert, args=(key,)) for key in keys]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    lines = set(env_file.read_text(encoding="utf-8").splitlines())
    missing = [key for key in keys if f"{key}=value-{key}" not in lines]
    assert not missing, f"concurrent upserts dropped {missing}; file was {sorted(lines)}"


def test_unset_preserves_the_other_keys(env_file, user_env):
    user_env.set_user_env("A", "1", str(env_file))
    user_env.set_user_env("B", "2", str(env_file))
    user_env.unset_user_env("A", str(env_file))
    assert env_file.read_text(encoding="utf-8").splitlines() == ["B=2"]


def test_the_env_file_keeps_its_restrictive_mode(env_file, user_env):
    user_env.set_user_env("HF_TOKEN", "secret-value", str(env_file))
    if sys.platform != "win32":
        assert env_file.stat().st_mode & 0o777 == 0o600


def test_prefs_flushes_the_file_and_its_directory(tmp_path, monkeypatch, prefs):
    target = tmp_path / "prefs.json"
    monkeypatch.setattr(prefs, "_PREFS_PATH", str(target))
    monkeypatch.setattr(prefs, "DATA_DIR", str(tmp_path))
    calls = {"file": 0, "dir": 0}

    from core import durable_io

    monkeypatch.setattr(durable_io, "flush_fd", lambda _fd: calls.__setitem__("file", calls["file"] + 1))
    monkeypatch.setattr(durable_io, "flush_dir", lambda _path: calls.__setitem__("dir", calls["dir"] + 1))

    prefs.set_("engine", "omnivoice")

    assert calls == {"file": 1, "dir": 1}, calls
    assert json.loads(target.read_text(encoding="utf-8"))["engine"] == "omnivoice"


def test_a_bare_filename_env_override_flushes_the_current_directory(tmp_path, monkeypatch, user_env):
    from core import durable_io

    flushed = []
    monkeypatch.setattr(durable_io, "flush_dir", flushed.append)
    monkeypatch.chdir(tmp_path)

    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/models", "env")

    assert flushed == ["."]
    assert (tmp_path / "env").read_text(encoding="utf-8").strip() == "OMNIVOICE_CACHE_DIR=/models"


def test_a_symlinked_env_file_is_written_through_to_its_target(tmp_path, monkeypatch, user_env):
    target = tmp_path / "dotfiles" / "voicestudio.env"
    target.parent.mkdir()
    target.write_text("HF_ENDPOINT=https://hf-mirror.com\n", encoding="utf-8")
    link = tmp_path / "env"
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError):
        pytest.skip("this host cannot create symlinks")
    monkeypatch.setattr(user_env, "USER_ENV_PATH", str(link))
    monkeypatch.delenv("OMNIVOICE_ENV_FILE", raising=False)

    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/models")

    assert link.is_symlink()
    assert target.read_text(encoding="utf-8").splitlines() == [
        "HF_ENDPOINT=https://hf-mirror.com",
        "OMNIVOICE_CACHE_DIR=/models",
    ]
