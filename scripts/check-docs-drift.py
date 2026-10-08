#!/usr/bin/env python3
"""Diff the canonical feature inventory against README, docs, and registries.

The inventory (``docs/features.yaml``) is the curated truth. This checker
verifies, without importing any backend module (the engine registries pull
torch transitively, which the docs-drift CI runner does not have):

  1. every ``features[]`` name appears verbatim in README.md or its explicitly linked catalog;
  2. ``tts_engines[].id`` is exactly the set of registry keys parsed from
     ``backend/services/tts_backend.py`` (eager ``_REGISTRY`` + lazy
     ``_LAZY_REGISTRY``), both directions;
  3. ``asr_engines[].id`` likewise against ``backend/services/asr_backend.py``;
  4. every ``readme:`` string appears in README.md or its linked catalog;
  5. every ``doc:`` / ``docs[]`` file exists.

Exit 0 = no drift. Exit 1 = drift; findings go to stderr and, with
``--output``, to a Markdown report consumed by the rolling-issue automation
in ``.github/workflows/docs-drift.yml``.

Runs daily and on dispatch, not per PR; nothing else checks this inventory,
so run it locally when a change adds or renames a feature or engine.
Rolling-issue pattern adapted from Patter (MIT) — see
docs/competitive-analysis.md, Patter deep dive 4.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

import yaml

# Markers locating the registry dicts whose keys we extract. Each marker is
# matched at a line start; the block ends at the first line that is exactly
# ``}`` or ``})`` (the registries are flat string-keyed dict literals).
_TTS_MARKERS = ("_LAZY_REGISTRY: dict[str, tuple[str, str]] = {",
                "_REGISTRY: dict[str, type[TTSBackend]] = _LazyRegistry({")
_ASR_MARKERS = ("_REGISTRY: dict[str, type[ASRBackend]] = _LazyASRRegistry({",)

_KEY_RE = re.compile(r'^\s*"([^"]+)"\s*:')


def _registry_ids(source: str, markers: tuple[str, ...], *, path: str) -> set[str]:
    """Parse string keys out of the dict literal(s) following each marker."""
    ids: set[str] = set()
    lines = source.splitlines()
    for marker in markers:
        try:
            start = next(i for i, ln in enumerate(lines) if ln.strip() == marker.strip())
        except StopIteration:
            raise SystemExit(
                f"check-docs-drift: marker not found in {path}: {marker!r} — "
                "the registry layout changed; update _TTS_MARKERS/_ASR_MARKERS."
            )
        for ln in lines[start + 1:]:
            stripped = ln.strip()
            if stripped in ("}", "})"):
                break
            if stripped.startswith("#"):
                continue
            m = _KEY_RE.match(ln)
            if m:
                ids.add(m.group(1))
    return ids


def _check(root: Path) -> list[str]:
    drifts: list[str] = []

    inv_path = root / "docs" / "features.yaml"
    if not inv_path.exists():
        return [f"`{inv_path.relative_to(root)}` is missing"]
    inv = yaml.safe_load(inv_path.read_text(encoding="utf-8")) or {}

    readme = (root / "README.md").read_text(encoding="utf-8")
    # Keep the complete inventory in a linked catalog when the README is compact.
    catalog = inv.get("catalog")
    if catalog:
        if f"]({catalog})" not in readme:
            drifts.append(f"README.md must link to catalog `{catalog}`")
        catalog_path = root / catalog
        if catalog_path.is_file():
            readme += "\n" + catalog_path.read_text(encoding="utf-8")
        else:
            drifts.append(f"catalog `{catalog}` does not exist")


    # 1. Features present in README.
    for name in inv.get("features", []):
        if name not in readme:
            drifts.append(f"feature `{name}` is in the inventory but not in README.md")

    # 2–4. Engine ids vs registries; readme strings; per-engine docs.
    for section, src_rel, markers in (
        ("tts_engines", "backend/services/tts_backend.py", _TTS_MARKERS),
        ("asr_engines", "backend/services/asr_backend.py", _ASR_MARKERS),
    ):
        entries = inv.get(section, [])
        inv_ids = {e["id"] for e in entries}
        code_ids = _registry_ids(
            (root / src_rel).read_text(encoding="utf-8"), markers, path=src_rel
        )
        for missing in sorted(code_ids - inv_ids):
            drifts.append(
                f"engine `{missing}` exists in `{src_rel}` but not in the "
                f"`{section}` inventory — document it (or list it deliberately)"
            )
        for gone in sorted(inv_ids - code_ids):
            drifts.append(
                f"engine `{gone}` is in the `{section}` inventory but no longer "
                f"in `{src_rel}` — remove it from the inventory and docs"
            )
        for entry in entries:
            readme_name = entry.get("readme")
            if readme_name and readme_name not in readme:
                drifts.append(
                    f"engine `{entry['id']}`: expected `{readme_name}` in README.md"
                )
            doc = entry.get("doc")
            if doc and not (root / doc).exists():
                drifts.append(f"engine `{entry['id']}`: doc `{doc}` does not exist")

    # 5. Required docs exist.
    for doc in inv.get("docs", []):
        if not (root / doc).exists():
            drifts.append(f"required doc `{doc}` does not exist")

    return drifts


def _report(drifts: list[str], checked: int) -> str:
    lines = ["# Docs drift report", ""]
    if drifts:
        lines.append(f"{len(drifts)} mismatch(es) between `docs/features.yaml`, "
                     "README.md, docs/, and the engine registries:")
        lines.append("")
        lines += [f"- {d}" for d in drifts]
        lines.append("")
        lines.append("Fix by updating the docs **or** the inventory — whichever is "
                     "stale. This issue updates in place and closes automatically "
                     "when the nightly check is clean.")
    else:
        lines.append(f"No drift — {checked} inventory entries verified.")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None, root: Path | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=None,
                        help="repo root (default: parent of this script's dir)")
    parser.add_argument("--output", type=Path, default=None,
                        help="write a Markdown report to this path")
    args = parser.parse_args([] if argv is None else argv)

    repo = args.root or root or Path(__file__).resolve().parents[1]
    drifts = _check(repo)

    inv = yaml.safe_load((repo / "docs" / "features.yaml").read_text(encoding="utf-8")) \
        if (repo / "docs" / "features.yaml").exists() else {}
    checked = sum(len(inv.get(k, [])) for k in ("features", "tts_engines", "asr_engines", "docs"))

    if args.output:
        args.output.write_text(_report(drifts, checked), encoding="utf-8")

    if drifts:
        for d in drifts:
            print(f"docs-drift: {d}", file=sys.stderr)
        print(f"check-docs-drift: {len(drifts)} drift(s) across {checked} "
              "inventory entries.", file=sys.stderr)
        return 1

    print(f"OK — {checked} inventory entries verified against README, docs, "
          "and engine registries.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
