"""Run package layout contracts as part of the normal repository suite."""
from pathlib import Path
import subprocess


def test_unified_cli_package_layouts():
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run(['node', '--test', 'electron/tests/cli-bundle.test.mjs',
                             'electron/tests/deb-scripts.test.mjs'], cwd=root,
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
