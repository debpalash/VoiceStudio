"""The install smoke must run for the files the installer installs.

`install-smoke.yml` installs a release and then the pull request's own source as
`main`, so it builds the product. Its trigger list used to name only the installer
scripts and their test files, which meant a change to the package manifests, the
lockfiles, the Electron builder config, the modules that config imports, or the
runtime setup helpers could break the install while every other job stayed green.
This test keeps the list equal to the inputs that build feeds on.
"""
from __future__ import annotations

import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
WORKFLOW = ROOT / ".github" / "workflows" / "install-smoke.yml"
BUILDER_CONFIG = ROOT / "electron" / "electron-builder.config.mjs"

# Every file the install job reads or builds: the installer scripts, the runtime
# setup helpers, the manifests and lockfiles, the Electron packaging config and
# the modules it imports, the bundled CLI and the backend entrypoint.
REQUIRED_INPUTS = {
    "scripts/install.sh",
    "scripts/install.ps1",
    "scripts/setup-api.mjs",
    "scripts/setup.py",
    "scripts/cli_install_smoke.py",
    "package.json",
    "bun.lock",
    "uv.lock",
    "pyproject.toml",
    "config/**",
    "store/**",
    "electron/build/**",
    "electron/package.json",
    "electron/electron-builder.config.mjs",
    "electron/native-helper-build.mjs",
    "electron/cli-bundle.mjs",
    "electron/omnivoice-tts-bundle.mjs",
    "electron/mas-runtime-bundle.mjs",
    "electron/snap-template-payload.mjs",
    "electron/packaging/**",
    "electron/scripts/**",
    "native/desktop-bridge/**",
    "cli/**",
    "backend/main.py",
    "README.md",
    "LICENSE",
    "LICENSE-NOTICE.md",
    "scripts/windows-rocm-recipe.json",
    "scripts/smoke_windows_rocm.py",
    "electron/*vite*.config.ts",
    "electron/T3CODE-LICENSE.txt",
}

# `extraResources` entries that ship the whole application payload. A change in
# them is a runtime concern the backend and Electron jobs already cover; the smoke
# is about whether the installer produces a working installation at all.
PAYLOAD_TREES = {
    "../backend": "the backend payload, covered by the runtime jobs",
    "../omnivoice": "the engine package payload, covered by the runtime jobs",
    "../frontend/dist": "the built web bundle, produced by the build itself",
}


def _workflow() -> dict:
    data = yaml.safe_load(WORKFLOW.read_text(encoding="utf-8"))
    return data["on"] if "on" in data else data[True]


def _covers(paths: set[str], target: str) -> bool:
    """Does a filter list cover `target`? Handles `**`, `*` and a plain prefix."""
    for entry in paths:
        if entry == target:
            return True
        if entry.endswith("/**") and (target == entry[:-3] or target.startswith(entry[:-3] + "/")):
            return True
        if "*" in entry and re.fullmatch(re.escape(entry).replace(r"\*\*", ".*").replace(r"\*", "[^/]*"), target):
            return True
    return False


def test_the_smoke_triggers_on_every_installed_input():
    triggers = _workflow()
    paths = set(triggers["pull_request"]["paths"])
    missing = sorted(REQUIRED_INPUTS - paths)
    assert not missing, (
        "install-smoke.yml would not run for these installed inputs, so a break in them "
        f"reaches main unchecked: {missing}"
    )


def test_the_packaging_modules_the_builder_imports_are_covered():
    """`electron-builder.config.mjs` is only the entry: its inputs do the work.

    Both shapes count: `import ... from './x.mjs'` and `resolve(here, 'build/Info.plist')`.
    A file the builder reads is as much an install input as a module it imports.
    """
    triggers = _workflow()
    paths = set(triggers["pull_request"]["paths"])
    text = BUILDER_CONFIG.read_text(encoding="utf-8")
    here = BUILDER_CONFIG.parent

    inputs: set[str] = set()
    for relative in re.findall(r"""from\s+['"](\.[^'"]+)['"]""", text):
        inputs.add((here / relative).resolve().relative_to(ROOT).as_posix())
    for relative in re.findall(r"""resolve\(here,\s*['"]([^'"]+)['"]\)""", text):
        inputs.add((here / relative).resolve().relative_to(ROOT).as_posix())

    uncovered = sorted(entry for entry in inputs if not _covers(paths, entry))
    assert not uncovered, (
        "the builder config reads files whose changes would not trigger the smoke: "
        f"{uncovered}"
    )


def test_the_builder_extras_are_covered():
    """Every `extraResources` source the installer copies is an install input."""
    triggers = _workflow()
    paths = set(triggers["pull_request"]["paths"])
    text = BUILDER_CONFIG.read_text(encoding="utf-8")
    here = BUILDER_CONFIG.parent

    uncovered: list[str] = []
    for relative in re.findall(r"""from:\s*['"]([^'"]+)['"]""", text):
        if relative in PAYLOAD_TREES:
            continue
        resolved = (here / relative).resolve()
        if not resolved.exists():
            continue  # a build output that the build creates
        uncovered.extend(
            entry
            for entry in [resolved.relative_to(ROOT).as_posix()]
            if not _covers(paths, entry)
        )
    assert not uncovered, (
        "the builder copies resources whose changes would not trigger the smoke: "
        f"{sorted(set(uncovered))}"
    )


def test_both_triggers_watch_the_same_paths():
    """A path added for pull requests has to cover main's own pushes too."""
    triggers = _workflow()
    assert set(triggers["pull_request"]["paths"]) == set(triggers["push"]["paths"])


def test_the_referenced_inputs_exist():
    """A filter that names a file nobody has any more silently stops matching."""
    triggers = _workflow()
    for entry in triggers["pull_request"]["paths"]:
        if entry.endswith("/**"):
            assert (ROOT / entry[:-3]).is_dir(), f"{entry} does not exist"
            continue
        if "*" in entry:
            parent = ROOT / Path(entry).parent
            assert parent.is_dir(), f"{entry} has no directory"
            assert list(parent.glob(Path(entry).name)), f"{entry} matches no file"
            continue
        assert (ROOT / entry).is_file(), f"{entry} does not exist"