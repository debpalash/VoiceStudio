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


def _run(tmp_path, home, *args, data_dir=None, cache_dir=None, cwd=None, extra_env=None):
    env = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("XDG_", "OMNIVOICE_", "HF_"))
    }
    env.update(
        {
            "HOME": str(home),
            "OMNIVOICE_DATA_DIR": str(data_dir or (tmp_path / "data-nonexistent")),
            "OMNIVOICE_CACHE_DIR": str(cache_dir or (tmp_path / "models-nonexistent")),
        }
    )
    env.update(extra_env or {})
    return subprocess.run(
        ["bash", SH, *args], capture_output=True, text=True, env=env, timeout=60, cwd=cwd
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


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("target", ["home", "parent-of-home"])
def test_sh_refuses_a_deletion_target_it_did_not_create(tmp_path, target):
    """An override can name the home directory or a parent of it (#4436).

    OMNIVOICE_DATA_DIR is honoured verbatim, so without the guard `--yes` turns
    "uninstall VoiceStudio" into "delete my home folder".
    """
    home, user_data, _, _ = _layout(tmp_path)
    named = home if target == "home" else home.parent

    applied = _run(tmp_path, home, "--yes", data_dir=named)
    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert home.exists(), f"{target} was deleted"
    # The app's own folders are still found and removed.
    assert not user_data.exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
def test_sh_refuses_to_delete_the_home_directory_as_a_model_cache(tmp_path):
    home, _, _, _ = _layout(tmp_path)
    applied = _run(tmp_path, home, "--yes", "--models", cache_dir=home)
    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert home.exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("branch", ["data", "models"])
def test_sh_resolves_a_relative_target_even_when_cdpath_is_set(tmp_path, branch):
    """An exported CDPATH makes `cd` echo the directory it picked.

    The captured target then held two lines, missed the home comparison, and `--yes`
    deleted the home directory. Both deletion branches resolve a target, so both are
    covered.
    """
    home, _, _, _ = _layout(tmp_path)
    extra = {"cwd": tmp_path, "extra_env": {"CDPATH": "."}}
    if branch == "data":
        applied = _run(tmp_path, home, "--yes", data_dir="home", **extra)
    else:
        applied = _run(tmp_path, home, "--yes", "--models", cache_dir="home", **extra)

    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert home.exists(), f"the {branch} branch deleted the home directory"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("branch", ["data", "models"])
def test_sh_treats_a_dash_target_as_a_literal_directory(tmp_path, branch):
    """`cd -` visits OLDPWD, so a directory named "-" must not resolve elsewhere.

    With the target resolved through OLDPWD, the guard compared unrelated paths and
    allowed the deletion of the literal "-" directory that contained the home
    directory.
    """
    check = tmp_path / "check"
    home = check / "-" / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "keep.txt").write_text("keep", encoding="utf-8")
    (check / "VoiceStudio" / "project").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    extra = {"cwd": check, "extra_env": {"OLDPWD": str(elsewhere)}}

    if branch == "data":
        applied = _run(tmp_path, home, "--yes", data_dir="-", **extra)
    else:
        applied = _run(tmp_path, home, "--yes", "--models", cache_dir="-", **extra)

    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert (home / "Documents" / "keep.txt").exists(), (
        f"the {branch} branch deleted the literal '-' directory holding home"
    )


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("branch", ["data", "models"])
def test_sh_refuses_a_target_that_carries_a_line_break(tmp_path, branch):
    """A newline-suffixed target is refused, not trimmed into a sibling path."""
    home, _, _, _ = _layout(tmp_path)
    sibling = tmp_path / "data"
    (sibling / "keep.txt").parent.mkdir(parents=True)
    (sibling / "keep.txt").write_text("keep", encoding="utf-8")
    named = tmp_path / "data\n"
    named.mkdir()

    if branch == "data":
        applied = _run(tmp_path, home, "--yes", data_dir=str(named))
    else:
        applied = _run(tmp_path, home, "--yes", "--models", cache_dir=str(named))

    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert (sibling / "keep.txt").exists(), "the sibling directory was removed instead"
    assert named.exists(), "the named directory was removed despite the refusal"


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
@pytest.mark.parametrize("branch", ["data", "models"])
def test_sh_refuses_a_newline_suffixed_parent_of_home(tmp_path, branch):
    """The parent of home is refused even when its name ends in a newline."""
    parent = tmp_path / "parent\n"
    home = parent / "home"
    (home / "Documents").mkdir(parents=True)
    (home / "Documents" / "keep.txt").write_text("keep", encoding="utf-8")

    if branch == "data":
        applied = _run(tmp_path, home, "--yes", data_dir=str(parent))
    else:
        applied = _run(tmp_path, home, "--yes", "--models", cache_dir=str(parent))

    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert (home / "Documents" / "keep.txt").exists(), (
        f"the {branch} branch deleted the parent of home"
    )
    assert parent.exists()


