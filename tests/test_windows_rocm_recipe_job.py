"""The no-GPU recipe job installs only pytest, PyYAML and NumPy.

Every test module it lists must therefore pass without the app's import graph;
one that reaches `services.*` (FastAPI, Starlette, Torch) belongs to the main
CI job instead. Run the job's own file list here with those packages hidden.
"""
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github/workflows/windows-rocm-recipe.yml"
# Installed for the main suite but absent from the recipe job.
HIDDEN = ("fastapi", "starlette", "pydantic", "torch", "torchaudio", "soundfile", "httpx", "uvicorn")


def _listed_modules():
    text = WORKFLOW.read_text(encoding="utf-8")
    step = text.split("name: Windows ROCm recipe tests", 1)[1].split("- name:", 1)[0]
    return re.findall(r"tests/test_windows_rocm_\w+\.py", step)


def test_recipe_job_modules_pass_without_app_dependencies():
    modules = _listed_modules()
    assert modules, "recipe job no longer lists its test modules"
    hide = "; ".join(f"sys.modules[{name!r}] = None" for name in HIDDEN)
    code = f"import sys; {hide}; import pytest; sys.exit(pytest.main(sys.argv[1:]))"
    env = {**os.environ, "PYTHONPATH": "backend", "PYTHONUTF8": "1"}
    result = subprocess.run(
        [sys.executable, "-c", code, *modules, "-q", "--tb=line", "-p", "no:cacheprovider", "-p", "no:xdist"],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=300, check=False,
    )
    assert result.returncode == 0, result.stdout[-4000:] + result.stderr[-2000:]
