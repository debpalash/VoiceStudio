"""MOSS-TTS-v1.5 venv probe + lazy bootstrap (issue #498).

The parent process needs to know *which Python interpreter* to spawn the
MOSS-TTS-v1.5 sidecar under. This module owns that resolution. It mirrors
``engines.indextts.bootstrap`` because MOSS has the same shape of problem:
a hard ``transformers`` pin (``==5.0.0``) that conflicts with the parent's
``transformers>=5.3`` — so MOSS runs in its own venv.

Probe order (priority — existing power-user installs win, zero migration):

    1. ``${OMNIVOICE_MOSS_TTS_V15_DIR}/.venv/`` — the user's clone-level
       venv. Highest priority: a user who already cloned MOSS-TTS and ran
       ``uv pip install -e ".[torch-runtime]"`` (per upstream docs) gets
       reused verbatim, no re-download of the ~16 GB model.
    2. ``backend/engines/moss_tts_v15/.venv/`` — this package's own venv,
       created by step 3 if needed.
    3. Bootstrap: ``uv venv`` then ``uv pip install -e
       "${DIR}[torch-runtime]"``. Requires ``OMNIVOICE_MOSS_TTS_V15_DIR``.
       The upstream ``torch-runtime`` extra is CUDA (``+cu128``), so the
       auto-bootstrap targets CUDA hosts; non-CUDA (CPU/Mac) users set up
       their own venv per docs/engines/moss-tts-v15.md (Probe 1).

Caching: resolution is memoised after the first successful call. Tests
reset via :func:`invalidate`.

Security: bootstrap never touches HF_TOKEN; the sidecar's stderr is drained
by SubprocessBackend through the parent root logger where Phase 1's
``HFTokenRedactor`` strips token bytes. ``uv pip install -e`` installs from
a user-controlled clone the user already trusts (same posture as IndexTTS).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Optional

from engines._venv_probe import ProbeResult, log_safe, venv_can_import

logger = logging.getLogger("omnivoice.moss_tts_v15.bootstrap")

#: Absolute path to the sidecar entrypoint. ``MossTTSV15Backend.sidecar_script``
#: returns this; SubprocessBackend spawns it with the resolved venv python.
MOSS_TTS_V15_SIDECAR_SCRIPT: Path = Path(__file__).parent / "main.py"

#: This package's owned venv (Probe 2). The MOSS-TTS clone, when bootstrapped,
#: is installed into this venv via ``uv pip install -e``.
_ENGINES_VENV_DIR: Path = Path(__file__).parent / ".venv"


def _uv_env() -> "dict[str, str] | None":
    """uv cache co-location for installs on a non-system volume (D:-drive /
    portable installs): without it uv stages every wheel on the system drive
    and cross-volume COPIES it into the venv. Canonical logic lives in
    services.sidecar_install.uv_subprocess_env (lazy import, like _locate_uv).
    """
    from services.sidecar_install import uv_subprocess_env
    return uv_subprocess_env(_ENGINES_VENV_DIR.parent.parent)

#: Env var pointing at the user's MOSS-TTS clone root.
_CLONE_DIR_ENV: str = "OMNIVOICE_MOSS_TTS_V15_DIR"

#: Per-process resolution cache. Cleared by :func:`invalidate` for tests.
_resolved_python: Optional[Path] = None

# Timeouts — bounded so a wedged venv never hangs the parent. The bootstrap
# install can take many minutes on a cold cache (MOSS pulls a CUDA torch
# build + transformers + an audio codec stack).
_UV_VENV_TIMEOUT_S = 120
_UV_PIP_INSTALL_TIMEOUT_S = 1800


# ── public API ────────────────────────────────────────────────────────────


def invalidate() -> None:
    """Clear the resolved-python cache. Tests call this between scenarios."""
    global _resolved_python
    _resolved_python = None


def is_moss_tts_v15_installed() -> bool:
    """Cheap file-existence check for a usable MOSS-TTS-v1.5 venv.

    Returns True if either Probe 1 or Probe 2 has a Python executable on
    disk. Does NOT spawn the venv Python — that's saved for
    :func:`resolve_moss_tts_v15_venv`, which is only invoked on the first
    generate() / health_check(). This fires on every Settings render via
    ``MossTTSV15Backend.is_available()``, so it stays cheap.
    """
    for cand in _probe_paths():
        if cand.is_file():
            return True
    return False


def resolve_moss_tts_v15_venv() -> Path:
    """Resolve the path to the Python interpreter that runs the sidecar.

    Probe order described in the module docstring. Memoised. Raises
    :exc:`RuntimeError` if no working venv can be located AND the bootstrap
    path is unavailable.
    """
    global _resolved_python
    if _resolved_python is not None:
        return _resolved_python

    clone_dir = os.environ.get(_CLONE_DIR_ENV)

    # A candidate whose probe ran out of time (#1414): preferred over
    # bootstrapping or declaring the engine missing, but only after every
    # candidate has had its chance to prove itself outright.
    unproven: Optional[Path] = None

    # Probe 1 — user's clone-level venv (highest priority for back-compat).
    if clone_dir:
        cand = _venv_python_path(Path(clone_dir) / ".venv")
        if cand.is_file():
            verdict = _venv_can_import_moss(cand)
            if verdict == "yes":
                logger.info(
                    "MOSS-TTS-v1.5 venv resolved from %s: %s", _CLONE_DIR_ENV, cand,
                )
                _resolved_python = cand
                return cand
            if verdict == "unproven":
                unproven = cand

    # Probe 2 — this package's own venv.
    cand = _venv_python_path(_ENGINES_VENV_DIR)
    if cand.is_file():
        verdict = _venv_can_import_moss(cand)
        if verdict == "yes":
            logger.info("MOSS-TTS-v1.5 venv resolved from engines path: %s", cand)
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
            "MOSS-TTS-v1.5 venv %s could not be verified in time; using it "
            "anyway rather than treating a slow import as a missing "
            "install (#1414).",
            log_safe(unproven),
        )
        _resolved_python = unproven
        return unproven

    # Probe 3 — bootstrap.
    if not clone_dir:
        raise RuntimeError(
            "MOSS-TTS-v1.5 is not installed. Set the "
            f"{_CLONE_DIR_ENV} environment variable to your MOSS-TTS clone "
            "(the directory that contains pyproject.toml), then restart "
            "VoiceStudio. See docs/engines/moss-tts-v15.md for the full "
            "install walk-through."
        )

    cand = _bootstrap_engines_venv(Path(clone_dir))
    _resolved_python = cand
    return cand


# ── internals ─────────────────────────────────────────────────────────────


def _venv_python_path(venv_dir: Path) -> Path:
    """Return the python executable path inside a venv directory.

    Handles the Unix (``bin/python``) vs Windows (``Scripts/python.exe``)
    layout. No filesystem access — caller checks .is_file().
    """
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _probe_paths() -> list[Path]:
    """Ordered list of candidate venv-python paths (no .is_file() check)."""
    out: list[Path] = []
    clone_dir = os.environ.get(_CLONE_DIR_ENV)
    if clone_dir:
        out.append(_venv_python_path(Path(clone_dir) / ".venv"))
    out.append(_venv_python_path(_ENGINES_VENV_DIR))
    return out


def _venv_can_import_moss(python_path: Path) -> ProbeResult:
    """Spawn the candidate python and verify the engine imports.

    Tri-state — "yes" / "no" / "unproven". See ``engines._venv_probe``:
    a probe that runs out of time proves nothing, and treating that as
    "no" is what discarded working user installs (#1414).
    """
    return venv_can_import(
        python_path, "import transformers, torch", engine="moss_tts_v15", logger=logger,
    )


def _locate_uv() -> Optional[str]:
    """Find the uv binary — bundled first (Tauri-set env var), else PATH."""
    bundled = os.environ.get("OMNIVOICE_BUNDLED_UV")
    if bundled and Path(bundled).is_file():
        return bundled
    sys_uv = shutil.which("uv")
    if sys_uv:
        return sys_uv
    return None


def _bootstrap_engines_venv(clone_dir: Path) -> Path:
    """Create engines/moss_tts_v15/.venv and install the user's clone into it.

    Runs ``uv venv <engines_venv>`` then ``uv pip install --python
    <engines_venv>/bin/python -e "<clone>[torch-runtime]"``. Verifies the
    result by re-probing the import — a successful uv invocation that still
    can't import the stack indicates a deeper environment problem, and we
    raise with whatever stderr we captured plus a docs pointer.
    """
    uv = _locate_uv()
    if not uv:
        raise RuntimeError(
            "uv is required to bootstrap the MOSS-TTS-v1.5 venv but was not "
            "found on PATH (and OMNIVOICE_BUNDLED_UV was not set). Install uv "
            "from https://docs.astral.sh/uv/ and re-launch VoiceStudio, or set "
            "OMNIVOICE_BUNDLED_UV to the absolute path of a uv binary."
        )

    logger.info(
        "Bootstrapping MOSS-TTS-v1.5 venv at %s from %s (this can take "
        "several minutes on first launch)", _ENGINES_VENV_DIR, clone_dir,
    )

    try:
        subprocess.run(
            [uv, "venv", str(_ENGINES_VENV_DIR)],
            check=True,
            timeout=_UV_VENV_TIMEOUT_S,
            capture_output=True,
            env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            f"uv venv failed for MOSS-TTS-v1.5 bootstrap at {_ENGINES_VENV_DIR}: "
            f"{exc.stderr.decode('utf-8', errors='replace') if exc.stderr else exc}"
        ) from exc

    from core.torch_indexes import UV_PIP_CU128_ARGS

    python_path = _venv_python_path(_ENGINES_VENV_DIR)
    try:
        subprocess.run(
            [
                uv, "pip", "install",
                "--python", str(python_path),
                "-e", f"{clone_dir}[torch-runtime]",
                # == also admits +cu128, which needs NVIDIA NPP just to
                # decode reference audio. Require the CPU decoder (#2690).
                "torchcodec===0.8.1",
                # The extra pins torch==2.9.1+cu128, which exists only on
                # PyTorch's index — without it this could never resolve, on
                # any host (core.torch_indexes).
                *UV_PIP_CU128_ARGS,
            ],
            check=True,
            timeout=_UV_PIP_INSTALL_TIMEOUT_S,
            capture_output=True,
            env=_uv_env(),
        )
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(
            "uv pip install -e failed during MOSS-TTS-v1.5 bootstrap "
            # uv's own error names what failed; the PyTorch index is always
            # supplied now, so a guess about the host would only mislead.
            f"({clone_dir}). See docs/engines/moss-tts-v15.md for the manual "
            "install. Error: "
            f"{exc.stderr.decode('utf-8', errors='replace') if exc.stderr else exc}"
        ) from exc

    # Only a *proven* failure is fatal: a bootstrap that installed correctly
    # and is merely slow to import must not be thrown away after spending
    # minutes on the install (#1414).
    if _venv_can_import_moss(python_path) == "no":
        raise RuntimeError(
            "MOSS-TTS-v1.5 bootstrap completed but the transformers/torch "
            f"import still fails from {python_path}. Verify that {clone_dir} "
            "is a valid MOSS-TTS clone. See docs/engines/moss-tts-v15.md."
        )

    logger.info("MOSS-TTS-v1.5 venv bootstrap successful: %s", python_path)
    return python_path


__all__ = [
    "MOSS_TTS_V15_SIDECAR_SCRIPT",
    "invalidate",
    "is_moss_tts_v15_installed",
    "resolve_moss_tts_v15_venv",
]
