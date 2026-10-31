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
from .recipe import ct2_supported, load_recipe, recipe_identity, validate_target
from .verify import PROJECT_ROOT, verify_environment

#: Packages the overlay replaces; everything else stays at its installed version.
_OVERLAID = ("torch", "torchaudio", "torchvision", "ctranslate2", "rocm", "triton",
             "amd-torch", "amd-torchvision")


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


def _cpu_ct2_missing():
    """Whether this environment has no CTranslate2 at all.

    The ROCm 10 overlay deliberately installs no HIP CT2, so readiness can be
    true while ``ctranslate2`` is absent — and then ``faster-whisper`` cannot
    import, so ASR and the CPU fallback are both gone. Metadata only; no import.
    """
    try:
        metadata.version("ctranslate2")
        return False
    except metadata.PackageNotFoundError:
        return True


def locked_ct2_requirement(uv):
    """The lockfile's own CTranslate2 pin for this interpreter.

    Asking ``uv export`` keeps environment-marker evaluation in uv's hands
    instead of reimplementing it here (this module is stdlib-only), and keeps
    the version on the lockfile's supply-chain review instead of whatever PyPI
    serves today. A bare ``ctranslate2`` would install the current release,
    which is not what the frozen sync would have installed.
    """
    try:
        result = subprocess.run(
            [str(uv), "export", "--frozen", "--no-dev", "--no-hashes", "--format", "requirements-txt"],
            cwd=PROJECT_ROOT, capture_output=True, text=True, errors="replace",
            check=True, timeout=600,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Windows ROCm could not read the locked CTranslate2 version: {exc}") from exc
    for line in result.stdout.splitlines():
        requirement = line.split("#", 1)[0].split(";", 1)[0].strip()
        if requirement.startswith("ctranslate2=="):
            return requirement
    raise RuntimeError("The lockfile pins no CTranslate2 the CPU fallback could install")


def _install_locked_cpu_ct2(uv):
    """Install exactly the locked CPU CTranslate2, nothing else.

    The overlay skipped ctranslate2 with the rest of the HIP replacement, so a
    ROCm 10 environment can verify healthy while ``faster_whisper`` cannot even
    import. Repairing only this package keeps a healthy install from re-fetching
    the AMD wheels.
    """
    with tempfile.TemporaryDirectory(prefix="voicestudio-windows-rocm-") as temporary:
        constraints = Path(temporary) / "constraints.txt"
        constraints.write_text(constraints_text(), encoding="utf-8")
        run_install(install_command(
            uv, [locked_ct2_requirement(uv)], ["ctranslate2"], constraints,
        ))


def provision(uv):
    """Reuse strictly verified installs; repair only confirmed package failures."""
    validate_target()
    recipe = load_recipe()
    # A recipe that pins no HIP CTranslate2 still owes the environment a working
    # CPU one: the frozen sync skipped ctranslate2 along with its HIP replacement.
    # This has to happen BEFORE the healthy-runtime early return below, otherwise
    # an existing ROCm 10 overlay that verifies clean keeps no CTranslate2 at all
    # and every faster-whisper path stays unusable. An environment that already
    # has the package is left untouched, so a healthy install still mutates nothing.
    cpu_ct2_missing = not ct2_supported(recipe) and _cpu_ct2_missing()
    try:
        report = verify_environment()
        if not cpu_ct2_missing:
            return report
        # The overlay itself is healthy; only the CPU speech dependency is absent.
        # Reinstalling AMD wheels here would be an unnecessary multi-GB download,
        # so repair exactly the one missing package and re-verify.
        _install_locked_cpu_ct2(uv)
        return verify_environment()
    except VerificationFailure as exc:
        if not exc.repairable:
            raise
    # Verify CT2 before changing any package, and discard downloads on any failure.
    # A recipe that pins no HIP CTranslate2 (ROCm 10) has nothing to pre-verify.
    with tempfile.TemporaryDirectory(prefix="voicestudio-windows-rocm-") as temporary:
        folder = Path(temporary)
        wheel = None
        if ct2_supported(recipe):
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
        if wheel is not None:
            # The CT2 wheel was hash-checked by checked_wheel; its dependencies stay
            # at their installed versions through the same constraints.
            run_install(install_command(uv, [str(wheel)], ["ctranslate2"], constraints))
        else:
            # The overlay skipped the locked CPU CTranslate2 along with its HIP
            # replacement (torch-variant.mjs excludes it from the frozen sync), so
            # this is the only pass that can put it back. Without it a fresh ROCm 10
            # environment has no ctranslate2 at all: faster-whisper cannot import,
            # the app's own import check fails, and even the CPU fallback is gone.
            run_install(install_command(
                uv, [locked_ct2_requirement(uv)], ["ctranslate2"], constraints,
            ))
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
