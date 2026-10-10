#!/usr/bin/env python3
"""Check model-licence inventory completeness without network or ML imports.

This validates recorded evidence, not legal clearance. ``commercial_use`` means
reviewed permission for this distribution; false also covers unresolved terms.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
from pathlib import Path
from urllib.parse import urlparse

import yaml

_REPO_ID = re.compile(r"[A-Za-z0-9][\w.-]*/[\w.-]+")
# Native program downloads are audited separately; these are not model weights.
_PROGRAM_REPOS = {"0xShug0/audio.cpp", "zackees/ffmpeg_bins"}
# A local checkout subdirectory and an SDK alias also match org/model syntax.
_LOCAL_MODEL_PATHS = {"pretrained_models/Fun-CosyVoice3-0.5B"}
_MODEL_ALIASES = {
    "moonshine/base": "UsefulSensors/moonshine-base",
    "moonshine/tiny": "UsefulSensors/moonshine-tiny",
}


def source_model_ids(root):
    """Find literal repository defaults without importing optional engines.

    Covers named repo/model defaults, repository keyword arguments and literal
    from_pretrained calls. SDK-generated identifiers and arbitrary user models
    remain unresolved asset families, not silently cleared wildcard records.
    """
    found = set()
    sources = (root / "backend", root / "omnivoice/models")
    for path in (path for directory in sources for path in directory.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            value = None
            if isinstance(node, ast.keyword) and node.arg in {
                "repo_id", "weights_repo_id", "model_id", "model_name", "model"
            }:
                value = node.value
            elif isinstance(node, (ast.Assign, ast.AnnAssign)):
                targets = node.targets if isinstance(node, ast.Assign) else [node.target]
                if any(isinstance(target, ast.Name) and (
                    re.search(r"REPO|MODEL|CHECKPOINT", target.id.upper())
                    or target.id == "CURATED_REVISIONS"
                ) for target in targets):
                    value = node.value
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                  and node.func.attr == "from_pretrained" and node.args):
                value = node.args[0]
            elif isinstance(node, ast.Call) and len(node.args) >= 2:
                # Defaults can be assigned to an arbitrary local variable; the
                # environment access still identifies their configured value.
                function = node.func
                is_getenv = (isinstance(function, ast.Attribute)
                             and isinstance(function.value, ast.Name)
                             and function.value.id == "os" and function.attr == "getenv")
                is_environ_get = (isinstance(function, ast.Attribute) and function.attr == "get"
                                  and isinstance(function.value, ast.Attribute)
                                  and function.value.attr == "environ"
                                  and isinstance(function.value.value, ast.Name)
                                  and function.value.value.id == "os")
                if is_getenv or is_environ_get:
                    value = node.args[1]
            if value is not None:
                for leaf in ast.walk(value):
                    if (isinstance(leaf, ast.Constant) and isinstance(leaf.value, str)
                            and _REPO_ID.fullmatch(leaf.value)):
                        found.add(leaf.value)
    return {_MODEL_ALIASES.get(rid, rid)
            for rid in found - _PROGRAM_REPOS - _LOCAL_MODEL_PATHS}


def catalog_ids(value):
    """Include transitive pipeline dependencies, not only top-level models."""
    if isinstance(value, dict):
        if isinstance(value.get("repo_id"), str):
            yield value["repo_id"]
        for child in value.values():
            yield from catalog_ids(child)
    elif isinstance(value, list):
        for child in value:
            yield from catalog_ids(child)


def validate(registry, catalog, source_ids=()):
    errors = []
    if not isinstance(registry, dict) or registry.get("schema_version") != 2:
        return ["Expected model licence inventory schema_version 2"]
    ids = set()
    for group in ("models", "dynamic_assets"):
        records = registry.get(group)
        if not isinstance(records, list) or not records:
            errors.append(f"Missing {group} records")
            continue
        for record in records:
            if not isinstance(record, dict):
                errors.append(f"Invalid {group} record")
                continue
            rid = record.get("id")
            if not isinstance(rid, str) or not rid.strip():
                errors.append(f"Missing {group} record id")
                continue
            if rid in ids:
                errors.append(f"Duplicate model record: {rid}")
            ids.add(rid)
            for field in ("license", "credit", "source_url", "evidence_url", "notes"):
                if not isinstance(record.get(field), str) or not record[field].strip():
                    errors.append(f"{rid}: missing {field}")
            for field in ("source_url", "evidence_url"):
                url = record.get(field)
                if isinstance(url, str):
                    parsed = urlparse(url)
                    if parsed.scheme != "https" or not parsed.netloc:
                        errors.append(f"{rid}: {field} must be an HTTPS source")
            status = record.get("review_status")
            if status not in {"unreviewed", "noncommercial", "cleared"}:
                errors.append(f"{rid}: invalid review_status")
            allowed = record.get("commercial_use")
            if type(allowed) is not bool:
                errors.append(f"{rid}: commercial_use must be boolean")
            elif allowed != (status == "cleared"):
                errors.append(f"{rid}: commercial_use requires cleared review_status")
            if group == "dynamic_assets" and (allowed or status != "unreviewed"):
                errors.append(f"{rid}: dynamic commercial_use needs individual resolved asset records")
            if status == "cleared":
                for field in ("reviewed_by", "reviewed_at", "review_scope", "revision"):
                    if not isinstance(record.get(field), str) or not record[field].strip():
                        errors.append(f"{rid}: cleared record needs {field}")
    # The original source/catalogue scan remains intact; v2 adds exact-evidence
    # shape checks without claiming SDK closure from static source inspection.
    import sys
    backend = str(Path(__file__).resolve().parents[1] / "backend")
    if backend not in sys.path:
        sys.path.insert(0, backend)
    from services.model_licenses import _component, ModelTermsError
    for record in registry.get("models", []) if isinstance(registry.get("models"), list) else []:
        if not isinstance(record, dict) or not isinstance(record.get("id"), str):
            continue
        rid = record["id"]
        revision = record.get("runtime_revision")
        if revision is not None and (not isinstance(revision, str) or not re.fullmatch(r"[0-9a-f]{40}", revision)):
            errors.append(f"{rid}: invalid runtime_revision")
        if record.get("runtime_pin_scope") == "central" and revision is None:
            errors.append(f"{rid}: central pin missing")
        if record.get("component_closure") not in {"complete", "incomplete"}:
            errors.append(f"{rid}: invalid component_closure")
        for field in ("commercial_inference", "commercial_outputs"):
            if record.get(field) not in {"unknown", "restricted"}:
                errors.append(f"{rid}: unsupported {field} assessment")
        if record.get("data_kind", "production") != "production":
            errors.append(f"{rid}: test/illustrative data is not production evidence")
        try:
            _component(record)
        except (ModelTermsError, KeyError, TypeError) as exc:
            errors.append(f"{rid}: invalid exact evidence ({type(exc).__name__})")
    records = registry.get("models")
    model_ids = {record["id"] for record in records if isinstance(record, dict)
                 and isinstance(record.get("id"), str)} if isinstance(records, list) else set()
    for rid in sorted((set(catalog_ids(catalog)) | set(source_ids)) - model_ids):
        errors.append(f"Missing model record: {rid}")
    return errors


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args()
    config = args.root / "backend/config"
    sys_path = str(args.root / "backend")
    import sys
    sys.path.insert(0, sys_path)
    from services.model_licenses import load_registry
    registry = load_registry(config / "model_licenses.json")
    catalog = yaml.safe_load((config / "models.yaml").read_text(encoding="utf-8"))
    errors = validate(registry, catalog, source_model_ids(args.root))
    if errors:
        print("\n".join(errors))
        return 1
    print(f"Model licence records valid: {len(registry['models'])} models, "
          f"{len(registry['dynamic_assets'])} unresolved asset families")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

