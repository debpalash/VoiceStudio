"""Keep resolved dependencies above reviewed security-fix floors."""

from collections import defaultdict
from pathlib import Path
import tomllib

from packaging.version import Version


PYTHON_FLOORS = {
    "aiohttp": "3.14.3",
    "cryptography": "50.0.0",
    "gradio": "6.15.1",
    # Hydra instantiate and logging-config callable resolution (1.3.7), and
    # the legacy target blacklist missing PyYAML unsafe loaders (1.3.8).
    "hydra-core": "1.3.8",
    # GHSA-qqmf-gpg7-g8gw: checkpoint _instantiator code execution, fixed in
    # 2.6.6. The advisory range is malformed (< 2022.6.15), so Dependabot
    # still flags fixed releases; this floor is the real guard.
    "lightning": "2.6.6",
    # CVE-2026-114612: proxy admin escalation, fixed in 1.103.1.
    "litellm": "1.103.1",
    "mako": "1.4.2",
    "mcp": "1.28.1",
    # Unbounded memory leak in the C extension with strict_map_key, fixed in 1.2.3.
    "msgpack": "1.2.3",
    "multidict": "6.9.1",
    # WekaClassifier model path bypassing nltk.pathsec, fixed in 3.10.4.
    "nltk": "3.10.4",
    "pillow": "12.3.0",
    "protobuf": "5.29.6",
    "pydantic-settings": "2.14.2",
    # Quadratic-time BER/DER decoding denial-of-service advisories, fixed in 0.6.5.
    "pyasn1": "0.6.5",
    "pygments": "2.20.0",
    # Infinite-loop and memory denial-of-service advisories, fixed in 6.21.4.
    "pypdf": "6.21.4",
    "pytorch-lightning": "2.6.6",
    "python-multipart": "0.0.31",
    "starlette": "1.3.1",
    "transformers": "5.10.0",
    "yt-dlp": "2026.7.4",
}


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


# The Dagger CI module resolves its own lockfile.
DAGGER_FLOORS = {
    # Memory leak in aborted validation (denial of service), fixed in 3.3.1.
    "graphql-core": "3.3.1",
}


def test_dagger_security_floors_are_locked():
    lock = tomllib.loads(Path(".dagger/uv.lock").read_text(encoding="utf-8"))
    resolved = _resolved_versions(lock["package"], DAGGER_FLOORS)
    assert resolved.keys() == DAGGER_FLOORS.keys()
    assert _versions_below_floors(resolved, DAGGER_FLOORS) == {}

def test_duplicate_lock_entries_cannot_hide_a_vulnerable_version():
    packages = [
        {"name": "demo", "version": "2.0"},
        {"name": "demo", "version": "0.9"},
    ]
    floors = {"demo": "1.0"}
    resolved = _resolved_versions(packages, floors)
    assert resolved == {"demo": [Version("2.0"), Version("0.9")]}
    assert _versions_below_floors(resolved, floors) == {"demo": ["0.9"]}
