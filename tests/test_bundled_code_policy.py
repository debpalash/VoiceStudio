"""Exercise the store provenance guard in separate interpreters."""
import os
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
import pytest


POLICY = Path(__file__).resolve().parents[1] / "backend/core/bundled_code.py"


def run_policy(code: str, enabled: bool = True, resources: Path | None = None, before: str = "") -> subprocess.CompletedProcess:
    setup = (
        "import importlib.util,sys,ctypes,subprocess; "
        f"spec=importlib.util.spec_from_file_location('policy',{str(POLICY)!r}); "
        "policy=importlib.util.module_from_spec(spec); spec.loader.exec_module(policy); "
        + before + "\npolicy.install(); "
    )
    env = {**os.environ, "VOICESTUDIO_BUNDLED_CODE_ONLY": "1" if enabled else "0",
           "VOICESTUDIO_BUNDLED_RESOURCES": str(resources or sys.base_prefix)}
    return subprocess.run([sys.executable, "-I", "-c", setup + code], env=env, capture_output=True, text=True)


def test_direct_build_keeps_existing_python_behavior():
    result = run_policy("exec(compile('answer=42','/unbundled/model.py','exec')); print(answer)", False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "42"


def test_policy_reports_its_active_state_on_repeat_calls():
    assert run_policy("print(policy.install())").stdout.strip() == "True"
    assert run_policy("print(policy.install())", False).stdout.strip() == "False"


def test_store_build_rejects_model_supplied_python_source():
    result = run_policy("exec(compile('raise AssertionError(\"executed\")','/unbundled/model.py','exec'))")
    assert result.returncode != 0
    assert "BUNDLED_CODE_POLICY" in result.stderr
    assert "AssertionError: executed" not in result.stderr


def test_store_build_allows_bundled_code_and_generated_python_expressions():
    result = run_policy("exec(compile('answer=42',sys.base_prefix+'/bundled.py','exec')); print(eval('answer+1'))")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "43"


def test_store_build_rejects_package_installer_subprocess_before_it_runs():
    result = run_policy("subprocess.run([sys.executable,'-m','pip','--version'],check=True)",
                        resources=Path(sys.executable).resolve().parent)
    assert result.returncode != 0
    assert "executable installation is store-managed" in result.stderr


def test_store_build_allows_stdlib_architecture_probe_using_system_file():
    result = run_policy("import platform; print(platform.architecture()[0])")
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() in {"32bit", "64bit"}


def test_store_build_blocks_shell_execution_before_it_runs():
    result = run_policy("import os; os.system('echo EXECUTED')")
    assert result.returncode != 0
    assert "BUNDLED_CODE_POLICY" in result.stderr
    assert "EXECUTED" not in result.stdout


@pytest.mark.skipif(os.name != "posix", reason="POSIX exec API")
def test_store_build_blocks_direct_exec_before_replacing_the_process():
    result = run_policy("import os; os.execv('/bin/echo',['echo','EXECUTED'])")
    assert result.returncode != 0
    assert "BUNDLED_CODE_POLICY" in result.stderr
    assert "EXECUTED" not in result.stdout


def test_store_rejects_even_identical_native_copies_outside_its_sealed_resources(tmp_path):
    import yaml._yaml
    source = Path(yaml._yaml.__file__)
    resources = tmp_path / "signed resources"
    runtime = resources / "python-runtime"
    runtime.mkdir(parents=True)
    bundled = runtime / source.name
    copied = tmp_path / source.name
    shutil.copy2(source, bundled)
    shutil.copy2(source, copied)
    (runtime / "manifest.json").write_text(json.dumps({
        "signedExecutables": [source.name],
        "files": {source.name: {"sha256": hashlib.sha256(bundled.read_bytes()).hexdigest()}},
    }))
    result = run_policy(f"ctypes.CDLL({str(copied)!r}); print('loaded')", resources=resources)
    assert result.returncode != 0
    assert "BUNDLED_CODE_POLICY: native library" in result.stderr


def test_encrypted_credentials_round_trip_with_the_bundled_policy_enabled(tmp_path):
    backend = POLICY.parents[1]
    before = f"""
import os
os.environ['OMNIVOICE_DATA_DIR'] = {str(tmp_path)!r}
sys.path.insert(0, {str(backend)!r})
from core import db
from services import settings_store, _secret_key
with db.db_conn() as connection:
    connection.execute('CREATE TABLE settings (key TEXT PRIMARY KEY, value TEXT, updated_at REAL)')
settings_store.set_secret('prewarm', 'test-only-prewarm')
assert settings_store.get_secret('prewarm') == 'test-only-prewarm'
_secret_key.invalidate()
"""
    result = run_policy("""
settings_store.set_hf_token('hf_test_only_not_a_credential')
settings_store.set_secret('provider.test', 'test-only-provider-value')
_secret_key.invalidate()
assert settings_store.get_hf_token() == 'hf_test_only_not_a_credential'
assert settings_store.get_secret('provider.test') == 'test-only-provider-value'
with db.db_conn() as connection:
    rows = connection.execute("SELECT value FROM settings WHERE key IN ('hf_token', 'secret.provider.test')").fetchall()
assert len(rows) == 2
assert all('test_only_not' not in row[0] and 'test-only-provider' not in row[0] for row in rows)
print('encrypted round trip passed')
""", before=before)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == 'encrypted round trip passed'
