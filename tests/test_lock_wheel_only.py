"""First-run setup must never compile an sdist on a user's machine (#4253).

`uv sync` builds any locked package that ships no wheel. On some Windows hosts
the build crashes setuptools (`Failed to build docopt==0.6.2`), which blocks
the runtime install and the backend never starts. Every package reachable from
the default install on Windows, macOS and Linux must therefore lock to a wheel.
Source-only PyPI packages are vendored as wheels under `vendor/wheels/` and
pointed at from `[tool.uv.sources]`.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

from packaging.markers import Marker
from packaging.utils import parse_wheel_filename
from urllib.parse import unquote

ROOT = Path(__file__).resolve().parent.parent

# Intel macOS runs the UI against a remote backend (README, docs/install/macos.md);
# its demucs 4.0.1 pin is never installed on Windows, Linux or Apple Silicon.
_ALLOWED_SDIST_ONLY: dict[tuple[str, str], str] = {
    ("demucs", "4.0.1"): "Intel-Mac-only resolution; no Windows/Linux install",
}
# Packages with wheels everywhere except one platform: (name, version, sys_platform, machine).
_ALLOWED_PLATFORM_SDIST: dict[tuple[str, str, str, str], str] = {
    ("sphn", "0.2.1", "linux", "aarch64"): (
        "upstream publishes no Linux ARM64 wheel; Linux ARM64 is a source-only target, "
        "not a packaged installer"
    ),
}

_PLATFORMS = [
    {"sys_platform": "win32", "platform_machine": "AMD64", "os_name": "nt", "platform_system": "Windows"},
    {"sys_platform": "win32", "platform_machine": "ARM64", "os_name": "nt", "platform_system": "Windows"},
    {"sys_platform": "linux", "platform_machine": "x86_64", "os_name": "posix", "platform_system": "Linux"},
    {"sys_platform": "linux", "platform_machine": "aarch64", "os_name": "posix", "platform_system": "Linux"},
    {"sys_platform": "darwin", "platform_machine": "arm64", "os_name": "posix", "platform_system": "Darwin"},
]
_PYTHONS = ["3.11", "3.12"]
_EXTRAS = ["group-9-omnivoice-cuda", "group-9-omnivoice-cpu"]


def _lock() -> dict:
    return tomllib.loads((ROOT / "uv.lock").read_text(encoding="utf-8"))


def _reachable(lock: dict, env: dict[str, str], extra: str) -> set[tuple[str, str]]:
    pkgs: dict[str, list[dict]] = {}
    for p in lock["package"]:
        pkgs.setdefault(p["name"], []).append(p)
    root = next(p for p in lock["package"] if "editable" in p["source"] or "virtual" in p["source"])
    seen: set[tuple[str, str, str]] = set()
    stack: list[tuple[dict, tuple[str, ...]]] = [(root, ())]
    while stack:
        pkg, extras = stack.pop()
        deps = list(pkg.get("dependencies", []))
        # A dependency requested with extras also pulls that package's optional deps.
        for name in extras:
            deps += pkg.get("optional-dependencies", {}).get(name, [])
        for dep in deps:
            marker = dep.get("marker")
            if marker and not Marker(marker).evaluate({**env, "extra": extra}):
                continue
            wanted = tuple(sorted(dep.get("extra", [])))
            for cand in pkgs.get(dep["name"], []):
                if "version" in dep and cand.get("version") != dep["version"]:
                    continue
                key = (cand["name"], cand.get("version", ""), ",".join(wanted))
                if key not in seen:
                    seen.add(key)
                    stack.append((cand, wanted))
    return {(name, version) for name, version, _ in seen}


def _platform_tag_matches(tag: str, env: dict[str, str]) -> bool:
    if tag == "any":
        return True
    system, machine = env["sys_platform"], env["platform_machine"]
    if system == "win32":
        # Windows ARM64 runs the x64 runtime under emulation when no ARM64 wheel exists.
        return tag == "win_amd64" or (machine == "ARM64" and tag == "win_arm64")
    if system == "darwin":
        return tag.startswith("macosx_") and tag.endswith(("_arm64", "_universal2"))
    arch = "x86_64" if machine == "x86_64" else "aarch64"
    return tag.startswith(("manylinux", "musllinux", "linux_")) and tag.endswith(arch)


def _interpreter_tag_matches(interpreter: str, abi: str, py: str) -> bool:
    nodot = py.replace(".", "")
    if interpreter in {"py3", f"py{nodot}"}:
        return True
    if interpreter == f"cp{nodot}":
        return True
    # abi3 wheels built for an older CPython still load on newer ones.
    return abi == "abi3" and interpreter.startswith("cp3") and int(interpreter[3:]) <= int(nodot[1:])


def _has_wheel_for(pkg: dict, env: dict[str, str], py: str) -> bool:
    for wheel in pkg.get("wheels", []):
        name = unquote((wheel.get("url") or wheel.get("path") or wheel.get("filename") or "").rsplit("/", 1)[-1])
        try:
            *_, tags = parse_wheel_filename(name)
        except Exception:
            continue
        if any(_platform_tag_matches(t.platform, env) and _interpreter_tag_matches(t.interpreter, t.abi, py)
               for t in tags):
            return True
    return False


def test_default_install_never_builds_an_sdist() -> None:
    lock = _lock()
    by_key = {(p["name"], p.get("version", "")): p for p in lock["package"]}
    offenders: set[tuple[str, str]] = set()
    for env in _PLATFORMS:
        for py in _PYTHONS:
            full = {**env, "python_version": py, "python_full_version": f"{py}.0", "implementation_name": "cpython"}
            for extra in _EXTRAS:
                for key in _reachable(lock, full, extra):
                    p = by_key[key]
                    platform_key = (*key, env["sys_platform"], env["platform_machine"])
                    if (not _has_wheel_for(p, env, py) and key not in _ALLOWED_SDIST_ONLY
                            and platform_key not in _ALLOWED_PLATFORM_SDIST):
                        offenders.add(key)
    assert not offenders, (
        f"locked without a wheel, so first-run setup would compile them: {sorted(offenders)}. "
        "Vendor a wheel under vendor/wheels/ and add it to [tool.uv.sources] (#4253)."
    )


def test_vendored_wheels_are_shipped_with_the_app() -> None:
    config = (ROOT / "electron" / "electron-builder.config.mjs").read_text(encoding="utf-8")
    assert "vendor/wheels" in config, "packaged setup resolves path wheels relative to the bundled uv.lock"
    for p in _lock()["package"]:
        src = p["source"].get("path")
        if src:
            assert (ROOT / src).is_file(), f"{p['name']} points at missing {src}"
