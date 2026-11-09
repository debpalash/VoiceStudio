import os

from core.env_startup import load_startup_env


def test_utf16_project_env_and_user_settings_load(tmp_path, monkeypatch):
    project = tmp_path / ".env"
    project.write_text("VOICE_TEST_PROJECT=Grüße\n", encoding="utf-16")
    user = tmp_path / "user-env"
    user.write_text("VOICE_TEST_USER=loaded\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", str(user))
    monkeypatch.delenv("VOICE_TEST_PROJECT", raising=False)
    monkeypatch.delenv("VOICE_TEST_USER", raising=False)
    load_startup_env(str(project), str(project))
    assert os.environ["VOICE_TEST_PROJECT"] == "Grüße"
    assert os.environ["VOICE_TEST_USER"] == "loaded"


def test_bad_project_file_does_not_block_user_settings(tmp_path, monkeypatch, caplog):
    project = tmp_path / ".env"
    project.write_bytes(b"VOICE_TEST_PROJECT=\xff\n")
    user = tmp_path / "user-env"
    user.write_text("VOICE_TEST_USER=loaded\n", encoding="utf-8")
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", str(user))
    monkeypatch.delenv("VOICE_TEST_USER", raising=False)
    load_startup_env(str(project), str(project))
    assert os.environ["VOICE_TEST_USER"] == "loaded"
    assert "encoding" in caplog.text.lower()


def test_bad_user_settings_are_left_untouched_and_startup_continues(tmp_path, monkeypatch, caplog):
    user = tmp_path / "user-env"
    original = b"VOICE_TEST_USER=\xff\n"
    user.write_bytes(original)
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", str(user))
    load_startup_env("", "")
    assert user.read_bytes() == original
    assert "saved environment settings" in caplog.text.lower()


def test_existing_environment_takes_precedence_over_project_file(tmp_path, monkeypatch):
    project = tmp_path / ".env"
    project.write_text("VOICE_TEST_PROJECT=file\n", encoding="utf-8-sig")
    monkeypatch.setenv("OMNIVOICE_ENV_FILE", str(tmp_path / "missing"))
    monkeypatch.setenv("VOICE_TEST_PROJECT", "launcher")
    load_startup_env(str(project), str(project))
    assert os.environ["VOICE_TEST_PROJECT"] == "launcher"
