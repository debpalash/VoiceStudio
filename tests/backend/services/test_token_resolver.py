"""Tests for backend/services/token_resolver.py — AUTH-01 / AUTH-03 / AUTH-06.

Covers the 3-source cascade (App → Env → HF-CLI), the on_401 fallback,
the state() shape consumed by the Settings UI, and the save/clear API the
React panel will call.
"""
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest


APP_TOKEN = "hf_appapp00000000000000000000000000000000abc"
ENV_TOKEN = "hf_envenv00000000000000000000000000000000def"
CLI_TOKEN = "hf_clicli00000000000000000000000000000000ghi"


@pytest.fixture
def fresh_resolver(monkeypatch, tmp_path):
    """Fresh import of services.token_resolver + a tmp DB-backed settings
    store. Also clears HF env vars so the env source is empty by default."""
    from huggingface_hub import constants
    monkeypatch.setattr(constants, "HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setattr(constants, "HF_STORED_TOKENS_PATH", str(tmp_path / "stored_tokens"))
    monkeypatch.setenv("HF_TOKEN_PATH", str(tmp_path / "hf-token"))
    monkeypatch.setenv("OMNIVOICE_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("HUGGING_FACE_HUB_TOKEN", raising=False)

    # Aggressive purge — see note in test_settings_store.py.
    for mod in list(sys.modules):
        if (
            mod == "core" or mod.startswith("core.")
            or mod == "services" or mod.startswith("services.")
        ):
            del sys.modules[mod]
    from core import db as _db
    _db.init_db()
    from services import token_resolver
    token_resolver.invalidate_cache()
    return token_resolver


def _mock_whoami(monkeypatch, mapping):
    """Patch HfApi.whoami so that a known {token: result} mapping drives
    validity. `mapping[token]` may be a dict like {"name": "alice"} or an
    Exception instance to raise. Every call must target huggingface.co."""
    import huggingface_hub

    def _fake_whoami(self, token=None, **kwargs):
        assert self.endpoint == "https://huggingface.co", self.endpoint
        if token in mapping:
            outcome = mapping[token]
            if isinstance(outcome, Exception):
                raise outcome
            return outcome
        raise RuntimeError(f"unexpected token in whoami: {token!r}")

    monkeypatch.setattr(huggingface_hub.HfApi, "whoami", _fake_whoami)


def _set_cli_token(monkeypatch, value):
    from huggingface_hub import constants
    path = Path(constants.HF_TOKEN_PATH)
    if value is None:
        path.unlink(missing_ok=True)
    else:
        path.write_text(value, encoding="utf-8")


def test_resolve_priority_app_wins(fresh_resolver, monkeypatch):
    """All three sources set + valid → App wins."""
    tr = fresh_resolver
    from services import settings_store
    settings_store.set_hf_token(APP_TOKEN)
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, CLI_TOKEN)
    _mock_whoami(monkeypatch, {
        APP_TOKEN: {"name": "alice"},
        ENV_TOKEN: {"name": "bob"},
        CLI_TOKEN: {"name": "carol"},
    })
    tr.invalidate_cache()
    result = tr.resolve()
    assert result is not None
    assert result.token == APP_TOKEN
    assert result.source == "app"
    assert result.username == "alice"


def test_resolve_skips_empty_falls_to_env(fresh_resolver, monkeypatch):
    """No app token, env set, no CLI → Env wins."""
    tr = fresh_resolver
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {ENV_TOKEN: {"name": "bob"}})
    tr.invalidate_cache()
    result = tr.resolve()
    assert result is not None
    assert result.token == ENV_TOKEN
    assert result.source == "env"


def test_resolve_returns_none_when_all_empty(fresh_resolver, monkeypatch):
    tr = fresh_resolver
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {})
    tr.invalidate_cache()
    assert tr.resolve() is None


def test_resolve_skips_401_app_falls_to_env(fresh_resolver, monkeypatch):
    """App set but 401, env set and valid → resolver returns Env, not App.
    AUTH-06 mid-resolve fallback."""
    tr = fresh_resolver
    from services import settings_store
    from huggingface_hub.errors import HfHubHTTPError

    settings_store.set_hf_token(APP_TOKEN)
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, None)
    err = HfHubHTTPError("401 Unauthorized", response=MagicMock(status_code=401))
    _mock_whoami(monkeypatch, {
        APP_TOKEN: err,
        ENV_TOKEN: {"name": "bob"},
    })
    tr.invalidate_cache()
    result = tr.resolve()
    assert result is not None
    assert result.source == "env"
    assert result.token == ENV_TOKEN


def test_on_401_cascade(fresh_resolver, monkeypatch):
    """Active source = 'app', call on_401('app') → next valid source."""
    tr = fresh_resolver
    from services import settings_store
    settings_store.set_hf_token(APP_TOKEN)
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {
        APP_TOKEN: {"name": "alice"},
        ENV_TOKEN: {"name": "bob"},
    })
    tr.invalidate_cache()
    # Initial resolve picks app.
    assert tr.resolve().source == "app"
    # When the consumer reports a 401 from "app", fall back.
    fallback = tr.on_401("app")
    assert fallback is not None
    assert fallback.source == "env"


