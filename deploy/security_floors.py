"""Security floors for the `uv pip install` paths (Docker and the Colab notebook).

`uv pip install .` applies `deploy/security-constraints.txt` only to packages
in the project's own resolution. A package a base image already ships that
nothing here requires (msgpack 1.1.1 in `pytorch/pytorch`) is left below its
floor, so the install paths upgrade what this script reports, then check.

    python3 deploy/security_floors.py requirements   # floors to install, one per line
    python3 deploy/security_floors.py check          # fail if any installed package is below its floor

Packages that are not installed are skipped: a floor is not a dependency.
"""
from __future__ import annotations

import importlib.metadata as md
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name

CONSTRAINTS = Path(__file__).with_name("security-constraints.txt")


def floors(path: Path = CONSTRAINTS) -> list[Requirement]:
    """Floors from the constraints file that apply to this interpreter (markers evaluated)."""
    reqs = [
        Requirement(line.strip())
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    return [req for req in reqs if req.marker is None or req.marker.evaluate()]


def installed() -> dict[str, str]:
    """Installed distributions by normalized name."""
    return {
        canonicalize_name(dist.metadata["Name"]): dist.version
        for dist in md.distributions()
        if dist.metadata["Name"]
    }


def below_floor(reqs: list[Requirement], versions: dict[str, str]) -> list[tuple[Requirement, str]]:
    """Installed packages whose version is below their floor; absent packages are skipped."""
    return [
        (req, versions[canonicalize_name(req.name)])
        for req in reqs
        if canonicalize_name(req.name) in versions
        and not req.specifier.contains(versions[canonicalize_name(req.name)], prereleases=True)
    ]


def main(argv: list[str]) -> int:
    """Print upgrade requirements, or check that every floor holds."""
    if argv[1:] not in (["requirements"], ["check"]):
        print(__doc__, file=sys.stderr)
        return 2
    reqs, versions = floors(), installed()
    low = below_floor(reqs, versions)
    if argv[1] == "requirements":
        # Name and version range only: one shell word each (no markers or spaces).
        for req, _ in low:
            print(f"{req.name}{req.specifier}")
        return 0
    if low:
        print("below the security floor: " + ", ".join(f"{r.name} {v} does not satisfy {r}" for r, v in low),
              file=sys.stderr)
        return 1
    held = [f"{r.name} {versions[canonicalize_name(r.name)]}" for r in reqs if canonicalize_name(r.name) in versions]
    print("security floors hold: " + ", ".join(held))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
