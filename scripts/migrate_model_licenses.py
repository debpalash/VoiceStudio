#!/usr/bin/env python3
"""One-way, offline v1 inventory migration; unknowns never become permission.

Use the v1 JSON and historical hf_revisions.py from the SAME source commit.
The output is a conservative starting point. Sidecar/SDK runtime pins and audit
observations require separate source verification; never infer them from evidence.
"""
from __future__ import annotations
import argparse
import ast
import copy
import json
from pathlib import Path


def migrate(registry: dict, runtime_source: str, source_commit: str) -> dict:
    if registry.get("schema_version") != 1:
        raise ValueError("Expected historical schema_version 1")
    nodes = ast.parse(runtime_source).body
    pins = next(ast.literal_eval(n.value) for n in nodes
                if isinstance(n, ast.AnnAssign) and isinstance(n.target, ast.Name)
                and n.target.id == "CURATED_REVISIONS")
    result = copy.deepcopy(registry)
    result.update(schema_version=2, registry_version="migration-draft", source_commit=source_commit)
    result["scope"] = ("Disclosures only. Ordinary downloads and first-use are unenforced. "
                       "A local acknowledgement grants no upstream or commercial permission.")
    ids = {r["id"] for r in result["models"]}
    if set(pins) - ids:
        raise ValueError("A runtime pin has no existing licence inventory record")
    for row in result["models"]:
        row.update(runtime_revision=pins.get(row["id"]),
                   runtime_pin_scope="central" if row["id"] in pins else "external",
                   runtime_pin_source="backend/services/hf_revisions.py" if row["id"] in pins else None,
                   component_closure="incomplete", upstream_access="unknown",
                   commercial_inference="restricted" if row["review_status"] == "noncommercial" else "unknown",
                   commercial_outputs="unknown", documents=[], artifacts=[], required_components=[])
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("runtime_source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--source-commit", required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; the migration never overwrites evidence")
    result = migrate(json.loads(args.source.read_text(encoding="utf-8")),
                     args.runtime_source.read_text(encoding="utf-8"), args.source_commit)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