def test_state_returns_three_rows(fresh_resolver, monkeypatch):
    """state() returns one row per source in priority order, with masked
    token + whoami fields populated. The Settings UI consumes this."""
    tr = fresh_resolver
    from services import settings_store
    settings_store.set_hf_token(APP_TOKEN)
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {
        APP_TOKEN: {"name": "alice"},
        ENV_TOKEN: {"name": "bob"},
    })
    tr.invalidate_cache()
    s = tr.state(validate=True)
    assert "sources" in s
    assert "active" in s
    assert [r.source for r in s["sources"]] == ["app", "env", "hf-cli"]
    assert s["active"] == "app"
    # masked format: hf_…<last 3 chars of the token>
    app_row = s["sources"][0]
    assert app_row.set is True
    assert app_row.masked is not None and app_row.masked.startswith("hf_")
    assert app_row.masked.endswith(APP_TOKEN[-3:])
    assert app_row.whoami_user == "alice"
    assert app_row.whoami_ok is True
    # HF-CLI row: unset
    cli_row = s["sources"][2]
    assert cli_row.set is False
    assert cli_row.masked is None


def test_save_writes_both_store_and_hub_files(fresh_resolver, monkeypatch):
    """save_app_token() updates settings_store AND the local Hub token file,
    so engines and the hf CLI see the same token."""
    from huggingface_hub import constants
    tr = fresh_resolver
    _mock_whoami(monkeypatch, {APP_TOKEN: {"name": "alice"}})
    tr.save_app_token(APP_TOKEN)

    from services import settings_store
    assert settings_store.get_hf_token() == APP_TOKEN
    assert Path(constants.HF_TOKEN_PATH).read_text() == APP_TOKEN


def test_save_never_writes_a_git_credential(fresh_resolver, monkeypatch):
    """Pitfall #2: the token must never reach the user's git credentials."""
    from huggingface_hub import _login
    tr = fresh_resolver
    _mock_whoami(monkeypatch, {APP_TOKEN: {"name": "alice"}})
    monkeypatch.setattr(_login, "set_git_credential", lambda *a, **k: pytest.fail("git credential written"))
    captured = {}
    real = _login._set_active_token

    def _spy(token_name, add_to_git_credential):
        captured["add_to_git_credential"] = add_to_git_credential
        return real(token_name=token_name, add_to_git_credential=add_to_git_credential)

    monkeypatch.setattr(_login, "_set_active_token", _spy)
    tr.save_app_token(APP_TOKEN)
    assert captured == {"add_to_git_credential": False}


def test_invalid_token_is_not_written_to_hub_files(fresh_resolver, monkeypatch):
    from huggingface_hub import constants
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {APP_TOKEN: RuntimeError("401")})
    fresh_resolver.save_app_token(APP_TOKEN)
    from services import settings_store
    assert settings_store.get_hf_token() == APP_TOKEN
    assert not Path(constants.HF_TOKEN_PATH).exists()


def test_clear_app_token_removes_from_store(fresh_resolver, monkeypatch):
    tr = fresh_resolver
    _mock_whoami(monkeypatch, {APP_TOKEN: {"name": "alice"}})

    tr.save_app_token(APP_TOKEN)
    from services import settings_store
    assert settings_store.get_hf_token() == APP_TOKEN

    tr.clear_app_token(also_clear_hf_cli=False)
    assert settings_store.get_hf_token() is None


def test_clear_app_token_also_clears_files_when_requested(fresh_resolver, monkeypatch):
    from huggingface_hub import constants
    from services import settings_store
    settings_store.set_hf_token(APP_TOKEN)
    _set_cli_token(monkeypatch, CLI_TOKEN)
    stored = Path(constants.HF_STORED_TOKENS_PATH)
    stored.write_text("synthetic stored tokens", encoding="utf-8")
    fresh_resolver.clear_app_token(also_clear_hf_cli=True)
    assert settings_store.get_hf_token() is None
    assert not Path(constants.HF_TOKEN_PATH).exists()
    assert not stored.exists()


