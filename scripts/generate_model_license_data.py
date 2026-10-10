#!/usr/bin/env python3
"""Generate disclosure data from the existing registry and YAML catalogue."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
from services.model_licenses import disclosure, digest, load_registry  # noqa: E402


def generate(root: Path = ROOT) -> dict:
    registry = load_registry(root / "backend/config/model_licenses.json")
    catalog = yaml.safe_load((root / "backend/config/models.yaml").read_text(encoding="utf-8"))
    offered = {m["repo_id"] for m in catalog["models"]}
    return {
        "schema_version": 1, "registry_version": registry["registry_version"],
        "registry_digest": digest(registry), "inventory_source_commit": registry["source_commit"],
        "checked_at": registry["checked_at"], "scope": registry["scope"],
        "enforcement": "disclosure_only",
        "models": [{"id": row["id"], "engines": row.get("engines", []),
                    "catalogued": row["id"] in offered,
                    **disclosure(row["id"], registry)} for row in registry["models"]],
        "dynamic_assets": registry["dynamic_assets"],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    output = ROOT / "docs/licensing/model-license-data.json"
    content = json.dumps(generate(), ensure_ascii=False, indent=2) + "\n"
    if args.check:
        if not output.exists() or output.read_text(encoding="utf-8") != content:
            print("Model licensing projection is stale; run scripts/generate_model_license_data.py")
            return 1
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content, encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
