#!/usr/bin/env python3
"""Dump the backend route surface together with its authorization guards.

`scripts/dump_api_routes.py` answers *which routes exist*. This answers *which
routes are guarded, and by what*: for every route it walks the FastAPI dependant
tree and records the authorization callables attached to it (router-level
``dependencies=[...]``, route-level ``dependencies=[...]``, and ``Depends(...)``
in the endpoint signature, transitively through sub-dependencies).

It is the input for `tests/test_route_authorization_policy.py`, the class-level
guard that fails any *mutating* route shipping with no authorization dependency
and no explicit allowlist entry.

    OMNIVOICE_MODEL=test uv run python scripts/dump_route_auth.py

Writes exactly one JSON object to stdout::

    {"routes": [{"path", "methods", "websocket", "auth_dependencies",
                 "endpoint", "module"}], "count": N}

`methods` is a sorted list of HTTP methods (HEAD/OPTIONS dropped; Starlette adds
them automatically) and is empty for WebSocket routes. `websocket` says how to
read that. `auth_dependencies` holds the subset of the recognized authorization
call names below that guard the route, sorted. `endpoint`/`module` are the
endpoint function's `__name__`/`__module__` (the module is the last-resort
way to find where an unguarded route is declared).

Like the inventory dump, `Mount` routes (StaticFiles / sub-app mounts, present
only when their directory exists) and the read-only bare ``GET /`` frontend
fallback are excluded, so the output is identical between a dev checkout and a
fresh runner. A *mutating* route at ``/`` is kept: it is a real surface, and
dropping it would let an unguarded ``POST /`` ship unnoticed.
"""
import json
import os
import sys
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]

# The authorization gates that decide whether a caller may reach a route.
# Anything not in this set (a plain DB session, a settings object, ...) is not
# an authorization decision and is deliberately ignored here.
AUTH_CALL_NAMES = frozenset(
    {
        "require_admin",
        "require_admin_action",
        "require_admin_media_action",
        "require_loopback",
        "require_local",
        "require_native_access",
        "require_desktop",
        "require_consumer",
        "require_consumer_reads_admin_writes",
        "check_admin",
        "require_call_control",
    }
)


def auth_dependencies(route) -> list[str]:
    """Sorted authorization call names guarding ``route``, transitively.

    Router-level ``dependencies=[...]`` land in ``route.dependant.dependencies``
    exactly like route-level ones, and each dependant carries its own children,
    so a single recursive walk covers both levels.
    """
    found: set[str] = set()

    def walk(dependant) -> None:
        for sub in getattr(dependant, "dependencies", None) or ():
            name = getattr(getattr(sub, "call", None), "__name__", None)
            if name in AUTH_CALL_NAMES:
                found.add(name)
            walk(sub)

    walk(getattr(route, "dependant", None))
    return sorted(found)


def route_records(app) -> list[dict]:
    """One record per HTTP/WebSocket route on ``app``, sorted for a stable diff."""
    from starlette.routing import Mount

    records = []
    for r in app.routes:
        path = getattr(r, "path", None)
        if not path or isinstance(r, Mount):
            continue
        websocket = "WebSocket" in type(r).__name__
        raw_methods = getattr(r, "methods", None)
        if raw_methods:
            methods = sorted(m for m in raw_methods if m not in ("HEAD", "OPTIONS"))
        elif websocket:
            methods = []
        else:
            continue  # non-HTTP, non-WS, non-mount: not part of the API surface
        # The bare ``GET /`` frontend fallback is environment-dependent and not
        # part of the API contract. Only the read-only form is dropped: a
        # mutating route at ``/`` is a real surface and must stay visible to the
        # policy test.
        if path == "/" and not set(methods) & {"POST", "PUT", "PATCH", "DELETE"}:
            continue
        endpoint = getattr(r, "endpoint", None)
        records.append(
            {
                "path": path,
                "methods": methods,
                "websocket": websocket,
                "auth_dependencies": auth_dependencies(r),
                "endpoint": getattr(endpoint, "__name__", None),
                "module": getattr(endpoint, "__module__", None),
            }
        )
    records.sort(key=lambda rec: (rec["path"], rec["methods"]))
    return records


def load_app():
    os.environ.setdefault("OMNIVOICE_MODEL", "test")
    os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")
    # Early-bind refactor: a bare (non-pytest) import defers routers behind
    # the startup gate — this dump needs the fully-built app at import.
    os.environ["OMNIVOICE_EAGER_INIT"] = "1"
    sys.path.insert(0, str(_REPO / "backend"))
    from main import app
    return app


def main():
    routes = route_records(load_app())
    json.dump({"routes": routes, "count": len(routes)}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