def test_resolve_accepts_hugging_face_hub_token_alias(fresh_resolver, monkeypatch):
    """HF docs list HUGGING_FACE_HUB_TOKEN as the legacy env-var name; the
    resolver must accept either."""
    tr = fresh_resolver
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.setenv("HUGGING_FACE_HUB_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, None)
    _mock_whoami(monkeypatch, {ENV_TOKEN: {"name": "bob"}})
    tr.invalidate_cache()
    result = tr.resolve()
    assert result is not None
    assert result.source == "env"
    assert result.token == ENV_TOKEN


def test_state_is_local_by_default(fresh_resolver, monkeypatch):
    tr = fresh_resolver
    monkeypatch.setattr(tr, "_READERS", {
        "app": lambda: APP_TOKEN, "env": lambda: ENV_TOKEN, "hf-cli": lambda: None,
    })
    def unexpected_validation(*args):
        raise AssertionError("local state must never validate against Hugging Face")
    monkeypatch.setattr(tr, "_validate", unexpected_validation)
    result = tr.state()
    assert result["active"] is None
    assert result["sources"][0].set
    assert result["sources"][0].whoami_ok is None
    assert APP_TOKEN not in repr(result)


def test_local_cli_reader_never_uses_environment_or_refresh(fresh_resolver, monkeypatch):
    import huggingface_hub
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    monkeypatch.setattr(huggingface_hub, "get_token", lambda: pytest.fail("must not refresh credentials"))
    _set_cli_token(monkeypatch, " \r\n" + CLI_TOKEN + "\n ")
    assert fresh_resolver._read_hf_cli() == CLI_TOKEN
    rows = {row.source: row for row in fresh_resolver.state()["sources"]}
    assert rows["env"].masked != rows["hf-cli"].masked
    assert all(row.whoami_ok is None for row in rows.values() if row.set)


def test_invalid_environment_falls_back_to_distinct_cli_file(fresh_resolver, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", ENV_TOKEN)
    _set_cli_token(monkeypatch, CLI_TOKEN)
    _mock_whoami(monkeypatch, {ENV_TOKEN: RuntimeError("invalid"), CLI_TOKEN: {"name": "cli-user"}})
    assert fresh_resolver.resolve().source == "hf-cli"


def test_environment_normalization_retains_correct_source(fresh_resolver, monkeypatch):
    monkeypatch.setenv("HF_TOKEN", " \r\n" + ENV_TOKEN + "\n ")
    _mock_whoami(monkeypatch, {ENV_TOKEN: {"name": "env-user"}})
    assert fresh_resolver.resolve().source == "env"
    assert fresh_resolver._read_hf_cli() is None


def test_save_and_clear_use_the_same_real_hub_files(fresh_resolver, monkeypatch):
    from huggingface_hub import constants
    _mock_whoami(monkeypatch, {APP_TOKEN: {"name": "test", "auth": {"accessToken": {"role": "read", "displayName": "synthetic"}}}})
    fresh_resolver.save_app_token(APP_TOKEN)
    assert Path(constants.HF_TOKEN_PATH).read_text() == APP_TOKEN
    assert Path(constants.HF_STORED_TOKENS_PATH).exists()
    assert fresh_resolver._read_hf_cli() == APP_TOKEN
    fresh_resolver.clear_app_token()
    assert fresh_resolver._read_hf_cli() == APP_TOKEN
    fresh_resolver.clear_app_token(also_clear_hf_cli=True)
    assert fresh_resolver._read_hf_cli() is None
    assert not Path(constants.HF_STORED_TOKENS_PATH).exists()


@pytest.mark.parametrize("value", ["", " \r\n "])
def test_blank_cli_file_is_not_a_source(fresh_resolver, monkeypatch, value):
    _set_cli_token(monkeypatch, value)
    assert fresh_resolver._read_hf_cli() is None


def test_cli_read_failure_does_not_expose_credentials(fresh_resolver, monkeypatch, caplog):
    def fail(*args, **kwargs):
        raise PermissionError("synthetic-secret-must-not-appear")
    monkeypatch.setattr(Path, "read_text", fail)
    assert fresh_resolver._read_hf_cli() is None
    assert "synthetic-secret-must-not-appear" not in caplog.text


def test_clear_attempts_all_files_and_reports_failure(fresh_resolver, monkeypatch):
    from core import config
    from huggingface_hub import constants
    selected = Path(constants.HF_TOKEN_PATH)
    legacy = selected.parent / "legacy" / "token"
    monkeypatch.setattr(config, "HF_CLI_TOKEN_PATHS", (str(selected), str(legacy)))
    for path in (selected, legacy):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(CLI_TOKEN)
        path.with_name("stored_tokens").write_text("synthetic")
    original_unlink = Path.unlink
    def unlink(path, *args, **kwargs):
        if path == selected:
            raise PermissionError("synthetic-secret-not-for-logs")
        return original_unlink(path, *args, **kwargs)
    monkeypatch.setattr(Path, "unlink", unlink)
    fresh_resolver._VALIDATION_CACHE[("hf-cli", "test")] = (0, "user")
    with pytest.raises(OSError, match="Could not clear all local Hugging Face token files") as error:
        fresh_resolver.clear_hf_cli_tokens()
    assert "synthetic-secret" not in str(error.value)
    assert selected.exists()
    assert not legacy.exists()
    assert not selected.with_name("stored_tokens").exists()
    assert not legacy.with_name("stored_tokens").exists()
    assert not fresh_resolver._VALIDATION_CACHE
