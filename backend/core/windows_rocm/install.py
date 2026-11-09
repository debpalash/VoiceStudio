"""User-started provisioning for the current interpreter, stdlib only."""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

from .artifacts import checked_wheel, download_archive
from .failures import VerificationFailure, failure_report
from .recipe import load_recipe, recipe_identity, validate_target
from .verify import PROJECT_ROOT, verify_environment

#: Packages the overlay replaces; everything else stays at its installed version.
_OVERLAID = ("torch", "torchaudio", "torchvision", "ctranslate2", "rocm", "triton")


def _normalized(name):
    return re.sub(r"[-_.]+", "-", name).lower()


def _overlaid(name):
    norm = _normalized(name)
    return any(norm == p or norm.startswith(p + "-") for p in _OVERLAID)


def constraints_text(distributions=None):
    """Pin every installed package the overlay must not move.

    The ROCm wheels pull dependencies (numpy, sympy, PyYAML…) that uv would
    otherwise resolve fresh from the index, silently upgrading what the locked
    sync installed. Pinning the current environment keeps the lockfile's
    versions — and its supply-chain review — for everything but the overlay.
    """
    dists = distributions if distributions is not None else metadata.distributions()
    pins = {}
    for dist in dists:
        name = dist.metadata["Name"]
        if name and not _overlaid(name):
            pins[_normalized(name)] = f"{name}=={dist.version}"
    return "".join(pin + "\n" for _, pin in sorted(pins.items()))


def hashed_requirements(recipe):
    """``url --hash`` lines for every AMD artifact (uv verifies them on install)."""
    base = recipe["torch"]["find_links"]
    lines = []
    for section in ("runtime", "torch"):
        hashes = recipe[section]["wheel_sha256"]
        for name in recipe[section]["wheel_files"]:
            lines.append(f"{base}{name} --hash=sha256:{hashes[name]}\n")
    return "".join(lines)


def install_command(uv, requirements, reinstall, constraints, *, build_isolation_off=()):
    command = [str(uv), "--no-config", "pip", "install", "--python", sys.executable,
               "--constraints", str(constraints)]
    for name in build_isolation_off:
        # The rocm sdist builds with the locked setuptools instead of fetching one.
        command.extend(["--no-build-isolation-package", name])
    for name in reinstall:
        command.extend(["--reinstall-package", name])
    return command + list(requirements)


def run_install(command):
    # The project/CUDA constraints conflict with the ROCm overlay; this
    # installer passes its own (constraints_text), and hash checking must stay
    # on whatever the user's environment says.
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "PIP_CONSTRAINT",
                       "UV_NO_VERIFY_HASHES"}
    }
    try:
        subprocess.run(command, cwd=PROJECT_ROOT, env=environment, check=True, stdout=sys.stderr, timeout=1800)
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Windows ROCm package installation failed: {exc}") from exc


def provision(uv):
    """Reuse strictly verified installs; repair only confirmed package failures."""
    validate_target()
    try:
        return verify_environment()
    except VerificationFailure as exc:
        if not exc.repairable:
            raise
    recipe = load_recipe()
    # Verify CT2 before changing any package, and discard downloads on any failure.
    with tempfile.TemporaryDirectory(prefix="voicestudio-windows-rocm-") as temporary:
        folder = Path(temporary)
        archive = folder / "ctranslate2.zip"
        ct2 = recipe["ctranslate2"]
        download_archive(ct2["archive_url"], archive, ct2["archive_size"])
        wheel = checked_wheel(archive, folder, ct2)
        constraints = folder / "constraints.txt"
        constraints.write_text(constraints_text(), encoding="utf-8")
        requirements = folder / "rocm-requirements.txt"
        requirements.write_text(hashed_requirements(recipe), encoding="utf-8")
        pins = recipe["runtime"]["packages"] + recipe["torch"]["packages"]
        run_install(install_command(
            uv, ["-r", str(requirements)], [pin.split("==")[0] for pin in pins], constraints,
            build_isolation_off=("rocm",),
        ))
        # The CT2 wheel was hash-checked by checked_wheel; its dependencies stay
        # at their installed versions through the same constraints.
        run_install(install_command(uv, [str(wheel)], ["ctranslate2"], constraints))
    return verify_environment()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--uv", default="uv", help="uv executable used for this interpreter")
    parser.add_argument("--verify-only", action="store_true", help="No downloads, model access or package changes")
    args = parser.parse_args(argv)
    try:
        validate_target()
        report = verify_environment() if args.verify_only else provision(args.uv)
        print(json.dumps(dict(report, recipe_sha256=recipe_identity())))
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary emits bounded machine-readable failure.
        print(json.dumps(failure_report(exc)))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
