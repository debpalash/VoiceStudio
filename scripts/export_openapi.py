"""Write the backend's OpenAPI schema to mobile/contract/openapi.json.

The iOS and Android apps generate their API clients from this file;
tests/test_mobile_contract.py fails when it drifts from the backend.

    uv run python scripts/export_openapi.py
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "mobile" / "contract" / "openapi.json"


def build_schema() -> dict:
    sys.path.insert(0, str(ROOT / "backend"))
    import main  # noqa: PLC0415 — backend import needs the sys.path above

    main._phase_a_build()
    main._phase_a_finalize()
    main.app.openapi_schema = None
    return main.app.openapi()


VERSION_PLACEHOLDER = "0.0.0"
_METHOD_SUFFIX = re.compile(r"_(get|head|post|put|patch|delete|options)$")


def render(schema: dict) -> str:
    # FastAPI derives multi-method operationIds from an unordered set, so a
    # GET+HEAD route gets one random, shared id. Pin each to its own method.
    for methods in schema.get("paths", {}).values():
        for method, op in methods.items():
            if isinstance(op, dict) and "operationId" in op:
                op["operationId"] = _METHOD_SUFFIX.sub("", op["operationId"]) + "_" + method
    # The app version leaks into info.version and field defaults. It differs by
    # environment (installed package metadata) and changes every release, so
    # it would make the contract churn; the apps read the version from /health.
    version = schema.get("info", {}).get("version")

    def neutral(node):
        if isinstance(node, dict):
            return {k: neutral(v) for k, v in node.items()}
        if isinstance(node, list):
            return [neutral(v) for v in node]
        return VERSION_PLACEHOLDER if version and node == version else node

    schema = neutral(schema)
    return json.dumps(schema, indent=1, sort_keys=True) + "\n"


if __name__ == "__main__":
    OUT.write_text(render(build_schema()), encoding="utf-8")
    print(f"wrote {OUT.relative_to(ROOT)}")
