"""Stage a private, relocatable CPython runtime; inventory is not licence clearance.

Install the environment using ``uv sync --frozen --no-dev --no-editable
--no-install-project`` first. This tool never downloads or installs anything.
The Mac App Store compliance gate remains independent of private staging.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import shutil
import subprocess
import tempfile


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def interpreter_inputs(python: Path) -> dict:
    code = (
        "import json,platform,sys,sysconfig; "
        "print(json.dumps(dict(basePrefix=sys.base_prefix,"
        "sitePackages=sysconfig.get_path('purelib'),"
        "version=platform.python_version(),arch=platform.machine())))"
    )
    result = subprocess.run([str(python), "-I", "-c", code], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def check_tree(root: Path) -> None:
    """Reject links and .pth search paths that would depend on the build host."""
    for path in root.rglob("*"):
        if path.is_symlink():
            target = path.readlink()
            if target.is_absolute() or not path.resolve().is_relative_to(root.resolve()):
                raise ValueError(f"External runtime symlink: {path.relative_to(root)}")
        elif path.is_file() and path.suffix == ".pth" and path.parent.name == "site-packages":
            for raw in path.read_text(encoding="utf-8").splitlines():
                value = raw.strip()
                if not value or value.startswith("#") or value.startswith(("import ", "import\t")):
                    continue
                target = Path(value)
                if target.is_absolute() or not (path.parent / target).resolve().is_relative_to(root.resolve()):
                    raise ValueError(f"External Python search path: {path.relative_to(root)}")


def packages_inventory(packages: Path, runtime: Path) -> list[dict]:
    result = []
    for distribution in importlib.metadata.distributions(path=[str(packages)]):
        metadata = distribution.metadata
        notices = []
        for item in distribution.files or []:
            if any(word in item.name.lower() for word in ("license", "licence", "notice", "copying")):
                path = Path(distribution.locate_file(item)).resolve()
                if path.is_file() and path.is_relative_to(runtime.resolve()):
                    notices.append(str(path.relative_to(runtime.resolve())))
        # Some distributions omit RECORD. Their actual notice files must still
        # be represented; the inventory never treats absence as approval.
        info = packages / f"{metadata['Name'].replace('-', '_')}-{distribution.version}.dist-info"
        if info.is_dir():
            for path in info.rglob("*"):
                if path.is_file() and any(word in path.name.lower() for word in ("license", "licence", "notice", "copying")):
                    notices.append(str(path.relative_to(runtime)))
        licence = metadata.get("License-Expression") or metadata.get("License") or "not declared"
        result.append({
            "name": metadata["Name"], "version": distribution.version,
            "license": licence if len(licence) <= 160 else "See the distribution METADATA licence field",
            "notices": sorted(set(notices)), "redistributionStatus": "needs-review",
        })
    return sorted(result, key=lambda item: item["name"].lower())


def runtime_files(root: Path) -> dict:
    result = {}
    for path in sorted(root.rglob("*")):
        relative = str(path.relative_to(root))
        if relative == "manifest.json":
            continue
        if path.is_symlink():
            result[relative] = {"symlink": str(path.readlink())}
        elif path.is_file():
            result[relative] = {"bytes": path.stat().st_size, "sha256": sha256(path)}
    return result


def runtime_customization() -> str:
    # init([]) alone still reads knownfiles on the first call in 3.11.
    # OS MIME files may be visible to stat but denied to a sandbox read.
    return (
        "import os\nimport mimetypes\nfrom pathlib import Path\n"
        "mimetypes.knownfiles = []\nmimetypes.init(files=[])\n"
        "os.environ['VOICESTUDIO_BUNDLED_CODE_ONLY'] = '1'\n"
        "os.environ['VOICESTUDIO_BUNDLED_RESOURCES'] = str(Path(__file__).resolve().parents[4])\n"
        "from _voicestudio_bundled_code import install\n"
        "if not install():\n    raise SystemExit('Bundled code policy is inactive')\n"
        "from _voicestudio_sandbox_bookmarks import restore\nrestore()\n"
    )


def stage_phonemizer(runtime: Path, packages: Path) -> list[dict]:
    api = packages / "phonemizer/backend/espeak/api.py"
    library = packages / "espeakng_loader/libespeak-ng.dylib"
    if not api.exists() and not library.exists():
        return []
    # The fork is locked in uv.lock. Fail closed if an upstream update changes
    # its lifecycle; do not silently apply an outdated native-state patch.
    expected = "e0fb8775e9b1a35d030686954b860f1ec7367d42ea82939df026e67d144d3bf8"
    if not library.is_file() or not api.is_file() or sha256(api) != expected:
        raise ValueError("The pinned phonemizer source changed; review the sandbox adapter")
    original = api.read_text(encoding="utf-8")
    start = original.index("        # will be automatically destroyed after use\n")
    end = original.index("        # finally load the library copy and initialize it.", start)
    patched = original[:start] + (
        "        # VoiceStudio MAS: lease a separately signed immutable image.\n"
        "        from _voicestudio_espeak_pool import claim\n"
        "        self._vs_lease = claim(library_path)\n"
        "        weakref.finalize(self, self._vs_lease.close)\n"
        "        espeak_copy = self._vs_lease.path\n\n"
    ) + original[end:]
    line = "        self._library = ctypes.cdll.LoadLibrary(str(espeak_copy))\n"
    patched = patched.replace(line, line + "        self._vs_lease.library = self._library\n", 1)
    compile(patched, str(api), "exec")
    for index in range(128):
        target = runtime / "native/espeak" / f"{index:03}" / library.name
        target.parent.mkdir(parents=True)
        shutil.copy2(library, target)
    helper = Path(__file__).resolve().parents[1] / "backend/core/bundled_espeak.py"
    shutil.copy2(helper, packages / "_voicestudio_espeak_pool.py")
    api.write_text(patched, encoding="utf-8")
    return [{"path": str(api.relative_to(runtime)), "sourceSha256": expected,
             "patchedSha256": sha256(api), "reason": "Use signed espeak instance images without runtime code copies"}]


def stage_runtime(base: Path, packages: Path, output: Path, lock: Path, version: str, arch: str) -> dict:
    if output.exists():
        raise FileExistsError(f"Refusing to replace an existing runtime: {output}")
    if not base.is_dir() or not packages.is_dir():
        raise ValueError("Missing CPython prefix or installed site-packages")
    if output.resolve().is_relative_to(base.resolve()) or output.resolve().is_relative_to(packages.resolve()):
        raise ValueError("Runtime output must be outside its inputs")
    check_tree(packages)
    minor = ".".join(version.split(".")[:2])
    executable = f"bin/python{minor}"
    if not (base / executable).is_file():
        raise ValueError("Missing CPython interpreter")
    output.parent.mkdir(parents=True, exist_ok=True)
    scratch = Path(tempfile.mkdtemp(prefix=".mas-python-", dir=output.parent))
    try:
        for name in ["bin", "lib", "include", "share", "BUILD"]:
            source = base / name
            if source.is_dir():
                shutil.copytree(source, scratch / name, symlinks=True,
                                ignore=shutil.ignore_patterns("__pycache__", "*.pyc", "site-packages"))
            elif source.is_file():
                shutil.copy2(source, scratch / name)
        destination_packages = scratch / "lib" / f"python{minor}" / "site-packages"
        shutil.copytree(packages, destination_packages, symlinks=True,
                        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
        customization = destination_packages / "sitecustomize.py"
        if customization.exists():
            raise ValueError("The runtime already contains a sitecustomize module")
        policy = Path(__file__).resolve().parents[1] / "backend/core/bundled_code.py"
        shutil.copy2(policy, destination_packages / "_voicestudio_bundled_code.py")
        shutil.copy2(policy.with_name("sandbox_bookmarks.py"), destination_packages / "_voicestudio_sandbox_bookmarks.py")
        customization.write_text(runtime_customization(), encoding="utf-8")
        patches = stage_phonemizer(scratch, destination_packages)
        check_tree(scratch)
        manifest = {
            "schemaVersion": 1, "python": executable, "version": version,
            "arch": arch, "lockSha256": sha256(lock),
            "redistributionApproved": False,
            "buildPatches": patches,
            "packages": packages_inventory(destination_packages, scratch),
            "files": runtime_files(scratch),
        }
        (scratch / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
        scratch.rename(output)
        return manifest
    finally:
        if scratch.exists():
            shutil.rmtree(scratch)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--lock", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    inputs = interpreter_inputs(args.python)
    manifest = stage_runtime(Path(inputs["basePrefix"]), Path(inputs["sitePackages"]), args.output,
                             args.lock, inputs["version"], inputs["arch"])
    print(json.dumps({"output": str(args.output), "packages": len(manifest["packages"]),
                      "redistributionApproved": False}))


if __name__ == "__main__":
    main()
