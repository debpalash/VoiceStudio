"""Fresh imports exercise Windows cache setup before Hub freezes token paths."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


@pytest.mark.parametrize("scenario", ["empty", "default", "legacy", "both", "explicit_token", "explicit_home", "explicit_cache", "explicit_app_cache", "xdg"])
def test_windows_token_path_matches_hub_and_preserves_existing_login(tmp_path, scenario):
    script = r'''
import json, os, sys
from pathlib import Path
root = Path(sys.argv[1]); scenario = sys.argv[2]
home = root / "user"
original_expanduser = os.path.expanduser
os.path.expanduser = lambda value: str(home) + value[1:] if value.startswith("~") else value
canonical = home / ".cache" / "huggingface" / "token"
legacy = root / "local" / "OmniVoice" / "hf_cache" / "token"
expected = canonical
if scenario in ("legacy", "both"):
    legacy.parent.mkdir(parents=True); legacy.write_text("synthetic-legacy")
    expected = legacy
if scenario in ("default", "both"):
    canonical.parent.mkdir(parents=True); canonical.write_text("synthetic-canonical")
    expected = canonical
if scenario == "explicit_token":
    os.environ["HF_TOKEN_PATH"] = "~/custom/token"; expected = home / "custom" / "token"
if scenario == "explicit_home":
    os.environ["HF_HOME"] = str(root / "custom-home"); expected = root / "custom-home" / "token"
if scenario == "explicit_cache":
    os.environ["HF_HUB_CACHE"] = str(root / "custom-cache")
if scenario == "explicit_app_cache":
    os.environ["OMNIVOICE_CACHE_DIR"] = str(root / "app-cache")
    # main.py applies the explicit app setting before importing core.config.
    os.environ["HF_HOME"] = os.environ["OMNIVOICE_CACHE_DIR"]
    os.environ["HF_HUB_CACHE"] = os.environ["OMNIVOICE_CACHE_DIR"]
    expected = root / "app-cache" / "token"
if scenario == "xdg":
    os.environ["XDG_CACHE_HOME"] = "~/xdg"; expected = home / "xdg" / "huggingface" / "token"
host = sys.platform; sys.platform = "win32"
from core import config
sys.platform = host
from huggingface_hub import constants
assert Path(constants.HF_TOKEN_PATH) == expected, (constants.HF_TOKEN_PATH, str(expected))
assert Path(constants.HF_STORED_TOKENS_PATH) == expected.parent / "stored_tokens"
# Real Hub persistence with only its remote identity request stubbed.
from huggingface_hub import hf_api
hf_api.HfApi.whoami = lambda self, token=None, **kw: {"name": "test", "auth": {"accessToken": {"role": "read", "displayName": "synthetic"}}}
from core import db
db.init_db()
from services import token_resolver as resolver
# Populate an unrelated old app location even for explicit overrides.
legacy.parent.mkdir(parents=True, exist_ok=True)
if not legacy.exists(): legacy.write_text("synthetic-old")
legacy.with_name("stored_tokens").write_text("[old]\nhf_token = synthetic-old\n")
resolver.save_app_token("hf_synthetic_saved")
assert expected.read_text() == "hf_synthetic_saved"
resolver.clear_app_token()
assert expected.exists(), "App-only clear must preserve CLI login"
resolver.clear_app_token(also_clear_hf_cli=True)
assert not expected.exists()
assert not expected.with_name("stored_tokens").exists()
if scenario.startswith("explicit_"):
    assert legacy.exists(), "Explicit override must not clear unrelated token files"
else:
    assert not canonical.exists() and not legacy.exists()
    assert not legacy.with_name("stored_tokens").exists()
    # Reinitialize the next startup's automatic selection after explicit clear.
    for key in ("HF_HOME", "HF_HUB_CACHE", "HF_TOKEN_PATH"):
        os.environ.pop(key, None)
    import importlib
    sys.platform = "win32"
    importlib.reload(config)
    sys.platform = host
    assert os.environ["HF_TOKEN_PATH"] != str(legacy), "Cleared legacy login must not resurrect"
print(json.dumps({"selected": str(expected)}))
'''
    env = {key: value for key, value in os.environ.items() if not key.startswith(("HF_", "HUGGING_FACE_", "OMNIVOICE_CACHE", "XDG_CACHE"))}
    env.update(HF_HUB_OFFLINE="1", OMNIVOICE_DATA_DIR=str(tmp_path / "app"), LOCALAPPDATA=str(tmp_path / "local"), PYTHONPATH=str(Path(__file__).resolve().parents[1] / "backend"))
    result = subprocess.run([sys.executable, "-c", script, str(tmp_path), scenario], env=env, capture_output=True, text=True, timeout=20)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["selected"]
