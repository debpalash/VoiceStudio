"""Keep resolved dependencies above reviewed security-fix floors."""

from collections import defaultdict
from pathlib import Path
import tomllib

from packaging.version import Version


PYTHON_FLOORS = {
    "aiohttp": "3.14.3",
    "cryptography": "50.0.0",
    "gradio": "6.15.1",
    # GHSA-qqmf-gpg7-g8gw: checkpoint _instantiator code execution, fixed in
    # 2.6.6. The advisory range is malformed (< 2022.6.15), so Dependabot
    # still flags fixed releases; this floor is the real guard.
    "lightning": "2.6.6",
    "mako": "1.3.12",
    "mcp": "1.28.1",
    "msgpack": "1.2.1",
    "nltk": "3.10.0",
    "pillow": "12.3.0",
    "protobuf": "5.29.6",
    "pydantic-settings": "2.14.2",
    "pygments": "2.20.0",
    "pypdf": "6.15.0",
    "pytorch-lightning": "2.6.6",
    "python-multipart": "0.0.31",
    "starlette": "1.3.1",
    "transformers": "5.10.0",
    "yt-dlp": "2026.7.4",
}
CARGO_FLOORS = {"quinn-proto": "0.11.15"}


def _resolved_versions(packages, names):
    resolved = defaultdict(list)
    for package in packages:
        name = package["name"].lower()
        if name in names:
            resolved[name].append(Version(package["version"]))
    return dict(resolved)


def _versions_below_floors(resolved, floors):
    return {
        name: [str(version) for version in versions if version < Version(floors[name])]
        for name, versions in resolved.items()
        if any(version < Version(floors[name]) for version in versions)
    }


def test_python_security_floors_are_locked():
    lock = tomllib.loads(Path("uv.lock").read_text(encoding="utf-8"))
    resolved = _resolved_versions(lock["package"], PYTHON_FLOORS)
    assert resolved.keys() == PYTHON_FLOORS.keys()
    assert _versions_below_floors(resolved, PYTHON_FLOORS) == {}


def test_quinn_security_floor_is_locked():
    lock = tomllib.loads(
        Path("frontend/src-tauri/Cargo.lock").read_text(encoding="utf-8")
    )
    resolved = _resolved_versions(lock["package"], CARGO_FLOORS)
    assert resolved.keys() == CARGO_FLOORS.keys()
    assert _versions_below_floors(resolved, CARGO_FLOORS) == {}


def test_duplicate_lock_entries_cannot_hide_a_vulnerable_version():
    packages = [
        {"name": "demo", "version": "2.0"},
        {"name": "demo", "version": "0.9"},
    ]
    floors = {"demo": "1.0"}
    resolved = _resolved_versions(packages, floors)
    assert resolved == {"demo": [Version("2.0"), Version("0.9")]}
    assert _versions_below_floors(resolved, floors) == {"demo": ["0.9"]}
