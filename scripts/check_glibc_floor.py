#!/usr/bin/env python3
"""Fail when a bundled ELF needs a newer glibc than the oldest base we ship on.

The Snap base (core22) carries glibc 2.35 and Snapcraft Builds repackages the
release .deb onto it, so every native helper and bundled library must be built
where glibc <= 2.35 (#3879).

Usage: check_glibc_floor.py [--max 2.35] PATH...   (files or directories)
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

DEFAULT_MAX = "2.35"  # core22
_VERSION = re.compile(r"GLIBC_(\d+(?:\.\d+)+)")


def parse_version(text: str) -> tuple[int, ...]:
    return tuple(int(part) for part in text.split("."))


def required_glibc(objdump_output: str) -> tuple[int, ...] | None:
    """Highest GLIBC_x.y symbol version in `objdump -T` output (GLIBC_PRIVATE never matches)."""
    versions = [parse_version(m.group(1)) for m in _VERSION.finditer(objdump_output)]
    return max(versions) if versions else None


def is_elf(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            return handle.read(4) == b"\x7fELF"
    except OSError:
        return False


def iter_elfs(paths: list[Path]):
    for path in paths:
        files = sorted(p for p in path.rglob("*") if p.is_file() and not p.is_symlink()) if path.is_dir() else [path]
        yield from (f for f in files if is_elf(f))


def check(paths: list[Path], limit: str) -> list[str]:
    ceiling = parse_version(limit)
    problems = []
    for elf in iter_elfs(paths):
        result = subprocess.run(["objdump", "-T", str(elf)], capture_output=True, text=True, check=False)
        if result.returncode != 0:
            problems.append(f"{elf}: objdump could not inspect it (exit {result.returncode})")
            continue
        need = required_glibc(result.stdout)
        if need and need > ceiling:
            problems.append(f"{elf}: needs GLIBC_{'.'.join(map(str, need))} (max {limit})")
    return problems


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--max", default=DEFAULT_MAX)
    parser.add_argument("paths", nargs="+", type=Path)
    args = parser.parse_args()
    missing = [p for p in args.paths if not p.exists()]
    if missing:
        print(f"error: not found: {', '.join(map(str, missing))}", file=sys.stderr)
        return 2
    count = sum(1 for _ in iter_elfs(args.paths))
    if not count:
        print("error: no ELF files found to check", file=sys.stderr)
        return 2
    problems = check(args.paths, args.max)
    for line in problems:
        print(line, file=sys.stderr)
    if not problems:
        print(f"{count} ELF file(s) need glibc <= {args.max}")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
