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
        base: mod.uvicornArgs({{}}),
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
    assert observed["unset"] == [base[0], "--no-sync", *base[1:]]
    assert observed["auto"] == [base[0], "--no-sync", *base[1:]]
    assert observed["rocm"] == [base[0], "--no-sync", *base[1:]]


def test_windows_rocm_gfx_must_look_like_an_architecture():
    setup = _load_setup()
    assert setup._rocm_windows_gfx({}) is None
    assert setup._rocm_windows_gfx({"OMNIVOICE_ROCM_GFX": ""}) is None
    assert setup._rocm_windows_gfx({"OMNIVOICE_ROCM_GFX": "rx9060"}) is None
    assert setup._rocm_windows_gfx({"OMNIVOICE_ROCM_GFX": "gfx1200; calc"}) is None
    assert setup._rocm_windows_gfx({"OMNIVOICE_ROCM_GFX": " GFX1200 "}) == "gfx1200"


def test_windows_rocm_swaps_only_the_torch_trio_from_amds_index():
    setup = _load_setup()
    cmd = setup.rocm_windows_install_cmd("gfx1200", python="C:/venv/python.exe")
    # The repo's torch==2.8.0 constraints would reject AMD's build.
    assert "--no-config" in cmd
    assert cmd[cmd.index("--index-url") + 1] == "https://repo.amd.com/rocm/whl-multi-arch/"
    # Only torch, torchaudio and torchvision are reinstalled; every other
    # package keeps the lockfile's version (pillow and numpy stay put).
    reinstalled = [cmd[i + 1] for i, a in enumerate(cmd) if a == "--reinstall-package"]
    assert reinstalled == ["torch", "torchaudio", "torchvision"]
    assert "--reinstall" not in cmd
    assert cmd[-3:] == [
        "torch[device-gfx1200]==2.12.0+rocm7.14.1",
        "torchvision[device-gfx1200]==0.27.0+rocm7.14.1",
        "torchaudio==2.11.0+rocm7.14.1",
    ]


def test_windows_rocm_without_gfx_keeps_default_torch(capsys, monkeypatch):
    setup = _load_setup()
    calls = []
    monkeypatch.setattr(setup, "_installed_torch_is_rocm_isolated", lambda: False)
    monkeypatch.setattr(setup.subprocess, "check_call", lambda *a, **k: calls.append(a))
    setup._ensure_rocm_torch_windows({"OMNIVOICE_TORCH_VARIANT": "rocm"})
    assert calls == []
    assert "OMNIVOICE_ROCM_GFX" in capsys.readouterr().out


def test_windows_rocm_is_opt_in(monkeypatch):
    setup = _load_setup()
    calls = []
    monkeypatch.setattr(setup.subprocess, "check_call", lambda *a, **k: calls.append(a))
    setup._ensure_rocm_torch_windows({"OMNIVOICE_ROCM_GFX": "gfx1200"})
    setup._ensure_rocm_torch_windows(
        {"OMNIVOICE_TORCH_VARIANT": "auto", "OMNIVOICE_ROCM_GFX": "gfx1200"}
    )
    assert calls == []


def test_windows_rocm_probe_never_imports_torch_in_process(monkeypatch):
    """A torch loaded here would lock torch/_C*.pyd and break the uv reinstall."""
    import sys
    import types

    setup = _load_setup()
    seen = {}

    def fake_run(argv, **kwargs):
        seen["argv"] = argv
        return types.SimpleNamespace(returncode=0, stdout="1\n")

    monkeypatch.setattr(setup.subprocess, "run", fake_run)
    monkeypatch.delitem(sys.modules, "torch", raising=False)
    assert setup._installed_torch_is_rocm_isolated("C:/venv/python.exe") is True
    assert seen["argv"][0] == "C:/venv/python.exe" and seen["argv"][1] == "-c"
    assert "torch" not in sys.modules

    monkeypatch.setattr(
        setup.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=0, stdout="0\n"),
    )
    assert setup._installed_torch_is_rocm_isolated() is False
    monkeypatch.setattr(
        setup.subprocess, "run",
        lambda *a, **k: types.SimpleNamespace(returncode=1, stdout=""),
    )
    assert setup._installed_torch_is_rocm_isolated() is False

