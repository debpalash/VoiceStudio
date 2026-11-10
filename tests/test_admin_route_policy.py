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
    ["mcp_bindings.py", "media_tools.py", "pronunciation.py", "telephony_twilio.py", "settings.py", "system.py"],
)
def test_privileged_router_uses_method_aware_admin_guard(filename):
    dependencies = _dependency_names(_router_assignment(_tree(filename)))
    assert "require_admin" in dependencies
    assert "require_loopback" not in dependencies


def test_gallery_router_uses_consumer_for_reads():
    """Gallery reads stay reachable for PIN/trusted-network consumers; only
    mutations escalate to require_admin (the router-level dependency is
    require_consumer, not require_admin)."""
    dependencies = _dependency_names(_router_assignment(_tree("gallery.py")))
    assert "require_consumer" in dependencies
    assert "require_admin" not in dependencies
    assert "require_loopback" not in dependencies


@pytest.mark.parametrize(
    "function_name",
    [
        "delete_voice",
        "search_youtube",
        "download_youtube_clip",
        "upload_voice_clip",
        "save_voice_as_profile",
        "update_voice",
        "batch_delete_voices",
        "voice_to_profile",
    ],
)
def test_gallery_mutating_routes_require_admin(function_name):
    dependencies = _dependency_names(
        _route_decorators(_tree("gallery.py"), function_name)
    )
    assert "require_admin" in dependencies, function_name
    assert "require_loopback" not in dependencies, function_name


@pytest.mark.parametrize("filename", ['audiobook.py', 'batch.py', 'capture.py', 'describe_voice.py', 'dub_generate.py', 'dub_translate.py', 'glossary.py', 'marketplace.py', 'personas.py', 'profiles.py', 'project_bundles.py', 'projects.py', 'stories.py', 'voice_convert.py', 'watermark.py'])
def test_content_router_keeps_consumer_reads_and_admin_writes(filename):
    dependencies = _dependency_names(_router_assignment(_tree(filename)))
    assert "require_consumer_reads_admin_writes" in dependencies
    assert "require_loopback" not in dependencies


# (router file, function) pairs a paired phone / consumer calls to run work.
_CONSUMER_GENERATION_ROUTES = [
    ("dub_translate.py", "dub_translate"),
    ("dub_translate.py", "dub_agent_fit"),
    ("dub_generate.py", "dub_generate"),
    ("dub_generate.py", "preview_segment"),
    ("batch.py", "enqueue_batch_job"),
    ("batch.py", "cancel_batch_job"),
    ("batch.py", "retry_batch_job"),
    ("voice_convert.py", "convert_speech"),
    ("capture.py", "transcribe_audio"),
    ("describe_voice.py", "describe_voice"),
    ("audiobook.py", "audiobook_synthesize"),
    ("audiobook.py", "longform_render"),
    ("profiles.py", "create_profile"),
]
# Editing / deleting stored content stays admin-only.
_ADMIN_ONLY_ROUTES = [
    ("profiles.py", "update_profile"),
    ("profiles.py", "delete_profile"),
    ("profiles.py", "replace_profile_audio"),
    ("batch.py", "delete_batch_job"),
    ("projects.py", "create_project"),
    ("projects.py", "delete_project"),
    ("glossary.py", "delete_term"),
    ("marketplace.py", "remove_from_marketplace"),
    ("project_bundles.py", "import_project_bundle"),
    ("personas.py", "import_persona"),
    ("watermark.py", "update_watermark_settings"),
]


def _is_consumer_generation(filename: str, function_name: str) -> bool:
    decorators = _route_decorators(_tree(filename), function_name)
    return any(
        isinstance(d, ast.Name) and d.id == "consumer_generation" for d in decorators
    )


@pytest.mark.parametrize("filename,function_name", _CONSUMER_GENERATION_ROUTES)
def test_phone_generation_routes_are_consumer_allowed(filename, function_name):
    assert _is_consumer_generation(filename, function_name)


@pytest.mark.parametrize("filename,function_name", _ADMIN_ONLY_ROUTES)
def test_content_edit_and_delete_routes_stay_admin_only(filename, function_name):
    assert not _is_consumer_generation(filename, function_name)


def test_consumer_generation_guard_behaviour(monkeypatch):
    """A consumer token runs marked generation POSTs and is refused on
    unmarked edit/delete routes of the same router."""
    from fastapi import APIRouter, Depends, FastAPI
    from fastapi.testclient import TestClient

    from api import dependencies as deps

    def consumer_ok(request):
        return None

    def admin_refused(request):
        raise deps.HTTPException(status_code=403, detail="admin required")

    monkeypatch.setattr(deps, "require_consumer", consumer_ok)
    monkeypatch.setattr(deps, "check_admin", admin_refused)

    router = APIRouter(dependencies=[Depends(deps.require_consumer_reads_admin_writes)])

    @router.get("/thing")
    def read():
        return {"ok": True}

    @router.post("/thing/run")
    @deps.consumer_generation
    def run():
        return {"ok": True}

    @router.put("/thing/{item}")
    def edit(item: str):
        return {"ok": True}

    @router.delete("/thing/{item}")
    def remove(item: str):
        return {"ok": True}

    app = FastAPI()
    app.include_router(router)
    client = TestClient(app)
    assert client.get("/thing").status_code == 200
    assert client.post("/thing/run").status_code == 200
    assert client.put("/thing/x").status_code == 403
    assert client.delete("/thing/x").status_code == 403


def test_openai_compat_router_uses_consumer_gate():
    """The /v1 audio routes are the documented consumption surface for PIN and
    trusted-network clients, so the router stays on require_consumer rather
    than require_admin."""
    dependencies = _dependency_names(_router_assignment(_tree("openai_compat.py")))
    assert "require_consumer" in dependencies
    assert "require_admin" not in dependencies
    assert "require_loopback" not in dependencies


def test_profile_audio_materialization_requires_admin_action():
    """Rendering a pending design sample inside GET /profiles/{id}/audio is an
    admin action: require_admin_media_action must guard the branch that
    synthesizes and persists ref_audio_path."""
    for node in _tree("profiles.py").body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == "get_profile_audio":
            calls = {
                child.func.id
                for child in ast.walk(node)
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name)
            }
            assert "require_admin_media_action" in calls, calls
            return
    raise AssertionError("get_profile_audio not found")


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


def test_calls_pairing_exception_does_not_expose_settings():
    tree = _tree("calls.py")
    assert "require_call_control" in _dependency_names(_router_assignment(tree))
    for function in ("get_settings", "put_settings"):
        assert "require_admin" in _dependency_names(_route_decorators(tree, function))
