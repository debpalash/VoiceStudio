"""Source installs must honour `OMNIVOICE_TORCH_VARIANT=rocm` (#1665).

`uv sync` always restores the lockfile's CUDA torch build, which is CPU-only
on AMD cards, and `uv run` re-syncs before every launch. These tests pin the
source flow: `scripts/setup.py` reinstalls the ROCm wheel after the sync, and
`scripts/dev-backend.mjs` launches with `uv run --no-sync` so it sticks.
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess

_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_SETUP = os.path.join(_ROOT, "scripts", "setup.py")
_DEV_BACKEND = os.path.join(_ROOT, "scripts", "dev-backend.mjs")


def _load_setup():
    spec = importlib.util.spec_from_file_location("vs_setup", _SETUP)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_rocm_opt_in_requires_explicit_variant():
    setup = _load_setup()
    assert setup._rocm_opt_in({}) is None
    assert setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "auto"}) is None
    assert setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "ROCm"}) == setup.ROCM_TORCH_INDEX
    assert (
        setup._rocm_opt_in({"OMNIVOICE_TORCH_VARIANT": "rocm", "OMNIVOICE_TORCH_INDEX": "https://x/"})
        == "https://x/"
    )


def test_rocm_reinstall_targets_this_venv_with_pinned_stack():
    setup = _load_setup()
    cmd = setup.rocm_torch_reinstall_cmd("https://idx/", python="/venv/bin/python")
    assert cmd == [
        "uv",
        "pip",
        "install",
        "--reinstall",
        "--python",
        "/venv/bin/python",
        *setup.ROCM_TORCH_PINS,
        "--index-url",
        "https://idx/",
    ]
    assert setup.ROCM_TORCH_PINS == (
        "torch==2.8.0",
        "torchaudio==2.8.0",
        "torchvision==0.23.0",
    )


def test_dev_backend_skips_resync_when_rocm_requested():
    module_uri = Path(_DEV_BACKEND).as_uri()
    script = f"""
      const mod = await import({json.dumps(module_uri)});
      console.log(JSON.stringify({{
        base: mod.UVICORN_ARGS,
        unset: mod.uvRunArgs({{}}),
        auto: mod.uvRunArgs({{ OMNIVOICE_TORCH_VARIANT: "auto" }}),
        rocm: mod.uvRunArgs({{ OMNIVOICE_TORCH_VARIANT: " ROCm " }}),
      }}));
    """
    completed = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        check=True,
        capture_output=True,
        text=True,
    )
    observed = json.loads(completed.stdout)
    base = observed["base"]
    assert observed["unset"] == base
    assert observed["auto"] == base
    assert observed["rocm"] == [base[0], "--no-sync", *base[1:]]
