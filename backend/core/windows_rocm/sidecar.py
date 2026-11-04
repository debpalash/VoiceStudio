"""Shared, model-free attestation checks for private Windows HIP environments."""

import ast
import importlib.util
import os
import re
from importlib import metadata
from pathlib import Path


def load_sibling(name: str):
    specification = importlib.util.spec_from_file_location(
        f"_windows_rocm_sidecar_{name}", Path(__file__).with_name(f"{name}.py"),
    )
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def validate_gpu_report(report: dict, python: Path, *, scope: str, recipe: dict,
                        recipe_sha256: str, packages: dict[str, str]) -> dict:
    executable = report.get("python") if isinstance(report, dict) else None
    requirements = (
        isinstance(report, dict),
        isinstance(executable, str) and os.path.normcase(os.path.abspath(executable))
        == os.path.normcase(os.path.abspath(python)),
    )
    if not all(requirements):
        raise RuntimeError("invalid_report")
    if not all((
        report.get("ready") is True,
        report.get("scope") == scope,
        report.get("architecture") == recipe["python"]["platform"],
        report.get("abi") == recipe["python"]["abi"],
        report.get("recipe_sha256") == recipe_sha256,
        report.get("packages") == packages,
        isinstance(report.get("hip"), str) and report["hip"].startswith(recipe["torch"]["hip_prefix"]),
        report.get("torch_gpu") is True,
        isinstance(report.get("gpu_architecture"), str)
        and report["gpu_architecture"] in recipe["gpu_architectures"],
        type(report.get("kernel_result")) in (int, float) and report["kernel_result"] == 4.0,
        report.get("kernel_device") == "cuda",
    )):
        raise RuntimeError("invalid_report")
    return report


def environment_matches(checkout: Path, packages: dict[str, str], hip: str) -> bool:
    configuration = (checkout / ".venv/pyvenv.cfg").read_text(encoding="utf-8")
    settings = dict(line.split("=", 1) for line in configuration.splitlines() if "=" in line)
    versions = {key.strip(): value.strip() for key, value in settings.items()}
    python_version = versions.get("version_info", versions.get("version", ""))
    if re.fullmatch(r"3[.]12(?:[.][0-9]+)?", python_version) is None:
        return False
    installed = checkout / ".venv/Lib/site-packages"
    by_name = {}
    for distribution in metadata.distributions(path=[str(installed)]):
        normalized = str((distribution.metadata or {}).get("Name", "")).lower().replace("_", "-")
        by_name.setdefault(normalized, []).append(distribution)
    for name, version in packages.items():
        normalized = name.lower().replace("_", "-")
        matches = by_name.get(normalized, [])
        if len(matches) != 1 or matches[0].version != version:
            return False
    assignments = {}
    for statement in ast.parse((installed / "torch/version.py").read_text(encoding="utf-8")).body:
        if isinstance(statement, ast.Assign):
            targets, value = statement.targets, statement.value
        elif isinstance(statement, ast.AnnAssign):
            targets, value = [statement.target], statement.value
        else:
            continue
        for target in targets:
            if isinstance(target, ast.Name) and target.id in {"__version__", "hip"}:
                assignments[target.id] = ast.literal_eval(value)
    return assignments.get("__version__") == packages["torch"] and assignments.get("hip") == hip
