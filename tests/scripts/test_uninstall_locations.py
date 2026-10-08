"""The uninstall scripts must find the Electron app's folders, not only the
retired Tauri ones.

The Electron shell keeps its managed Python runtime, window state, logs and
updater cache under its VoiceStudio app folder (app.getPath('userData'),
electron/src/main/backend.ts). A script that only knew the Tauri identifier
left gigabytes behind on every Electron uninstall.

uninstall.sh runs for real against a throwaway HOME; uninstall.ps1 is pinned by
static checks because PowerShell is not available on every CI platform.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys

import pytest

REPO = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
SH = os.path.join(REPO, "scripts", "uninstall.sh")
PS1 = os.path.join(REPO, "scripts", "uninstall.ps1")


def _layout(tmp_path):
    home = tmp_path / "home"
    if platform.system() == "Darwin":
        user_data = home / "Library/Application Support/VoiceStudio"
        logs = home / "Library/Logs/VoiceStudio"
        legacy = home / "Library/Application Support/com.debpalash.omnivoice-studio"
    else:
        user_data = home / ".config/VoiceStudio"
        logs = user_data / "logs"
        legacy = home / ".local/share/com.debpalash.omnivoice-studio"
    for path in (user_data / "runtime/project/.venv", logs, legacy / "project"):
        path.mkdir(parents=True, exist_ok=True)
    return home, user_data, logs, legacy


def _run(tmp_path, home, *args):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("XDG_", "OMNIVOICE_", "HF_"))
    }
    env.update(
        {
            "HOME": str(home),
            "OMNIVOICE_DATA_DIR": str(tmp_path / "data-nonexistent"),
            "OMNIVOICE_CACHE_DIR": str(tmp_path / "models-nonexistent"),
        }
    )
    return subprocess.run(
        ["bash", SH, *args], capture_output=True, text=True, env=env, timeout=60
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
def test_sh_removes_electron_and_legacy_tauri_folders(tmp_path):
    home, user_data, logs, legacy = _layout(tmp_path)
    owned = tmp_path / "fast-disk" / "VoiceStudio"
    (owned / "project/.venv").mkdir(parents=True)
    (user_data / "runtime-location.json").write_text(json.dumps({"root": str(owned), "owned": True}))

    dry = _run(tmp_path, home)
    assert dry.returncode == 0, dry.stderr
    for path in (user_data, owned, legacy):
        assert str(path) in dry.stdout
    assert user_data.exists() and owned.exists(), "the dry run must delete nothing"

    applied = _run(tmp_path, home, "--yes")
    assert applied.returncode == 0, applied.stderr
    assert not user_data.exists()
    assert not owned.exists()
    assert not legacy.exists()
    assert not logs.exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize(
    "location",
    [
        {"owned": False},  # a reused Tauri environment is never Electron's to delete
        {"owned": True, "name": "Python"},  # not a folder the app created
    ],
)
def test_sh_keeps_runtime_roots_the_app_does_not_own(tmp_path, location):
    home, user_data, _, _ = _layout(tmp_path)
    root = tmp_path / "elsewhere" / location.get("name", "VoiceStudio")
    root.mkdir(parents=True)
    (user_data / "runtime-location.json").write_text(
        json.dumps({"root": str(root), "owned": location["owned"]})
    )

    applied = _run(tmp_path, home, "--yes")
    assert applied.returncode == 0, applied.stderr
    assert root.exists()
    assert not user_data.exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("make_file", [
    # Damaged: an owned flag and an unrelated root spread over separate lines.
    lambda root: '{\n  "owned": true,\n  "other": {"root": "%s"}\n}' % root,
    lambda root: '{"root": "/somewhere/else", "owned": true}\n{"root": "%s"}' % root,
    # Well formed, but the folder holds no app project.
    lambda root: json.dumps({"root": root, "owned": True}),
])
def test_sh_ignores_damaged_or_unverified_runtime_records(tmp_path, make_file):
    home, user_data, _, _ = _layout(tmp_path)
    root = tmp_path / "unrelated" / "VoiceStudio"
    (root / "keep-me").mkdir(parents=True)
    (user_data / "runtime-location.json").write_text(make_file(str(root)))

    applied = _run(tmp_path, home, "--yes")
    assert applied.returncode == 0, applied.stderr
    assert (root / "keep-me").exists()


def test_scripts_no_longer_cite_the_removed_tauri_backend():
    for path in (SH, PS1):
        with open(path, encoding="utf-8-sig") as handle:
            assert "src-tauri" not in handle.read()


def test_ps1_targets_electron_folders_and_its_uninstaller():
    with open(PS1, encoding="utf-8-sig") as handle:
        text = handle.read()
    assert "$electronAppName = 'VoiceStudio'" in text
    assert "Join-Path $appData $electronAppName" in text
    assert "$electronUpdaterCache = 'voicestudio-electron-updater'" in text
    assert "runtime-location.json" in text and "$location.owned -eq $true" in text
    assert "(Join-Path $root 'project') -PathType Container" in text
    assert "'Uninstall VoiceStudio.exe'" in text
    # App removal is refused before cleanup when the caller is elevated.
    # Behavioral coverage lives in test_uninstall_elevation.py.
    assert "if ($Yes -and $isElevated)" in text
    assert "without administrator rights" in text
    assert "Test-AdminOnlyWritable" not in text
    # The legacy Tauri cleanup stays.
    assert "$legacyIdentifier = 'com.debpalash.omnivoice-studio'" in text
