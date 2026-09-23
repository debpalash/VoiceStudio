"""Static policy guard for server-mode administrative routes.

The behavioural tests prove the dependencies themselves.  This file proves
the dangerous routers are actually wired to the strict dependency; testing a
perfect guard is worthless when a route imports the legacy one instead.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
ROUTERS = ROOT / "backend" / "api" / "routers"


def _tree(filename: str) -> ast.Module:
    return ast.parse((ROUTERS / filename).read_text(encoding="utf-8"))


def _router_assignment(tree: ast.Module) -> ast.expr:
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        if not any(
            isinstance(target, ast.Name) and target.id == "router"
            for target in node.targets
        ):
            continue
        return node.value
    raise AssertionError("router assignment not found")


def _route_decorators(tree: ast.Module, function_name: str) -> list[ast.expr]:
    for node in tree.body:
        if (
            isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
            and node.name == function_name
        ):
            return node.decorator_list
    raise AssertionError(f"route function not found: {function_name}")


def _dependency_names(nodes: ast.AST | list[ast.AST]) -> set[str]:
    roots = nodes if isinstance(nodes, list) else [nodes]
    names: set[str] = set()
    for root in roots:
        for node in ast.walk(root):
            if not isinstance(node, ast.Call) or not node.args:
                continue
            if not isinstance(node.func, ast.Name) or node.func.id != "Depends":
                continue
            dependency = node.args[0]
            if isinstance(dependency, ast.Name):
                names.add(dependency.id)
    return names


def _mutating_route_functions(
    tree: ast.Module,
) -> list[ast.FunctionDef | ast.AsyncFunctionDef]:
    mutating_methods = {"post", "put", "patch", "delete"}
    functions = []
    for node in tree.body:
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call):
                continue
            route = decorator.func
            if (
                isinstance(route, ast.Attribute)
                and isinstance(route.value, ast.Name)
                and route.value.id == "router"
                and route.attr in mutating_methods
            ):
                functions.append(node)
                break
    return functions


@pytest.mark.parametrize(
    "filename",
    [
        "calls.py",
        "mcp_bindings.py",
        "media_tools.py",
        "pronunciation.py",
        "settings.py",
        "system.py",
        "telephony_twilio.py",
        "workers.py",
    ],
)
def test_privileged_router_uses_method_aware_admin_guard(filename):
    dependencies = _dependency_names(_router_assignment(_tree(filename)))
    assert "require_admin" in dependencies
    assert "require_loopback" not in dependencies


def test_every_mutating_engine_route_uses_method_aware_admin_guard():
    functions = _mutating_route_functions(_tree("engines.py"))
    assert functions
    for function in functions:
        dependencies = _dependency_names(function.decorator_list)
        assert "require_admin" in dependencies, function.name
        assert "require_loopback" not in dependencies, function.name


def test_sidecar_install_status_uses_method_aware_admin_guard():
    dependencies = _dependency_names(
        _route_decorators(_tree("engines.py"), "sidecar_install_status")
    )
    assert "require_admin" in dependencies


def test_managed_sidecar_install_stays_desktop_only():
    dependencies = _dependency_names(
        _route_decorators(_tree("engines.py"), "install_sidecar_engine")
    )
    assert {"require_admin", "require_desktop"} <= dependencies


@pytest.mark.parametrize(
    ("filename", "function_name"),
    [
        ("engines.py", "engine_disk_usage"),
        ("engines.py", "engine_health"),
        ("settings.py", "list_llm_provider_models"),
        ("system.py", "system_diagnose"),
    ],
)
def test_side_effectful_get_requires_strict_admin_action(filename, function_name):
    dependencies = _dependency_names(_route_decorators(_tree(filename), function_name))
    assert "require_admin_action" in dependencies