@pytest.mark.skipif(sys.platform.startswith("win"), reason="bash script is macOS/Linux-only")
def test_sh_resolves_home_before_the_first_deletion(tmp_path):
    """A deleted app folder can hold the symlink that names home.

    Resolving home per call compared the model cache against a link that the first
    deletion had already removed, so the real home directory was allowed through.
    """
    real_home = tmp_path / "real" / "home"
    (real_home / "Documents").mkdir(parents=True)
    (real_home / "Documents" / "keep.txt").write_text("keep", encoding="utf-8")
    app = tmp_path / "app"
    app.mkdir()
    link = app / "home_link"
    os.symlink(real_home, link)

    applied = _run(tmp_path, link, "--yes", "--models", data_dir=app, cache_dir=real_home)

    assert applied.returncode == 0, applied.stderr
    assert "Refusing to remove" in applied.stderr
    assert (real_home / "Documents" / "keep.txt").exists()
    assert not app.exists(), "the app folder itself should still be removed"


def test_both_scripts_check_a_target_before_removing_it():
    with open(SH, encoding="utf-8") as handle:
        sh = handle.read()
    with open(PS1, encoding="utf-8-sig") as handle:
        ps1 = handle.read()

    assert "is_unsafe_delete_root() {" in sh
    assert "resolve_delete_target() {" in sh
    # With CDPATH exported, `cd` prints the directory it picked and the captured
    # target carries two lines, which would miss every comparison.
    assert "unset CDPATH" in sh
    # `cd -` visits OLDPWD, so a relative operand is prefixed before it is resolved.
    assert "./$path" in sh
    # The slash strip runs in place: command substitution would also drop a trailing
    # newline and delete a different directory than the one that was named.
    assert "strip_trailing_slashes" not in sh
    assert 'while [ "${t%/}" != "$t" ]' in sh
    assert 'while [ "${MODELS_DIR%/}" != "$MODELS_DIR" ]' in sh
    # Home is resolved once, before any deletion can remove a symlink that names it.
    assert "RESOLVED_HOME=$(resolve_delete_target \"${HOME:-}\")" in sh
    assert sh.count('resolve_delete_target "${HOME:-}"') == 1
    # A path with a line break is refused before either deletion branch compares it.
    assert sh.index('has_line_break "$t"') < sh.index('rm -rf -- "$t"')
    assert sh.index('has_line_break "$MODELS_DIR"') < sh.index('rm -rf -- "$MODELS_DIR"')
    assert sh.index('while [ "${t%/}" != "$t" ]') < sh.index('rm -rf -- "$t"')
    assert sh.index('is_unsafe_delete_root "$t"') < sh.index('rm -rf -- "$t"')
    assert sh.index('is_unsafe_delete_root "$MODELS_DIR"') < sh.index('rm -rf -- "$MODELS_DIR"')

    assert "function Resolve-DeleteTarget" in ps1
    assert "function Test-UnsafeDeleteRoot" in ps1
    # The path that is validated is the path that is removed, resolved once through
    # PowerShell's provider location.
    assert "$target = Resolve-DeleteTarget $t" in ps1
    assert "Test-UnsafeDeleteRoot $target" in ps1
    assert "$modelsTarget = Resolve-DeleteTarget $modelsDir" in ps1
    assert "Test-UnsafeDeleteRoot $modelsTarget" in ps1
    assert ps1.index("Test-UnsafeDeleteRoot $target") < ps1.index("Remove-Item -LiteralPath $target")
    assert ps1.index("Test-UnsafeDeleteRoot $modelsTarget") < ps1.index("Remove-Item -LiteralPath $modelsTarget")


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
