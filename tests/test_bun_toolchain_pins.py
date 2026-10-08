"""One JavaScript toolchain: the bun in package.json, one lockfile, one range per dependency.

CI, the security audit, and the Docker frontend build each drifted to their
own bun (unpinned, "1.2", `1-alpine`), a stale electron/bun.lock and a
standalone scripts/ package kept second dependency trees alive, and root and
Electron disagreed on TypeScript. Every consumer now follows package.json.
"""
from __future__ import annotations

import json
import re
import subprocess
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parents[1]
_WORKFLOWS = _ROOT / ".github" / "workflows"
_PACKAGE = json.loads((_ROOT / "package.json").read_text(encoding="utf-8"))


def _bun_version() -> str:
    manager = _PACKAGE["packageManager"]
    assert manager.startswith("bun@"), manager
    return manager.removeprefix("bun@")


def _tracked(pattern: str) -> list[str]:
    out = subprocess.run(
        ["git", "ls-files", "--", pattern], cwd=_ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line]


def _setup_bun_steps():
    for path in sorted(_WORKFLOWS.glob("*.yml")):
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        for job_name, job in (data.get("jobs") or {}).items():
            for step in job.get("steps") or []:
                if str(step.get("uses", "")).startswith("oven-sh/setup-bun@"):
                    yield f"{path.name}:{job_name}", step.get("with") or {}


def test_every_setup_bun_step_uses_the_package_manager_version():
    steps = list(_setup_bun_steps())
    assert steps, "expected at least one setup-bun step"
    for where, with_ in steps:
        if with_.get("bun-version-file") == "package.json":
            continue
        assert str(with_.get("bun-version")) == _bun_version(), (
            f"{where} must pin bun-version {_bun_version()!r} (package.json packageManager)"
        )


def test_docker_frontend_builder_uses_the_package_manager_version():
    dockerfile = (_ROOT / "deploy" / "Dockerfile").read_text(encoding="utf-8")
    images = re.findall(r"^FROM\s+oven/bun:(\S+)", dockerfile, flags=re.MULTILINE)
    assert images, "deploy/Dockerfile no longer builds with oven/bun"
    for image in images:
        tag = image.split("@", 1)[0]
        assert tag in (_bun_version(), f"{_bun_version()}-alpine"), (
            f"deploy/Dockerfile uses oven/bun:{image}; pin {_bun_version()}-alpine"
        )


def test_the_root_bun_lock_is_the_only_lockfile():
    locks = _tracked("*bun.lock") + _tracked("*bun.lockb")
    assert locks == ["bun.lock"], f"nested bun lockfiles drift from the workspace lock: {locks}"


def test_every_package_manifest_is_in_the_workspace():
    workspaces = set(_PACKAGE.get("workspaces", []))
    manifests = {str(Path(p).parent) for p in _tracked("*package.json")}
    assert manifests == {"."} | workspaces, (
        f"package.json files outside the bun workspace get their own dependency tree: "
        f"{sorted(manifests - {'.'} - workspaces)}"
    )


def test_workspace_manifests_agree_on_shared_dependency_ranges():
    def deps(manifest: dict) -> dict:
        merged: dict = {}
        for key in ("dependencies", "devDependencies", "optionalDependencies"):
            merged.update(manifest.get(key) or {})
        return merged

    root = deps(_PACKAGE)
    for workspace in _PACKAGE.get("workspaces", []):
        member = deps(json.loads((_ROOT / workspace / "package.json").read_text(encoding="utf-8")))
        mismatched = {
            name: (root[name], member[name]) for name in root.keys() & member.keys() if root[name] != member[name]
        }
        assert not mismatched, f"root and {workspace} declare different ranges: {mismatched}"


def test_the_required_test_job_runs_the_frontend_node_tests():
    """`bun run test:frontend` ran in no workflow, so its suites rotted unseen."""
    ci = yaml.safe_load((_WORKFLOWS / "ci.yml").read_text(encoding="utf-8"))
    job = next(j for j in ci["jobs"].values() if j.get("name") == "Tests (backend + frontend)")
    runs = [str(step.get("run", "")) for step in job["steps"]]
    assert any("bun run test:frontend" in run for run in runs)
    assert "test:frontend" in _PACKAGE["scripts"]
