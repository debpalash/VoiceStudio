"""Persisted selection and installed-only resolution for diarisation runtimes."""
from __future__ import annotations

import os
from pathlib import Path

from core import prefs

PYANNOTE = "pyannote"
SORTFORMER = "audiocpp-sortformer"
SORTFORMER_REPO = "audio-cpp/audio.cpp-gguf"
SORTFORMER_FILE = "Sortformer-Diar-4spk-v1-GGUF/sortformer-diar-4spk-v1-q8_0.gguf"

_SORTFORMER_MODEL_MISSING = "Install the Sortformer model bundle"
_SORTFORMER_MODEL_BROKEN = "Repair the installed Sortformer model bundle"
_SORTFORMER_RUNTIME_MISSING = (
    "The Sortformer model is installed. Install the audio.cpp runtime to use it"
)
_SORTFORMER_CLI_MISSING = (
    "The installed audio.cpp runtime does not include speaker diarisation"
)


def selected_backend() -> str:
    value = str(
        prefs.resolve(
            "diarization_backend",
            env="OMNIVOICE_DIARIZATION_BACKEND",
            default=PYANNOTE,
        )
    ).strip()
    return value if value in {PYANNOTE, SORTFORMER} else PYANNOTE


def sortformer_model_path() -> Path:
    configured = os.environ.get("OMNIVOICE_DIARIZATION_MODEL", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()

    # An installed-only lookup never reaches the network. Installation remains
    # an explicit Model Library action through the reviewed audio.cpp bundle.
    from huggingface_hub import hf_hub_download
    from services.hf_revisions import revision_for

    return Path(
        hf_hub_download(
            repo_id=SORTFORMER_REPO,
            filename=SORTFORMER_FILE,
            revision=revision_for(SORTFORMER_REPO),
            local_files_only=True,
        )
    ).resolve()


PYANNOTE_REPOS = (
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
)


def selected_model_repos() -> list[str]:
    """Registry models the selected diarisation runtime loads.

    A user-configured Sortformer GGUF (``OMNIVOICE_DIARIZATION_MODEL``) is a
    local asset with no registry identity, so it names no repo.
    """
    if selected_backend() == SORTFORMER:
        if os.environ.get("OMNIVOICE_DIARIZATION_MODEL", "").strip():
            return []
        return [SORTFORMER_REPO]
    return list(PYANNOTE_REPOS)


def ensure_selected_accepted() -> None:
    """Raise ``ModelLicenceNotAccepted`` before an unaccepted diarisation model runs."""
    from services.model_acceptance import ensure_accepted

    ensure_accepted(selected_model_repos())


def select_backend(backend: str) -> None:
    if backend not in {PYANNOTE, SORTFORMER}:
        raise ValueError("Unknown diarisation engine")
    prefs.set_("diarization_backend", backend)


def sortformer_status() -> dict:
    """Return path-free readiness for the model and its native executable."""
    status = {
        "model": SORTFORMER_REPO,
        "model_installed": False,
        "runtime_installed": False,
        "installed": False,
        "reason": _SORTFORMER_MODEL_MISSING,
    }
    try:
        model = sortformer_model_path()
    except Exception:
        return status
    try:
        if not model.is_file() or model.suffix.lower() != ".gguf":
            return status
        with model.open("rb") as model_file:
            if model_file.read(4) != b"GGUF":
                status["reason"] = _SORTFORMER_MODEL_BROKEN
                return status
    except OSError:
        status["reason"] = _SORTFORMER_MODEL_BROKEN
        return status

    status["model_installed"] = True
    status["reason"] = _SORTFORMER_RUNTIME_MISSING
    try:
        from engines.audiocpp.bootstrap import resolve_server_binary

        server = resolve_server_binary()
    except (OSError, RuntimeError):
        return status
    cli = server.with_name("audiocpp_cli.exe" if os.name == "nt" else "audiocpp_cli")
    if not cli.is_file() or (os.name != "nt" and not os.access(cli, os.X_OK)):
        status["reason"] = _SORTFORMER_CLI_MISSING
        return status
    status.update(runtime_installed=True, installed=True, reason=None)
    return status


def installed_backends() -> set[str]:
    """Return complete local runtimes without loading weights or downloading."""
    installed: set[str] = set()
    from api.routers.setup.models import KNOWN_MODELS, cache_is_complete, is_cached

    repo_id = "pyannote/speaker-diarization-3.1"
    spec = next(model for model in KNOWN_MODELS if model["repo_id"] == repo_id)
    if is_cached(repo_id) and cache_is_complete(spec):
        installed.add(PYANNOTE)

    try:
        native = sortformer_status()
        if not native["installed"]:
            return installed
        installed.add(SORTFORMER)
    except (OSError, RuntimeError, ValueError):
        pass
    return installed
