from __future__ import annotations

import ast
import re
import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
STACK = {"torch", "torchaudio", "torchvision"}


def _constraints():
    with (ROOT / "pyproject.toml").open("rb") as handle:
        project = tomllib.load(handle)
    pins = {}
    for item in project["tool"]["uv"]["constraint-dependencies"]:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(.+)", item)
        if match:
            pins[match.group(1)] = match.group(2)
    return pins


def _python_assignment(path, name):
    text = path.read_text(encoding="utf-8")
    tree = ast.parse(text)
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            if any(isinstance(target, ast.Name) and target.id == name for target in targets):
                return ast.literal_eval(node.value)
    raise AssertionError(f"{path}: assignment {name} was renamed or removed")


def _typescript_assignment(name):
    path = ROOT / "electron/src/main/runtime-project.ts"
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"export const {name}\s*=\s*(\[[\s\S]*?\]|['\"][^'\"]+['\"])", text
    )
    assert match, f"{path}: export {name} was renamed or removed"
    raw = re.sub(r"\bas const\b", "", match.group(1)).strip()
    return ast.literal_eval(raw)


def _pin_map(items):
    result = {}
    for item in items:
        match = re.fullmatch(r"([A-Za-z0-9_.-]+)==(.+)", item)
        assert match, f"expected exact package pin, got {item!r}"
        result[match.group(1)] = match.group(2)
    return result


def test_electron_rocm_pins_match_project_constraints():
    constraints = _constraints()
    electron = _pin_map(_typescript_assignment("ROCM_TORCH_PINS"))
    assert {name: electron.get(name) for name in STACK} == {
        name: constraints.get(name) for name in STACK
    }


def test_project_constraints_pin_the_complete_torch_stack():
    constraints = _constraints()
    assert STACK <= constraints.keys()


def test_setup_rocm_pins_match_project_constraints():
    constraints = _constraints()
    setup = _pin_map(_python_assignment(ROOT / "scripts/setup.py", "ROCM_TORCH_PINS"))
    failures = []
    for name in sorted(STACK):
        expected = constraints.get(name)
        actual = setup.get(name)
        if expected is None:
            failures.append(f"{name}: no version in project constraints")
        elif actual is None:
            failures.append(f"{name}: not installed by ROCm setup")
        elif actual != expected:
            failures.append(f"{name}: setup has {actual}, constraints require {expected}")
    assert not failures, "\n".join(failures)


def test_sidecar_rocm_pins_match_their_project_constraints():
    constraints = _constraints()
    sidecar = _pin_map(_python_assignment(
        ROOT / "backend/core/torch_indexes.py", "ROCM_TORCH_PINS"
    ))
    assert sidecar, "sidecar ROCM_TORCH_PINS must not be empty"
    assert {name: constraints.get(name) for name in sidecar} == sidecar


def test_all_rocm_index_copies_match():
    setup_index = _python_assignment(ROOT / "scripts/setup.py", "ROCM_TORCH_INDEX")
    sidecar_index = _python_assignment(
        ROOT / "backend/core/torch_indexes.py", "PYTORCH_ROCM_INDEX_URL"
    )
    electron_index = _typescript_assignment("ROCM_TORCH_INDEX")
    assert sidecar_index == setup_index == electron_index


def test_setup_reinstalls_the_complete_stack_together():
    setup = _pin_map(_python_assignment(ROOT / "scripts/setup.py", "ROCM_TORCH_PINS"))
    assert STACK <= setup.keys()


def test_rocm_and_torch_versions_meet_the_rdna4_floor():
    index = _python_assignment(
        ROOT / "backend/core/torch_indexes.py", "PYTORCH_ROCM_INDEX_URL"
    )
    match = re.search(r"/whl/rocm(\d+)\.(\d+)$", index)
    assert match, "sidecar ROCm index must end with /whl/rocm<major>.<minor>"
    assert tuple(map(int, match.groups())) >= (6, 4)
    torch_version = tuple(map(int, _constraints()["torch"].split(".")[:2]))
    assert torch_version >= (2, 8)
