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

More than one app serves routes: the telephony gateway runs its own FastAPI app on
its own loopback listener, so main.app alone is not the whole surface. Every app
is walked and each record carries the ``app`` it belongs to, which is why the
output also lists the apps the census covered.
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


def route_records(app, app_name: str = "main") -> list[dict]:
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
        if path == "/" and not websocket and methods == ["GET"]:
            continue
        endpoint = getattr(r, "endpoint", None)
        records.append(
            {
                "app": app_name,
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


def load_apps() -> list[tuple[str, object]]:
    """Every app that serves routes, with the name the census reports for it.

    The telephony gateway (`api/routers/telephony_twilio.py`) builds a second
    FastAPI app for its own loopback listener, and its media-stream WebSocket lives
    there. Walking only `main.app` left that route outside the policy guard.
    """
    os.environ.setdefault("OMNIVOICE_MODEL", "test")
    os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")
    # Early-bind refactor: a bare (non-pytest) import defers routers behind
    # the startup gate — this dump needs the fully-built app at import.
    os.environ["OMNIVOICE_EAGER_INIT"] = "1"
    sys.path.insert(0, str(_REPO / "backend"))
    from main import app as main_app
    from api.routers.telephony_twilio import build_gateway_app

    return [("main", main_app), ("telephony", build_gateway_app())]


def load_app():
    """The main app alone, for callers that only need its routes.

    The census itself walks every app through `load_apps()`.
    """
    return load_apps()[0][1]


def main():
    routes: list[dict] = []
    apps: list[str] = []
    for name, app in load_apps():
        apps.append(name)
        routes.extend(route_records(app, name))
    routes.sort(key=lambda rec: (rec["path"], rec["methods"], rec["app"]))
    json.dump({"apps": apps, "routes": routes, "count": len(routes)}, sys.stdout)
    sys.stdout.write("\n")


if __name__ == "__main__":
    main()
