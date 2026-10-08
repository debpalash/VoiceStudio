"""plan-01 follow-up / #64 — durable per-user env file helper.

Backs the configurable models directory: the Settings endpoint writes
OMNIVOICE_CACHE_DIR into ~/.config/omnivoice/env, which main.py loads at startup
(→ HF_HOME / HF_HUB_CACHE / TORCH_HOME). The helper must upsert one key without
clobbering others (e.g. a persisted HF_TOKEN) and store the file 0600.
"""
from __future__ import annotations

import os
import stat
import sys

from core import user_env


def test_set_creates_and_upserts(tmp_path):
    p = str(tmp_path / "env")
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/data/models", path=p)
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR", path=p) == "/data/models"
    # upsert: change value, do not duplicate the key
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/other/models", path=p)
    assert user_env.get_user_env("OMNIVOICE_CACHE_DIR", path=p) == "/other/models"
    with open(p) as f:
        assert f.read().count("OMNIVOICE_CACHE_DIR=") == 1


def test_preserves_other_keys(tmp_path):
    p = tmp_path / "env"
    p.write_text("HF_TOKEN=hf_abc123\nFOO=bar\n")
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/m", path=str(p))
    txt = p.read_text()
    assert "HF_TOKEN=hf_abc123" in txt
    assert "FOO=bar" in txt
    assert "OMNIVOICE_CACHE_DIR=/m" in txt


def test_unset_removes_only_that_key(tmp_path):
    p = tmp_path / "env"
    p.write_text("HF_TOKEN=hf_x\nOMNIVOICE_CACHE_DIR=/m\n")
    user_env.unset_user_env("OMNIVOICE_CACHE_DIR", path=str(p))
    txt = p.read_text()
    assert "OMNIVOICE_CACHE_DIR" not in txt
    assert "HF_TOKEN=hf_x" in txt


def test_get_missing_returns_none(tmp_path):
    assert user_env.get_user_env("NOPE", path=str(tmp_path / "env")) is None


def test_set_with_bare_filename_no_parent(tmp_path, monkeypatch):
    # A path with no directory component (e.g. OMNIVOICE_ENV_FILE=env) must not
    # blow up: os.makedirs("") raises, so the helper has to skip the mkdir.
    monkeypatch.chdir(tmp_path)
    user_env.set_user_env("K", "v", path="envfile")
    assert user_env.get_user_env("K", path="envfile") == "v"


def test_file_is_0600(tmp_path):
    if sys.platform == "win32":
        return  # POSIX perms not meaningful on Windows
    p = tmp_path / "env"
    user_env.set_user_env("K", "v", path=str(p))
    assert stat.S_IMODE(os.stat(p).st_mode) == 0o600


import pytest


@pytest.mark.parametrize("value", [
    "/chosen/Books #1",
    "/chosen/it's here",
    "C:\\Users\\me\\Models #2",
    "/chosen/${HOME}/models",
    "/chosen/a b/c\\'d",
    '/chosen/"quoted"',
])
def test_special_characters_survive_the_dotenv_round_trip(tmp_path, monkeypatch, value):
    """A folder like ``Books #1`` was written unquoted and truncated at `` #``
    by the startup dotenv loader, silently pointing at a sibling directory."""
    p = tmp_path / "env"
    p.write_text("HF_TOKEN=hf_keep\n")
    user_env.set_user_env("OMNIVOICE_TEST_PATH", value, path=str(p))
    assert user_env.get_user_env("OMNIVOICE_TEST_PATH", path=str(p)) == value
    monkeypatch.delenv("OMNIVOICE_TEST_PATH", raising=False)
    monkeypatch.setenv("HF_TOKEN", "")
    assert user_env.load_into_environ(str(p)) is True
    assert os.environ["OMNIVOICE_TEST_PATH"] == value
    assert os.environ["HF_TOKEN"] == "hf_keep"


def test_ordinary_values_stay_unquoted(tmp_path):
    p = tmp_path / "env"
    user_env.set_user_env("HF_ENDPOINT", "https://hf-mirror.com", path=str(p))
    user_env.set_user_env("OMNIVOICE_CACHE_DIR", "/data/models", path=str(p))
    assert p.read_text() == "HF_ENDPOINT=https://hf-mirror.com\nOMNIVOICE_CACHE_DIR=/data/models\n"


def test_line_breaks_are_rejected(tmp_path):
    with pytest.raises(ValueError):
        user_env.set_user_env("K", "a\nb", path=str(tmp_path / "env"))


def test_hand_written_unquoted_values_still_expand_variables(tmp_path, monkeypatch):
    """docs/performance.md tells users to edit this file; ``${HOME}`` in an
    unquoted or double-quoted value expands as before #2519, while the
    single-quoted form the app writes stays literal."""
    monkeypatch.setenv("HOME", "/home/u")
    p = tmp_path / "env"
    p.write_text(
        "HF_HOME=${HOME}/models\n"
        'OMNIVOICE_TEST_DQ="${HOME}/dq"\n'
        "OMNIVOICE_TEST_SQ='${HOME}/sq'\n"
        "OMNIVOICE_TEST_LAST='${HOME}/first'\n"
        "OMNIVOICE_TEST_LAST=${HOME}/second\n"
    )
    user_env.set_user_env("OMNIVOICE_TEST_PATH", "/chosen/${HOME}/models", path=str(p))
    for key in ("HF_HOME", "OMNIVOICE_TEST_DQ", "OMNIVOICE_TEST_SQ", "OMNIVOICE_TEST_LAST",
                "OMNIVOICE_TEST_PATH"):
        monkeypatch.delenv(key, raising=False)
    assert user_env.load_into_environ(str(p)) is True
    assert os.environ["HF_HOME"] == "/home/u/models"
    assert os.environ["OMNIVOICE_TEST_DQ"] == "/home/u/dq"
    assert os.environ["OMNIVOICE_TEST_SQ"] == "${HOME}/sq"
    assert os.environ["OMNIVOICE_TEST_LAST"] == "/home/u/second"
    assert os.environ["OMNIVOICE_TEST_PATH"] == "/chosen/${HOME}/models"
