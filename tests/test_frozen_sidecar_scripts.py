"""Frozen desktop bundles must expose sidecar entrypoints as real files."""
from __future__ import annotations

import ast
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_backend_bundle_copies_engine_sidecar_scripts() -> None:
    """Private engine interpreters cannot execute modules stored only in PYZ."""
    tree = ast.parse((ROOT / "backend.spec").read_text(encoding="utf-8"))
    bundled_roots: set[tuple[str, str]] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.List, ast.Tuple)):
            continue
        for item in node.elts:
            if not isinstance(item, ast.Tuple) or len(item.elts) != 2:
                continue
            try:
                source, destination = (ast.literal_eval(part) for part in item.elts)
            except (TypeError, ValueError):
                continue
            if isinstance(source, str) and isinstance(destination, str):
                bundled_roots.add((source.replace("\\", "/"), destination.replace("\\", "/")))

    sidecar_scripts = tuple((ROOT / "backend" / "engines").glob("*/main.py"))
    assert sidecar_scripts, "expected at least one engine sidecar entrypoint"
    assert ("backend/engines", "engines") in bundled_roots, (
        "backend.spec must copy backend/engines to engines: managed venvs execute "
        "these main.py files outside PyInstaller's embedded interpreter"
    )
