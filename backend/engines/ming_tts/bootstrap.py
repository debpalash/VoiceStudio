"""Ming-3.0-tts venv probe + lazy bootstrap (issue #3793).

Ming-3.0-tts-0.5b (inclusionAI / Ant Group) is a Qwen2.5-backbone zero-shot
cloning TTS. Upstream pins ``transformers==4.46.3`` and ``torch==2.6.0``, which
conflict with the parent's ``transformers>=5.3``, so it runs in its **own
subprocess venv** like IndexTTS / MOSS-TTS-v1.5 / dots.tts / Confucius4.

Probe order (existing power-user installs win — zero migration):

    1. ``${OMNIVOICE_MING_TTS_DIR}/.venv/`` — the user's clone-level venv.
    2. ``backend/engines/ming_tts/.venv/`` — this package's own venv.
    3. Bootstrap: ``uv venv`` then ``uv pip install -r <clone>/requirements.txt``.

Upstream ships **no pyproject.toml/setup.py**: ``ming_tts`` is a flat module
importable only with the clone root on ``sys.path``, so the import probe and the
sidecar both put the clone there. The engine is opt-in (env-dir gated) and
never touched unless ``OMNIVOICE_MING_TTS_DIR`` is set.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from engines._venv_probe import ProbeResult, log_safe, venv_can_import

logger = logging.getLogger("omnivoice.ming_tts.bootstrap")

#: Absolute path to the sidecar entrypoint.
MING_TTS_SIDECAR_SCRIPT: Path = Path(__file__).parent / "main.py"

#: This package's owned venv (Probe 2).
_ENGINES_VENV_DIR: Path = Path(__file__).parent / ".venv"


def _uv_env() -> "dict[str, str] | None":
    """uv cache co-location for installs on a non-system volume (D:-drive /
    portable installs): without it uv stages every wheel on the system drive
    and cross-volume COPIES it into the venv. Canonical logic lives in
    services.sidecar_install.uv_subprocess_env (lazy import, like _locate_uv).
    """
    from services.sidecar_install import uv_subprocess_env
    return uv_subprocess_env(_ENGINES_VENV_DIR.parent.parent)

#: Env var pointing at the user's Ming-3.0-tts clone root.
_CLONE_DIR_ENV: str = "OMNIVOICE_MING_TTS_DIR"

#: The package importable from the clone (flat module in the upstream root).
_IMPORT_PROBE = "ming_tts"

_resolved_python: Optional[Path] = None

_UV_VENV_TIMEOUT_S = 120
_UV_PIP_INSTALL_TIMEOUT_S = 1800


def invalidate() -> None:
    """Clear the resolved-python cache. Tests call this between scenarios."""
    global _resolved_python
    _resolved_python = None


def _clone_dir() -> str | None:
    """Use the same launch-relative path semantics as the child sidecar."""
    raw = os.environ.get(_CLONE_DIR_ENV, "").strip()
    return os.path.abspath(os.path.expanduser(raw)) if raw else None


def is_ming_tts_installed() -> bool:
    """Cheap file-existence check for a usable venv (no subprocess spawn)."""
    return any(cand.is_file() for cand in _probe_paths())


def ming_weights_installed() -> bool:
    """True when the pinned weights are already local (no network, no download)."""
    model = os.environ.get("OMNIVOICE_MING_TTS_MODEL", "").strip()
    if model and os.path.isdir(model):
        return True
    try:
        from huggingface_hub import try_to_load_from_cache
        from services.hf_revisions import revision_for

        default_repo = "inclusionAI/Ming-3.0-tts-0.5b"
        repo = model or default_repo
        # Same rule as the sidecar: the default id always uses the pinned revision.
        rev = revision_for(repo) if repo == default_repo else os.environ.get(
            "OMNIVOICE_MING_TTS_REVISION", "").strip()
        if repo != default_repo and not re.fullmatch(r"[0-9a-f]{40}", rev):
            return False  # the sidecar refuses a custom repo without a commit SHA
        return isinstance(try_to_load_from_cache(repo, "model.safetensors", revision=rev or None), str)
    except Exception:
        return False


def resolve_ming_tts_venv() -> Path:
    """Resolve the sidecar's Python interpreter (probe order in the docstring).
    Memoised. Raises :exc:`RuntimeError` if none can be located and bootstrap
    is unavailable."""
    global _resolved_python
    if _resolved_python is not None:
        return _resolved_python

    clone_dir = _clone_dir()

    # A candidate whose probe ran out of time (#1414): preferred over
    # bootstrapping or declaring the engine missing, but only after every
    # candidate has had its chance to prove itself outright.
    unproven: Optional[Path] = None

    if clone_dir:
        cand = _venv_python_path(Path(clone_dir) / ".venv")
        if cand.is_file():
            verdict = _venv_can_import(cand)
            if verdict == "yes":
                logger.info("Ming-3.0-tts venv resolved from %s: %s", _CLONE_DIR_ENV, log_safe(cand))
                _resolved_python = cand
                return cand
            if verdict == "unproven":
                unproven = cand

    cand = _venv_python_path(_ENGINES_VENV_DIR)
    if cand.is_file():
        verdict = _venv_can_import(cand)
        if verdict == "yes":
            logger.info("Ming-3.0-tts venv resolved from engines path: %s", log_safe(cand))
            _resolved_python = cand
            return cand
        if verdict == "unproven" and unproven is None:
            unproven = cand

    if unproven is not None:
        # Nothing proved itself, but something plausible is installed. Use
        # it: a venv that really is broken fails the sidecar handshake with
        # a real error, which beats reinstalling over the top of a working
        # install or telling the user their engine isn't there.
        logger.warning(
            "Ming-3.0-tts venv %s could not be verified in time; using it "
            "anyway rather than treating a slow import as a missing "
            "install (#1414).",
            log_safe(unproven),
        )
        _resolved_python = unproven
        return unproven

    if not clone_dir:
        raise RuntimeError(
            "Ming-3.0-tts is not installed. Set the "
            f"{_CLONE_DIR_ENV} environment variable to your Ming-3.0-tts clone "
            "(the directory that contains requirements.txt), then restart "
            "VoiceStudio. See docs/engines/ming-tts.md."
        )

    cand = _bootstrap_engines_venv(Path(clone_dir))
    _resolved_python = cand
    return cand


def _venv_python_path(venv_dir: Path) -> Path:
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _probe_paths() -> list[Path]:
    out: list[Path] = []
    clone_dir = _clone_dir()
    if clone_dir:
        out.append(_venv_python_path(Path(clone_dir) / ".venv"))
    out.append(_venv_python_path(_ENGINES_VENV_DIR))
    return out


def _import_probe_code() -> str:
    """Probe snippet mirroring the sidecar's import semantics: upstream is not
    pip-installable, so ``ming_tts`` resolves via the clone on sys.path."""
    clone = _clone_dir()
    if clone:
        return f"import sys; sys.path.insert(0, {clone!r}); import {_IMPORT_PROBE}"
    return f"import {_IMPORT_PROBE}"


def _venv_can_import(python_path: Path) -> ProbeResult:
    """Spawn the candidate python and verify the engine imports.

    Tri-state — "yes" / "no" / "unproven". See ``engines._venv_probe``:
    a probe that runs out of time proves nothing, and treating that as
    "no" is what discarded working user installs (#1414).
    """
    return venv_can_import(
        python_path, _import_probe_code(), engine="ming_tts", logger=logger,
    )


def _locate_uv() -> Optional[str]:
    bundled = os.environ.get("OMNIVOICE_BUNDLED_UV")
    if bundled and Path(bundled).is_file():
        return bundled
    return shutil.which("uv")


def _bootstrap_engines_venv(clone_dir: Path) -> Path:
    """Create engines/ming_tts/.venv and install the user's clone."""
    uv = _locate_uv()
    if not uv:
        raise RuntimeError(
            "uv is required to bootstrap the Ming-3.0-tts venv but was not "
            "found on PATH (and OMNIVOICE_BUNDLED_UV was not set). Install uv "
            "from https://docs.astral.sh/uv/ and re-launch VoiceStudio."
        )

    logger.info(
        "Bootstrapping Ming-3.0-tts venv at %s from %s (several minutes on first "
        "launch)", log_safe(_ENGINES_VENV_DIR), log_safe(clone_dir),
    )
    try:
        subprocess.run(
            [uv, "venv", "--python", "3.10", str(_ENGINES_VENV_DIR)],
            check=True, timeout=_UV_VENV_TIMEOUT_S, capture_output=True,
            env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"uv venv failed for Ming-3.0-tts bootstrap at {_ENGINES_VENV_DIR}: "
            f"{exc.stderr.decode('utf-8', errors='replace') if exc.stderr else exc}"
        ) from exc

    python_path = _venv_python_path(_ENGINES_VENV_DIR)
    requirements = clone_dir / "requirements.txt"
    try:
        if requirements.is_file():
            subprocess.run(
                [uv, "pip", "install", "--python", str(python_path),
                 "-r", str(requirements)],
                check=True, timeout=_UV_PIP_INSTALL_TIMEOUT_S, capture_output=True,
                env=_uv_env(),
            )
        # Editable install only if upstream ever ships packaging metadata —
        # as of 2026-07 there is none, and `uv pip install -e` on a bare clone
        # fails outright. Import resolution is handled via sys.path instead.
        if (clone_dir / "pyproject.toml").is_file() or (clone_dir / "setup.py").is_file():
            subprocess.run(
                [uv, "pip", "install", "--python", str(python_path), "-e", str(clone_dir)],
                check=True, timeout=_UV_PIP_INSTALL_TIMEOUT_S, capture_output=True,
                env=_uv_env(),
            )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "uv pip install failed during Ming-3.0-tts bootstrap "
            f"({clone_dir}): "
            f"{exc.stderr.decode('utf-8', errors='replace') if exc.stderr else exc}. "
            "See docs/engines/ming-tts.md."
        ) from exc

    # Only a *proven* failure is fatal: a bootstrap that installed correctly
    # and is merely slow to import must not be thrown away after spending
    # minutes on the install (#1414).
    if _venv_can_import(python_path) == "no":
        raise RuntimeError(
            f"Ming-3.0-tts bootstrap completed but `import {_IMPORT_PROBE}` still "
            f"fails from {python_path}. Verify {clone_dir} is a valid clone. "
            "See docs/engines/ming-tts.md."
        )

    logger.info("Ming-3.0-tts venv bootstrap successful: %s", log_safe(python_path))
    return python_path


__all__ = [
    "MING_TTS_SIDECAR_SCRIPT",
    "invalidate",
    "is_ming_tts_installed",
    "resolve_ming_tts_venv",
]
