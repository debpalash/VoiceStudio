"""One-click sidecar-engine provisioner.

Some engines (IndexTTS 2.5, MOSS-v1.5, dots.tts, and Confucius4) have the
same shape and can't live in the app venv because they pin a ``transformers``
version that conflicts with the parent's ``>=5.3``. They run as sidecars:
a source checkout + a dedicated venv + (for IndexTTS-2) model weights in
``<checkout>/checkpoints/``. Until now provisioning that trio was four
manual terminal steps; this module turns it into a resumable background
job the Model Catalogue UI can start and poll.

Design notes (single source of truth for the choices):

* **Fetch: git primary, tarball fallback.** ``git clone --depth 1`` is the
  primary path (fast, matches the documented manual flow, and leaves a
  repo the user can update). When git is absent — common on Windows — we
  fall back to downloading the GitHub source tarball over HTTPS (httpx,
  honours proxy env vars) and extracting it with :mod:`tarfile`. A
  ``pip install git+https://…`` path was rejected because the engine
  *directory* must exist on disk anyway: the sidecar resolves its venv
  and model weights relative to it.
* **Managed install root:** ``DATA_DIR/engines/<engine_id>/`` — always
  user-writable (works in frozen/packaged builds where ``backend/`` is
  read-only), survives app updates, and never collides with a user's own
  clone. A user-managed install (env var already pointing at their clone)
  is left completely alone.
* **Weights ARE part of the install** for engines whose sidecar loads
  from ``<checkout>/<weights_subdir>/`` (IndexTTS 2.5's ``main.py`` reads
  ``$OMNIVOICE_INDEXTTS_DIR/checkpoints/config_v2_5.yaml`` — verified). The
  download goes through ``huggingface_hub.snapshot_download`` with the
  endpoint from :mod:`services.endpoint_race` (HF endpoint auto-select;
  **no hardcoded huggingface.co**) and the token from
  :mod:`services.token_resolver`.
* **Idempotent + resumable:** every step no-ops when its output is
  already healthy and repairs it when it is half-there (a checkout
  without ``pyproject.toml`` is re-fetched; a venv that can't import the
  probe module is re-installed; ``snapshot_download`` resumes weights).
* **Persistence:** on success the checkout path is written to
  ``os.environ[<env_var>]`` (the engine's bootstrap reads the env var, so
  it works immediately — no restart) and to ``prefs.json`` under
  ``env.<env_var>`` (restored into the environment at startup by
  ``main.py``), the same mechanism Settings' env panel uses.

Cross-platform: no symlinks, no shell strings (argv lists only), venv
layout resolved per-OS (``Scripts/python.exe`` vs ``bin/python``).
"""
from __future__ import annotations

import logging
import os
import shutil
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, NamedTuple, Optional

from core.config import DATA_DIR
from core.contained_subprocess import OwnedPopen, WindowsJobPopen, spawn_owned

logger = logging.getLogger("omnivoice.sidecar_install")

_GIB = 1024 ** 3

# Headroom kept free on the target volume on top of the estimated install
# size. Same needs-X/have-Y message shape as the model-install guard
# (api.routers.setup.models.disk_space_error), but a deliberately SMALLER
# floor than its MIN_FREE_GB=10: required_bytes here is already a
# conservative over-estimate, so stacking the full model-cache headroom on
# top would block legitimate installs on ~15 GB-free machines.
MIN_FREE_GB = 5

# Bounded in-memory log per job (last N lines survive; enough for the UI's
# log tail and for the failure remediation to quote real output).
_LOG_MAX_LINES = 200

_GIT_CLONE_TIMEOUT_S = 600
_TARBALL_TIMEOUT_S = 600
_UV_VENV_TIMEOUT_S = 300
_UV_PIP_INSTALL_TIMEOUT_S = 3600
_IMPORT_PROBE_TIMEOUT_S = 120


# ── Spec ───────────────────────────────────────────────────────────────────


class ExtraSource(NamedTuple):
    """A second pinned source tree fetched into the checkout: an upstream git
    submodule, which neither a depth-1 clone nor GitHub's source tarball
    includes. Always fetched as the tarball of its pinned commit."""

    path: str           # where it goes, relative to the checkout
    revision: str       # reviewed upstream commit
    tarball_url: str    # GitHub archive of that commit
    required_path: str  # a file, relative to *path*, proving the tree is there


@dataclass(frozen=True)
class SidecarSpec:
    """Everything the provisioner needs to install one sidecar engine.

    Parametrized so future sidecar engines (MOSS-v1.5, dots.tts,
    Confucius4) become one SPECS entry, not another installer.
    """

    engine_id: str
    display_name: str
    repo_url: str                      # git clone URL (primary fetch path)
    tarball_url: str                   # source tarball (fallback when git is absent)
    checkout_dirname: str              # directory name of the checkout under the managed root
    env_var: str                       # env var the engine's bootstrap reads (install dir)
    probe_module: str                  # python -c "import <probe_module>" proves the venv works
    repo_ref: Optional[str] = None     # branch/tag selected by git clone
    source_revision: Optional[str] = None  # reviewed upstream commit
    source_required_path: Optional[str] = None  # distinguishes incompatible source generations
    weights_repo_id: Optional[str] = None   # HF repo downloaded into <checkout>/<weights_subdir>
    weights_revision: Optional[str] = None  # reviewed HF commit
    weights_subdir: str = "checkpoints"
    # Model-config filenames accepted inside weights_subdir. A tuple, not a
    # single name: IndexTTS 2.5's weights repo ships config.yaml, but installs
    # predating #1611 were only usable after hand-renaming it to
    # config_v2_5.yaml, and those must keep working without a reinstall.
    weights_config_names: tuple[str, ...] = ("config.yaml",)
    docs_path: str = "docs/engines"         # where the manual-install fallback lives
    required_bytes: int = 12 * _GIB    # conservative source+venv+weights estimate for preflight
    weights_bytes: Optional[int] = None
    dependency_bytes: Optional[int] = None
    potentially_shared_bytes: Optional[int] = None
    temporary_free_bytes: Optional[int] = None
    disk_confidence: str = "unknown"
    # Called after a successful install/uninstall so the engine's memoised
    # venv resolution re-probes (import inside the lambda — never at module load).
    invalidate: Callable[[], None] = field(default=lambda: None)
    # Cheap "is a healthy install already present?" probe (file existence only).
    installed_probe: Callable[[], bool] = field(default=lambda: False)
    # Extra `uv venv` arguments — an interpreter pin for an upstream that
    # declares one, e.g. ("--python", "3.10").
    venv_args: tuple[str, ...] = ()
    # `uv pip install` target, "{checkout}" substituted. Each upstream installs
    # differently (editable, editable with an extra, a requirements file, a
    # constraints file); the default is the editable install IndexTTS uses.
    install_args: tuple[str, ...] = ("-e", "{checkout}")
    # Add PyTorch's CUDA index on a CUDA host. Plain PyPI torch is CPU-only on
    # Windows, and `+cuNNN` local-version pins exist nowhere else.
    uses_cuda_index: bool = False
    # Python that proves the venv works; "{checkout}" / "{checkout_repr}"
    # substituted. None means `import <probe_module>`.
    probe_code: Optional[str] = None
    # The file whose presence proves a fetched checkout is the whole
    # repository. Most upstreams ship a pyproject.toml; Confucius4 ships
    # only requirements.txt and setup.py.
    source_manifest: str = "pyproject.toml"
    # False for an engine that is a PyPI package, not a repository: nothing
    # is fetched, and the managed root holds only the engine's own venv.
    has_source: bool = True
    # Add PyTorch's CPU index on every host, for an engine that only ever
    # runs torch on the CPU (see core.torch_indexes).
    cpu_torch_index: bool = False
    # torch/torchaudio pins for an upstream that leaves torch unpinned. Left
    # to the resolver, PyPI's newest torch (CPU-only on Windows) pairs with a
    # CUDA torchaudio from the other index. The host picks the build of the
    # pinned pair: `+cu128` on a CUDA host, `+cpu` on other Windows and Linux
    # hosts, plain on macOS.
    torch_pins: tuple[str, ...] = ()
    # Submodule trees the upstream repository needs (see ExtraSource).
    extra_sources: tuple[ExtraSource, ...] = ()
    # Download only these files of the weights repo (huggingface_hub
    # allow_patterns). Empty downloads the whole repository.
    weights_allow_patterns: tuple[str, ...] = ()
    # Require the completion marker the import probe writes. Only IndexTTS,
    # installed before the marker existed, opts out.
    requires_install_marker: bool = True
    # Opt-in recipe revision: legacy markers stay valid for unchanged engines.
    install_revision: str = ""
    # The weights repo is also an ordinary Model Catalogue download that the
    # engine's in-process path uses (CosyVoice). Otherwise it is one only the
    # installer can place, and a plain download is not offered for it.
    weights_catalogue_download: bool = False
    # Can the one-click install work on THIS machine? (ok, reason). Consulted
    # before an Install button is offered and again when an install starts, so
    # a host the upstream does not support never gets a job that can only fail.
    host_supported: Callable[[], tuple[bool, str]] = field(default=lambda: (True, ""))


def _indextts_invalidate() -> None:
    from engines.indextts import bootstrap
    bootstrap.invalidate()


def _indextts_installed() -> bool:
    from engines.indextts.bootstrap import is_indextts_installed
    return is_indextts_installed()


def _moss_invalidate() -> None:
    from engines.moss_tts_v15 import bootstrap
    bootstrap.invalidate()


def _moss_installed() -> bool:
    from engines.moss_tts_v15.bootstrap import is_moss_tts_v15_installed
    return is_moss_tts_v15_installed()


def _confucius4_invalidate() -> None:
    from engines.confucius4 import bootstrap
    bootstrap.invalidate()


def _confucius4_installed() -> bool:
    from engines.confucius4.bootstrap import is_confucius4_installed
    return is_confucius4_installed()


def _dots_invalidate() -> None:
    from engines.dots_tts import bootstrap
    bootstrap.invalidate()


def _dots_installed() -> bool:
    from engines.dots_tts.bootstrap import is_dots_tts_installed
    return is_dots_tts_installed()


def _host_family() -> str:
    """The accelerator family this host runs, or "cpu" when it cannot tell."""
    try:
        from core.device_caps import detect_host_caps
        return str(detect_host_caps().family)
    except Exception:  # noqa: BLE001 — a probe failure must not break installs
        return "cpu"


def _torch_pin_args(spec: "SidecarSpec") -> list[str]:
    from core.torch_indexes import UV_PIP_CPU_ARGS, UV_PIP_CU128_ARGS

    if _host_family() == "cuda":
        return [f"{pin}+cu128" for pin in spec.torch_pins] + list(UV_PIP_CU128_ARGS)
    if sys.platform in ("win32", "linux"):
        return [f"{pin}+cpu" for pin in spec.torch_pins] + list(UV_PIP_CPU_ARGS)
    return list(spec.torch_pins)


def _moss_host() -> tuple[bool, str]:
    if _host_family() == "cuda":
        return True, ""
    return False, (
        "MOSS-TTS-v1.5's one-click install uses its CUDA build of PyTorch, and "
        "this machine has no NVIDIA GPU available. Its guide covers a manual "
        "CPU install."
    )


def _dots_host() -> tuple[bool, str]:
    if sys.platform != "win32":
        return True, ""
    return False, (
        "dots.tts publishes no Windows install. Run VoiceStudio on Linux or "
        "macOS, or under WSL2, to use it."
    )


def _no_intel_mac(message: str) -> Callable[[], tuple[bool, str]]:
    """A host gate for an engine whose pinned PyTorch has no Intel Mac build
    (PyTorch stopped publishing macOS x86_64 wheels after 2.2)."""
    def gate() -> tuple[bool, str]:
        import platform
        if sys.platform == "darwin" and platform.machine().lower() == "x86_64":
            return False, message
        return True, ""
    return gate


def _in_app_env(module: str) -> Callable[[], bool]:
    """An install made with ``uv sync --extra`` lives in the app's own
    environment. It counts as installed, so the installer never provisions a
    second copy over one that works."""
    def probe() -> bool:
        import importlib.util
        try:
            return importlib.util.find_spec(module) is not None
        except (ImportError, ValueError):
            return False
    return probe


# The trimmed CosyVoice requirements ship with the app (see that file for
# what was dropped from upstream's list and why).
_COSYVOICE_REQUIREMENTS = str(
    Path(__file__).resolve().parents[1] / "engines" / "cosyvoice_subprocess" / "requirements.txt"
)


SPECS: dict[str, SidecarSpec] = {
    "indextts2": SidecarSpec(
        engine_id="indextts2",
        display_name="IndexTTS 2.5",
        repo_url="https://github.com/index-tts/index-tts.git",
        tarball_url=(
            "https://github.com/index-tts/index-tts/archive/"
            "bf2e967fac7933197143b017a60820b1ad40c448.tar.gz"
        ),
        checkout_dirname="index-tts-2.5",
        env_var="OMNIVOICE_INDEXTTS_DIR",
        probe_module="indextts.infer_v2_5",
        repo_ref="indextts-2.5",
        source_revision="bf2e967fac7933197143b017a60820b1ad40c448",
        source_required_path="indextts/infer_v2_5.py",
        weights_repo_id="IndexTeam/IndexTTS-2.5",
        weights_revision="d0aa86e75bb6f3437f3831e95056fa72842d89ef",
        weights_subdir="checkpoints",
        weights_config_names=("config.yaml", "config_v2_5.yaml"),
        docs_path="docs/engines/indextts.md",
        # ~0.1 GB source + up to ~6 GB venv (torch + transformers<5) +
        # ~6 GB weights. Deliberately conservative; the preflight subtracts
        # whatever a partial install already put on disk.
        required_bytes=12 * _GIB,
        weights_bytes=6 * _GIB,
        dependency_bytes=6 * _GIB,
        potentially_shared_bytes=None,
        temporary_free_bytes=12 * _GIB,
        disk_confidence="estimated",
        invalidate=_indextts_invalidate,
        installed_probe=_indextts_installed,
        # Installed before the completion marker existed; its weights
        # marker already proves a finished install.
        requires_install_marker=False,
    ),
    # Pinned to the upstream commits current on 2026-09-10. Weights are not
    # fetched here: each engine downloads them into the shared HF cache on its
    # first synthesis, as its manual install always has.
    "moss-tts-v15": SidecarSpec(
        engine_id="moss-tts-v15",
        display_name="MOSS-TTS-v1.5",
        repo_url="https://github.com/OpenMOSS/MOSS-TTS.git",
        tarball_url=(
            "https://github.com/OpenMOSS/MOSS-TTS/archive/"
            "934d6826b084c46a0d033402174d5f8ac4ed2519.tar.gz"
        ),
        checkout_dirname="MOSS-TTS",
        env_var="OMNIVOICE_MOSS_TTS_V15_DIR",
        probe_module="transformers",
        probe_code="import transformers, torch",
        source_revision="934d6826b084c46a0d033402174d5f8ac4ed2519",
        source_required_path="pyproject.toml",
        venv_args=("--python", "3.11"),
        install_args=("-e", "{checkout}[torch-runtime]"),
        uses_cuda_index=True,
        host_supported=_moss_host,
        docs_path="docs/engines/moss-tts-v15.md",
        # ~7 GB CUDA torch venv now, ~16 GB of weights on first synthesis.
        required_bytes=24 * _GIB,
        dependency_bytes=8 * _GIB,
        temporary_free_bytes=8 * _GIB,
        disk_confidence="estimated",
        invalidate=_moss_invalidate,
        installed_probe=_moss_installed,
    ),
    "confucius4-tts": SidecarSpec(
        engine_id="confucius4-tts",
        display_name="Confucius4-TTS",
        repo_url="https://github.com/netease-youdao/Confucius4-TTS.git",
        tarball_url=(
            "https://github.com/netease-youdao/Confucius4-TTS/archive/"
            "4fb32c481302d8858c3aec6a1c2a8b4cea8894c0.tar.gz"
        ),
        checkout_dirname="Confucius4-TTS",
        env_var="OMNIVOICE_CONFUCIUS4_TTS_DIR",
        probe_module="confuciustts",
        # Upstream is not pip-installable; the package resolves from the
        # checkout on sys.path, exactly as the engine's sidecar imports it.
        probe_code="import sys; sys.path.insert(0, {checkout_repr}); import confuciustts",
        source_revision="4fb32c481302d8858c3aec6a1c2a8b4cea8894c0",
        # No pyproject.toml upstream: requirements.txt is its manifest.
        source_manifest="requirements.txt",
        source_required_path="setup.py",
        venv_args=("--python", "3.10"),
        install_args=("-r", "{checkout}/requirements.txt"),
        # torch==2.7.0: CPU-only from PyPI on Windows; the CUDA index supplies
        # 2.7.0+cu128, which satisfies the same pin.
        uses_cuda_index=True,
        docs_path="docs/engines/confucius4-tts.md",
        # ~7 GB venv now, ~5 GB of weights on first synthesis.
        required_bytes=14 * _GIB,
        dependency_bytes=8 * _GIB,
        temporary_free_bytes=8 * _GIB,
        disk_confidence="estimated",
        invalidate=_confucius4_invalidate,
        installed_probe=_confucius4_installed,
    ),
    "dots-tts": SidecarSpec(
        engine_id="dots-tts",
        display_name="dots.tts",
        repo_url="https://github.com/rednote-hilab/dots.tts.git",
        tarball_url=(
            "https://github.com/rednote-hilab/dots.tts/archive/"
            "32407a55228630475c48ecdb2c4e2c0f9c09e030.tar.gz"
        ),
        checkout_dirname="dots.tts",
        env_var="OMNIVOICE_DOTS_TTS_DIR",
        probe_module="dots_tts.runtime",
        source_revision="32407a55228630475c48ecdb2c4e2c0f9c09e030",
        source_required_path="constraints/recommended.txt",
        # Upstream requires-python is >=3.10,<3.13.
        venv_args=("--python", "3.11"),
        install_args=("-e", "{checkout}", "-c", "{checkout}/constraints/recommended.txt"),
        host_supported=_dots_host,
        docs_path="docs/engines/dots-tts.md",
        # ~7 GB venv now, ~9 GB checkpoint on first synthesis.
        required_bytes=18 * _GIB,
        dependency_bytes=8 * _GIB,
        temporary_free_bytes=8 * _GIB,
        disk_confidence="estimated",
        invalidate=_dots_invalidate,
        installed_probe=_dots_installed,
    ),
    # PyPI packages rather than repositories: nothing to clone, and the managed
    # root holds only the engine's own venv. The pins are the app's own
    # optional extras (a test ties the two together), so the engine runs the
    # same wheel whichever way it was installed.
    "supertonic3": SidecarSpec(
        engine_id="supertonic3",
        display_name="Supertonic-3",
        repo_url="",
        tarball_url="",
        checkout_dirname="supertonic3",
        env_var="OMNIVOICE_SUPERTONIC3_DIR",
        probe_module="supertonic",
        has_source=False,
        venv_args=("--python", "3.11"),
        install_args=("supertonic==1.3.1",),
        docs_path="docs/engines/supertonic3.md",
        # onnxruntime + numpy + huggingface_hub, no torch. The ~400 MB of
        # weights download on first synthesis into the shared HF cache.
        required_bytes=1 * _GIB,
        installed_probe=_in_app_env("supertonic"),
    ),
    "pockettts": SidecarSpec(
        engine_id="pockettts",
        display_name="PocketTTS",
        repo_url="",
        tarball_url="",
        checkout_dirname="pockettts",
        env_var="OMNIVOICE_POCKETTTS_DIR",
        probe_module="pocket_tts",
        has_source=False,
        venv_args=("--python", "3.11"),
        install_args=("pocket-tts==2.1.0",),
        cpu_torch_index=True,
        docs_path="docs/engines/pockettts.md",
        # CPU torch + scipy. The gated weights download on first use.
        required_bytes=3 * _GIB,
        installed_probe=_in_app_env("pocket_tts"),
        host_supported=_no_intel_mac(
            "PocketTTS needs a PyTorch version that has no Intel Mac build."
        ),
    ),
    # Run in a sidecar from its own venv (engines/voxcpm2_subprocess). voxcpm
    # leaves torch unpinned, so the pair is pinned here; each build of it was
    # resolved with voxcpm==2.0.3 on 2026-09-10 (Windows and Linux: +cu128 and
    # +cpu; Apple Silicon: plain). Weights download on first synthesis.
    "voxcpm2": SidecarSpec(
        engine_id="voxcpm2",
        display_name="VoxCPM2",
        repo_url="",
        tarball_url="",
        checkout_dirname="voxcpm2",
        env_var="OMNIVOICE_VOXCPM2_DIR",
        probe_module="voxcpm",
        has_source=False,
        venv_args=("--python", "3.11"),
        install_args=("voxcpm==2.0.3",),
        torch_pins=("torch==2.11.0", "torchaudio==2.11.0"),
        docs_path="docs/engines/voxcpm2.md",
        # CUDA torch (~5 GB unpacked) + transformers.
        required_bytes=10 * _GIB,
        installed_probe=_in_app_env("voxcpm"),
        host_supported=_no_intel_mac(
            "VoxCPM2 needs a PyTorch version that has no Intel Mac build."
        ),
    ),
    # Upstream is unpinned and its entry point has moved before (#1287), so the
    # install pins a reviewed commit (2026-09-06) and the sidecar drives the
    # runtime that commit ships (moss_tts_nano_runtime.NanoTTSService). Its
    # pyproject pins torch==2.7.0 exactly, so the CUDA index yields +cu128.
    "moss-tts-nano": SidecarSpec(
        engine_id="moss-tts-nano",
        display_name="MOSS-TTS-Nano",
        repo_url="https://github.com/OpenMOSS/MOSS-TTS-Nano.git",
        tarball_url=(
            "https://github.com/OpenMOSS/MOSS-TTS-Nano/archive/"
            "8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb.tar.gz"
        ),
        checkout_dirname="MOSS-TTS-Nano",
        env_var="OMNIVOICE_MOSS_TTS_NANO_DIR",
        probe_module="moss_tts_nano_runtime",
        probe_code=(
            "import moss_tts_nano_runtime, torchaudio\n"
            "if not torchaudio.list_audio_backends():\n"
            "    raise RuntimeError('torchaudio has no I/O backend (install soundfile)')"
        ),
        source_revision="8b7bcc9341b3b4ef3a3a58ba1338a7d85ff133eb",
        source_required_path="moss_tts_nano_runtime.py",
        docs_path="docs/engines/moss-tts-nano.md",
        venv_args=("--python", "3.11"),
        # Upstream's pyproject pins torchaudio==2.7.0 but ships no I/O backend
        # — torchaudio 2.7 dispatches load/save to soundfile/torchcodec, and
        # the engine cannot read its own bundled reference clip without one
        # (#2100). Pulling soundfile alongside the editable install keeps
        # every audio read inside this engine's own venv.
        install_args=("-e", "{checkout}", "soundfile"),
        install_revision="audio-backend-v1",
        uses_cuda_index=True,
        # torch 2.7 (CUDA build on NVIDIA hosts) + transformers + onnxruntime.
        # The model and its audio tokenizer download on first synthesis.
        required_bytes=8 * _GIB,
        installed_probe=_in_app_env("moss_tts_nano"),
        host_supported=_no_intel_mac(
            "MOSS-TTS-Nano pins a PyTorch version that has no Intel Mac build."
        ),
    ),
    # A reviewed commit (2026-05-25) plus the Matcha-TTS submodule it imports.
    # No pyproject: its dependencies come from the trimmed requirements file,
    # and like upstream's example.py the sidecar puts the checkout and
    # third_party/Matcha-TTS on sys.path. Only the CosyVoice 3 weights it loads
    # are downloaded (about 5.4 of 9.8 GB).
    "cosyvoice": SidecarSpec(
        engine_id="cosyvoice",
        display_name="CosyVoice 3",
        repo_url="https://github.com/FunAudioLLM/CosyVoice.git",
        tarball_url=(
            "https://github.com/FunAudioLLM/CosyVoice/archive/"
            "074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc.tar.gz"
        ),
        checkout_dirname="CosyVoice",
        env_var="OMNIVOICE_COSYVOICE_DIR",
        probe_module="cosyvoice",
        probe_code=(
            "import os, sys; c = {checkout_repr}; "
            "sys.path[:0] = [c, os.path.join(c, 'third_party', 'Matcha-TTS')]; "
            "from cosyvoice.cli.cosyvoice import AutoModel"
        ),
        source_revision="074ca6dc9e80a2f424f1f74b48bdd7d3fea531cc",
        source_manifest="requirements.txt",
        source_required_path="cosyvoice/cli/cosyvoice.py",
        extra_sources=(
            ExtraSource(
                path="third_party/Matcha-TTS",
                revision="dd9105b34bf2be2230f4aa1e4769fb586a3c824e",
                tarball_url=(
                    "https://github.com/shivammehta25/Matcha-TTS/archive/"
                    "dd9105b34bf2be2230f4aa1e4769fb586a3c824e.tar.gz"
                ),
                required_path="matcha/__init__.py",
            ),
        ),
        venv_args=("--python", "3.10"),
        install_args=("-r", _COSYVOICE_REQUIREMENTS),
        torch_pins=("torch==2.7.0", "torchaudio==2.7.0"),
        weights_repo_id="FunAudioLLM/Fun-CosyVoice3-0.5B-2512",
        weights_revision="29e01c4e8d000f4bcd70751be16fa94bf3d85a18",
        weights_subdir="pretrained_models/Fun-CosyVoice3-0.5B",
        weights_config_names=("cosyvoice3.yaml",),
        # Also a Model Catalogue download, used by the in-process engine and
        # by remote workers that run it.
        weights_catalogue_download=True,
        weights_allow_patterns=(
            "cosyvoice3.yaml", "config.json", "configuration.json",
            "campplus.onnx", "speech_tokenizer_v3.onnx",
            "llm.pt", "flow.pt", "hift.pt", "CosyVoice-BlankEN/*",
        ),
        docs_path="docs/engines/cosyvoice.md",
        # ~0.1 GB source + ~7 GB venv (CUDA torch) + ~5.4 GB weights.
        required_bytes=14 * _GIB,
        weights_bytes=6 * _GIB,
        dependency_bytes=7 * _GIB,
        installed_probe=_in_app_env("cosyvoice"),
        host_supported=_no_intel_mac(
            "CosyVoice 3 needs a PyTorch version that has no Intel Mac build."
        ),
    ),
}


class HostUnsupported(RuntimeError):
    """The one-click install cannot work on this machine. The message is a
    VoiceStudio-owned sentence from the spec, safe to show the user."""


def host_support(spec: SidecarSpec) -> tuple[bool, str]:
    """Whether *spec*'s install can work here. A probe that raises counts as
    unsupported: offering a button that fails is worse than not offering it."""
    try:
        ok, why = spec.host_supported()
    except Exception:  # noqa: BLE001
        return False, (
            f"Could not check whether {spec.display_name} can be installed on "
            f"this machine. Its guide ({spec.docs_path}) has the manual steps."
        )
    return bool(ok), (why or "")


def installable_engine_ids() -> frozenset[str]:
    """Engines that get an Install button on THIS host."""
    return frozenset(eid for eid, spec in SPECS.items() if host_support(spec)[0])


def _expand(value: str, checkout: Path) -> str:
    return value.replace("{checkout_repr}", repr(str(checkout))).replace(
        "{checkout}", str(checkout)
    )


def get_spec(engine_id: str) -> Optional[SidecarSpec]:
    return SPECS.get(engine_id)


def persistent_env_vars() -> set[str]:
    """Env vars the provisioner persists — merged into the Settings env-var
    allowlist (api.routers.system.PERSISTENT_KEYS) so users can inspect or
    clear them from the same panel as every other persisted var."""
    return {s.env_var for s in SPECS.values()}


# ── Paths ──────────────────────────────────────────────────────────────────


def managed_root(spec: SidecarSpec) -> Path:
    """Per-engine managed install root (checkout lives inside it)."""
    return Path(DATA_DIR) / "engines" / spec.engine_id


def managed_checkout(spec: SidecarSpec) -> Path:
    return managed_root(spec) / spec.checkout_dirname


def engine_venv_python(env_var: str) -> Optional[Path]:
    """The interpreter of the install *env_var* points at, if it has one.

    For engines that can live in the app's environment or in a venv of their
    own (PocketTTS, Supertonic-3): they prefer their own, and fall back to the
    app's interpreter for an install made with ``uv sync --extra``.
    """
    env_dir = os.environ.get(env_var)
    if not env_dir:
        return None
    py = _venv_python(Path(env_dir) / ".venv")
    # The interpreter alone proves nothing: a reinstall that failed partway
    # leaves it behind. The completion marker is written only after the
    # engine's import probe passed in this venv, and removed when a new
    # dependency step starts, so it is the probe's verdict without running a
    # multi-second import on every engine-list refresh.
    if not py.is_file() or not (Path(env_dir) / _INSTALL_COMPLETE_MARKER).is_file():
        return None
    return py


def _legacy_managed_checkouts(spec: SidecarSpec) -> tuple[Path, ...]:
    """App-owned predecessor checkouts retained during in-place upgrades."""
    if spec.engine_id == "indextts2":
        return (managed_root(spec) / "index-tts",)
    return ()


def _venv_python(venv_dir: Path) -> Path:
    """Venv python path, per-OS layout (Windows: Scripts/, POSIX: bin/)."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _locate_uv() -> Optional[str]:
    """Find uv: bundled (shell-set OMNIVOICE_BUNDLED_UV) first, then PATH.

    Same resolution order as engines.indextts.bootstrap._locate_uv — the
    canonical uv-resolution pattern for sidecar venvs.

    The desktop shell sets OMNIVOICE_BUNDLED_UV because PATH alone cannot find
    the packaged uv: it ships in the app's own resources directory, which is on
    nobody's PATH, and a GUI launch does not inherit the shell's PATH additions
    either (#2215). Without it every sidecar installer fails preflight on a
    clean install even though the binary is right there in the bundle.
    """
    bundled = os.environ.get("OMNIVOICE_BUNDLED_UV")
    if bundled and Path(bundled).is_file():
        return bundled
    return shutil.which("uv")


# ── uv volume co-location ──────────────────────────────────────────────────
#
# uv keeps its wheel cache (and managed Pythons) under the OS cache/data
# roots on the SYSTEM drive (%LOCALAPPDATA%\uv\cache, ~/.cache/uv, …). When a
# sidecar venv lives on a different volume — DATA_DIR on D:, portable mode —
# every wheel (the sidecar's own multi-GB torch included) is downloaded and
# unpacked on the system drive first and then cross-volume *copied* into the
# venv (hardlinks can't cross volumes): the system drive silently needs as
# much space as the whole install and fills up even though the user pointed
# the install at another drive precisely because C: was tight (Discord
# report, tarbol6457 — same class as the Tauri bootstrap fix in
# frontend/src-tauri/src/setup.rs::uv_env_overrides_for).


def _nearest_existing(path: Path) -> Path:
    """Deepest existing ancestor of *path* (the target may not exist yet)."""
    p = Path(path).absolute()
    while not p.exists():
        parent = p.parent
        if parent == p:
            break
        p = parent
    return p


def _same_volume(a: Path, b: Path) -> bool:
    """True when *a* and *b* live on the same filesystem/volume.

    Windows drive letters, POSIX mount points, and not-yet-created targets
    (compared via their nearest existing ancestor) are all handled by
    ``st_dev``. Errs on ``True`` (→ no env override) when it can't tell, so a
    probe failure can never change behavior for default installs.
    """
    try:
        return os.stat(_nearest_existing(a)).st_dev == os.stat(_nearest_existing(b)).st_dev
    except OSError:
        return True


def _default_uv_cache_root() -> Path:
    """Volume anchor of uv's default cache (mirrors uv's platform defaults).

    Only the VOLUME matters — the exact subpath never has to match uv's."""
    if sys.platform == "win32":
        return Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "uv"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Caches" / "uv"
    return Path(os.environ.get("XDG_CACHE_HOME") or Path.home() / ".cache") / "uv"


def uv_subprocess_env(cache_parent: Path) -> "dict[str, str]":
    """Environment for ``uv`` subprocesses that install into *cache_parent*'s volume.

    Always a copy of ``os.environ`` with ``UV_NO_CONFIG=1``: an engine's
    install resolves its own requirements, never VoiceStudio's. The backend
    runs inside the app's tree, so uv would otherwise discover the app's
    ``pyproject.toml`` and apply its ``[tool.uv] constraint-dependencies``
    (``torch==2.8.0``) to the engine's venv. An engine pinning another torch
    (MOSS-TTS-v1.5, Confucius4) could then never resolve, and one that pins
    none got the app's torch instead of its own. Mirrors still apply: they
    arrive as ``UV_INDEX_URL``, an environment variable, not a config file.

    When uv's default cache is on another volume than *cache_parent*, the
    copy also places the *unset* one(s) of ``UV_CACHE_DIR`` /
    ``UV_PYTHON_INSTALL_DIR`` placed inside *cache_parent*, so downloads, the
    unpacked wheel cache, managed Pythons, and the venv all stay on the
    target volume — and same-volume hardlink installs work again. The two
    variables are independent (mirrors ``uv_env_overrides_for`` in
    ``frontend/src-tauri/src/setup.rs``): a user-pinned value is never
    overridden, but pinning one must not leave the other's multi-GB state on
    the system drive.

    Canonical helper for every sidecar/engine bootstrap (like ``_locate_uv``):
    pass the directory that should hold the shared ``.uv-cache`` — typically
    the common parent of the engine venvs on that volume.
    """
    env = dict(os.environ)
    env["UV_NO_CONFIG"] = "1"
    if _same_volume(cache_parent, _default_uv_cache_root()):
        return env
    if not env.get("UV_CACHE_DIR"):  # explicit user choice always wins
        env["UV_CACHE_DIR"] = str(Path(cache_parent) / ".uv-cache")
    if not env.get("UV_PYTHON_INSTALL_DIR"):
        env["UV_PYTHON_INSTALL_DIR"] = str(Path(cache_parent) / ".uv-python")
    return env


# ── Disk preflight ─────────────────────────────────────────────────────────


def _dir_size_bytes(path: Path) -> int:
    """Best-effort recursive size (bytes already spent by a partial install)."""
    total = 0
    try:
        for root, _dirs, files in os.walk(path):
            for f in files:
                try:
                    total += os.path.getsize(os.path.join(root, f))
                except OSError:
                    continue
    except OSError:
        pass  # unreadable dir — treat as zero bytes spent
    return total


def _preserved_install_bytes(spec: SidecarSpec, checkout: Path) -> tuple[int, int]:
    """Return bytes preserved for the final install and dependency peak.

    Resumable weights reduce the final download requirement, but they do not
    reduce uv's separate environment-build peak. Only source and a usable
    existing venv count against that peak.
    """
    if not _source_present(spec, checkout):
        return 0, 0

    weights_dir = checkout / spec.weights_subdir
    weights = _dir_size_bytes(weights_dir) if spec.weights_repo_id else 0
    venv_dir = checkout / ".venv"
    venv = _dir_size_bytes(venv_dir)
    source = max(0, _dir_size_bytes(checkout) - weights - venv)
    usable_venv = venv if _venv_python(venv_dir).is_file() else 0
    return source + usable_venv + weights, source + usable_venv


def disk_free_bytes(path: Path) -> int:
    """Free bytes on the volume backing *path* (nearest existing ancestor).
    Never raises; 0 when the volume can't be probed."""
    try:
        p = path.resolve()
        while not p.exists():
            parent = p.parent
            if parent == p:
                break
            p = parent
        return int(shutil.disk_usage(str(p)).free)
    except Exception:
        return 0


def disk_space_error(spec: SidecarSpec) -> Optional[str]:
    """Actionable message when the estimated remaining install won't fit
    (needs X + headroom Y, have Z — same shape as the model-install guard);
    ``None`` when it fits or the volume can't be probed."""
    root = managed_root(spec)
    # A preserved predecessor is not a partial copy of the new install: the
    # upgrade needs its full space until the new sidecar is verified.
    checkout = managed_checkout(spec)
    # Credit only bytes the later steps preserve. An invalid layout or revision
    # marker makes _step_fetch_source delete the whole checkout.
    preserved, dependency_peak_credit = _preserved_install_bytes(spec, checkout)
    remaining = max(0, spec.required_bytes - preserved)
    if spec.temporary_free_bytes is not None:
        # Resumable model weights are unrelated to uv's dependency-build peak.
        remaining = max(
            remaining,
            max(0, spec.temporary_free_bytes - dependency_peak_credit),
        )
    free = disk_free_bytes(root)
    if free <= 0:
        return None  # can't probe → never block on missing information
    required = remaining + MIN_FREE_GB * _GIB
    if free >= required:
        return None

    def _gb(n: int) -> str:
        return f"{n / _GIB:.1f} GB"

    return (
        f"Not enough disk space to install {spec.display_name}: it needs about "
        f"{_gb(remaining)} plus {MIN_FREE_GB} GB free headroom ({_gb(required)} total), "
        f"but only {_gb(free)} is free at {root}. Free up space and retry."
    )


# ── Job state ──────────────────────────────────────────────────────────────

STEP_IDS = (
    "preflight",
    "fetch_source",
    "create_venv",
    "install_deps",
    "verify",
    "fetch_weights",
    "persist",
)

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()

# Guards each job's log deque: the worker thread appends while the status
# poll copies it, and list() over a deque raises RuntimeError if it mutates
# mid-iteration. One module-level lock is plenty — appends are tiny and at
# most one job runs per engine.
_log_lock = threading.Lock()


class _StepError(Exception):
    """Install-step failure carrying user-facing remediation text."""

    def __init__(self, message: str, remediation: str):
        super().__init__(message)
        self.remediation = remediation


def _new_job(engine_id: str) -> dict:
    return {
        "engine_id": engine_id,
        "state": "running",
        "steps": [{"id": s, "state": "pending", "detail": None} for s in STEP_IDS],
        "log": deque(maxlen=_LOG_MAX_LINES),
        "error": None,
        "remediation": None,
        "weights_progress": None,
        "started_at": time.time(),
        "finished_at": None,
    }


def _job_step(job: dict, step_id: str) -> dict:
    return next(s for s in job["steps"] if s["id"] == step_id)


def _log(job: dict, line: str) -> None:
    line = line.rstrip()
    if line:
        with _log_lock:
            job["log"].append(line)
        logger.info("[%s install] %s", job["engine_id"], line)


def _serialize_job(job: Optional[dict]) -> Optional[dict]:
    if job is None:
        return None
    out = dict(job)
    with _log_lock:
        out["log"] = list(job["log"])
    out["steps"] = [dict(s) for s in job["steps"]]
    return out


def get_status(engine_id: str) -> dict:
    """Install state + last/current job for one engine. Cheap (file probes)."""
    spec = get_spec(engine_id)
    if spec is None:
        raise KeyError(engine_id)
    with _jobs_lock:
        job = _serialize_job(_jobs.get(engine_id))
    installed = _healthy(spec)
    checkout = managed_checkout(spec)
    env_dir = os.environ.get(spec.env_var)
    return {
        "engine_id": engine_id,
        "installed": installed,
        # True when the on-disk install is the app-managed one (uninstallable
        # from the app). A user's own clone is never "managed".
        "managed": bool(
            (checkout.is_dir() and (not env_dir or Path(env_dir) == checkout))
            or (env_dir and Path(env_dir) in _legacy_managed_checkouts(spec))
        ),
        "install_dir": env_dir or (str(checkout) if checkout.is_dir() else None),
        "job": job,
    }


def _safe_installed(spec: SidecarSpec) -> bool:
    try:
        return bool(spec.installed_probe())
    except Exception:
        return False


def _user_managed_dir(spec: SidecarSpec) -> Optional[Path]:
    """The user's own install dir when the env var points anywhere but the
    app-managed checkout; None for managed/unset (ours to provision)."""
    env_dir = os.environ.get(spec.env_var)
    managed_paths = (managed_checkout(spec), *_legacy_managed_checkouts(spec))
    if env_dir and Path(env_dir) not in managed_paths:
        return Path(env_dir)
    return None


def _healthy(spec: SidecarSpec) -> bool:
    """A COMPLETE install: for a user-managed dir, trust the engine's own
    probe (their clone, their layout); for the app-managed install require
    the venv AND the fully-downloaded weights, so a partial install repairs
    instead of reporting already_installed."""
    if _user_managed_dir(spec) is not None:
        return _safe_installed(spec)
    checkout = managed_checkout(spec)
    if not checkout.is_dir():
        # A working app-managed predecessor stays available to the engine,
        # but the install endpoint must still offer the reviewed 2.5 upgrade.
        env_dir = os.environ.get(spec.env_var)
        if env_dir and Path(env_dir) in _legacy_managed_checkouts(spec):
            return False
        # No managed install at all. A legacy install may still exist (e.g.
        # IndexTTS's old lazy-bootstrap venv under backend/engines/) — trust
        # the engine's own probe so we never re-provision over a working one.
        return _safe_installed(spec)
    if not _source_present(spec, checkout):
        return False
    if not _venv_python(checkout / ".venv").is_file():
        return False
    if spec.weights_repo_id and not _weights_present(spec):
        return False
    # Weights left by an earlier run do not prove this run's dependencies
    # finished; only the marker the import probe writes does. IndexTTS
    # predates the marker and keeps its weights check, so no existing install
    # is asked to reinstall.
    if not spec.requires_install_marker:
        return True
    marker = checkout / _INSTALL_COMPLETE_MARKER
    if not spec.install_revision:
        return marker.is_file()
    try:
        return marker.read_text(encoding="utf-8").splitlines() == [
            spec.probe_module, spec.install_revision,
        ]
    except (OSError, UnicodeError):
        return False


def _persist(spec: SidecarSpec) -> None:
    """Point the engine at the managed checkout: process env for immediate
    use, prefs.json ``env.*`` for the next launch, and invalidate the
    engine's memoised venv resolution so it re-probes without a restart."""
    checkout = managed_checkout(spec)
    os.environ[spec.env_var] = str(checkout)
    from core import prefs
    prefs.set_(f"env.{spec.env_var}", str(checkout))
    try:
        spec.invalidate()
    except Exception:
        pass  # best-effort cache invalidation — the env var is already set


def start_install(engine_id: str) -> dict:
    """Start (or report) the install job for *engine_id*.

    Returns ``{"status": "started"|"already_running"|"already_installed", ...}``.
    Raises KeyError for an engine with no sidecar spec.
    """
    spec = get_spec(engine_id)
    if spec is None:
        raise KeyError(engine_id)
    ok, why = host_support(spec)
    if not ok:
        raise HostUnsupported(why)
    with _jobs_lock:
        existing = _jobs.get(engine_id)
        if existing and existing["state"] == "running":
            return {"status": "already_running", "engine": engine_id}
        # A healthy install (user-managed or app-managed) never reinstalls;
        # a PARTIAL managed install falls through so the job repairs it.
        if _healthy(spec):
            # Self-heal: a healthy MANAGED install whose env var was lost
            # (e.g. prefs.json wiped) just needs re-pointing, not a reinstall.
            if (
                _user_managed_dir(spec) is None
                and _venv_python(managed_checkout(spec) / ".venv").is_file()
                and not _safe_installed(spec)
            ):
                _persist(spec)
            return {"status": "already_installed", "engine": engine_id}
        job = _new_job(engine_id)
        _jobs[engine_id] = job
    th = threading.Thread(
        target=_run_install, args=(spec, job),
        name=f"sidecar-install-{engine_id}", daemon=True,
    )
    th.start()
    return {"status": "started", "engine": engine_id}


def uninstall(engine_id: str) -> dict:
    """Remove the app-managed install and clear the persisted path.

    Refuses to touch a user-managed install (env var pointing anywhere but
    the managed checkout) — those were never ours to delete.
    """
    spec = get_spec(engine_id)
    if spec is None:
        raise KeyError(engine_id)
    with _jobs_lock:
        job = _jobs.get(engine_id)
        if job and job["state"] == "running":
            return {"status": "install_in_progress", "engine": engine_id}
    env_dir = os.environ.get(spec.env_var)
    checkout = managed_checkout(spec)
    managed_paths = (checkout, *_legacy_managed_checkouts(spec))
    if env_dir and Path(env_dir) not in managed_paths:
        return {
            "status": "not_managed",
            "engine": engine_id,
            "detail": (
                f"{spec.display_name} points at {env_dir}, which VoiceStudio did not "
                f"install. Remove that directory yourself if you want it gone, or "
                f"clear {spec.env_var} in Settings."
            ),
        }
    root = managed_root(spec)
    removed = root.is_dir()
    shutil.rmtree(root, ignore_errors=True)
    if env_dir:  # only ever the managed checkout at this point
        os.environ.pop(spec.env_var, None)
    from core import prefs
    if prefs.get(f"env.{spec.env_var}") in {str(path) for path in managed_paths}:
        prefs.delete(f"env.{spec.env_var}")
    try:
        spec.invalidate()
    except Exception:
        pass  # best-effort cache invalidation — uninstall already succeeded
    with _jobs_lock:
        _jobs.pop(engine_id, None)
    return {"status": "uninstalled" if removed else "not_installed", "engine": engine_id}


# ── Worker ─────────────────────────────────────────────────────────────────


def _run_install(spec: SidecarSpec, job: dict) -> None:
    step_fns: list[tuple[str, Callable[[SidecarSpec, dict], None]]] = [
        ("preflight", _step_preflight),
        ("fetch_source", _step_fetch_source),
        ("create_venv", _step_create_venv),
        ("install_deps", _step_install_deps),
        ("verify", _step_verify),
        ("fetch_weights", _step_fetch_weights),
        ("persist", _step_persist),
    ]
    try:
        for step_id, fn in step_fns:
            step = _job_step(job, step_id)
            step["state"] = "running"
            try:
                fn(spec, job)
            except _StepError:
                step["state"] = "error"
                raise
            except Exception as exc:  # noqa: BLE001 — surfaced into the job
                step["state"] = "error"
                raise _StepError(
                    f"{type(exc).__name__}: {exc}",
                    "Re-run the install — it resumes from where it stopped. If it "
                    f"keeps failing, see {spec.docs_path} for the manual steps.",
                ) from exc
            if step["state"] == "running":
                step["state"] = "done"
        job["state"] = "succeeded"
        _log(job, f"{spec.display_name} installed successfully.")
    except _StepError as exc:
        job["state"] = "failed"
        job["error"] = str(exc)
        job["remediation"] = exc.remediation
        _log(job, f"FAILED: {exc}")
    finally:
        job["finished_at"] = time.time()


def _step_preflight(spec: SidecarSpec, job: dict) -> None:
    if _locate_uv() is None:
        raise _StepError(
            "uv was not found (checked the bundled path via OMNIVOICE_BUNDLED_UV, "
            "then PATH).",
            "Install uv from https://docs.astral.sh/uv/ and relaunch VoiceStudio, or "
            "set OMNIVOICE_BUNDLED_UV to the absolute path of a uv binary.",
        )
    err = disk_space_error(spec)
    if err:
        raise _StepError(err, "Free up disk space (or move VoiceStudio's data directory "
                              "to a larger volume) and retry.")
    managed_root(spec).mkdir(parents=True, exist_ok=True)
    _job_step(job, "preflight")["detail"] = "uv found, disk space OK"
    _log(job, "Preflight OK — uv resolved and enough free disk space.")


def _step_fetch_source(spec: SidecarSpec, job: dict) -> None:
    _fetch_main_source(spec, job)
    if spec.has_source and spec.extra_sources:
        _ensure_extra_sources(spec, job, managed_checkout(spec))


def _fetch_main_source(spec: SidecarSpec, job: dict) -> None:
    step = _job_step(job, "fetch_source")
    checkout = managed_checkout(spec)
    if not spec.has_source:
        checkout.mkdir(parents=True, exist_ok=True)
        step["state"] = "done"
        step["detail"] = "PyPI package, no source to fetch"
        return
    if _source_present(spec, checkout):
        step["state"] = "done"
        step["detail"] = "source already present"
        _log(job, f"Source already present at {checkout} — skipping fetch.")
        return
    if checkout.exists():
        # Half-fetched checkout — repair by refetching. Superseded managed
        # generations live at a different path and remain usable until this
        # replacement has completed successfully.
        _log(job, f"Removing incomplete or outdated checkout at {checkout} …")
        shutil.rmtree(checkout, ignore_errors=True)

    git = shutil.which("git")
    if git:
        _log(job, f"Cloning {spec.repo_url} (git, depth 1) …")
        clone_argv = [git, "clone", "--depth", "1"]
        if spec.repo_ref:
            clone_argv.extend(["--branch", spec.repo_ref])
        clone_argv.extend([spec.repo_url, str(checkout)])
        rc = _run_logged(job, clone_argv, timeout=_GIT_CLONE_TIMEOUT_S)
        if rc == 0 and spec.source_revision:
            rc = _run_logged(
                job,
                [git, "-C", str(checkout), "checkout", "--detach", spec.source_revision],
                timeout=_GIT_CLONE_TIMEOUT_S,
            )
        if rc == 0 and _source_layout_ok(spec, checkout):
            _write_source_marker(spec, checkout)
            step["detail"] = "git clone"
            return
        _log(job, f"git clone failed (exit {rc}) — falling back to source tarball.")
        shutil.rmtree(checkout, ignore_errors=True)
    else:
        _log(job, "git not found — using the source-tarball fallback.")

    _fetch_tarball(spec, job, checkout)
    if not _source_layout_ok(spec, checkout):
        raise _StepError(
            f"Fetched source at {checkout} has no {spec.source_manifest} — the download "
            "appears incomplete or the upstream layout changed.",
            "Re-run the install; if it keeps failing, clone the repository "
            f"manually and set {spec.env_var} to the clone (see the engine docs).",
        )
    _write_source_marker(spec, checkout)
    step["detail"] = "source tarball"


_SOURCE_REVISION_MARKER = ".voicestudio_source_revision"
# Written once the import probe passes. For an engine with no weights
# download, the venv interpreter existing proves nothing: a dependency
# install that died halfway leaves one behind.
_INSTALL_COMPLETE_MARKER = ".voicestudio_install_complete"


def _source_layout_ok(spec: SidecarSpec, checkout: Path) -> bool:
    if not (checkout / spec.source_manifest).is_file():
        return False
    return not spec.source_required_path or (checkout / spec.source_required_path).is_file()


def _write_source_marker(spec: SidecarSpec, checkout: Path) -> None:
    if spec.source_revision:
        (checkout / _SOURCE_REVISION_MARKER).write_text(
            f"{spec.source_revision}\n", encoding="utf-8",
        )


def _source_present(spec: SidecarSpec, checkout: Path) -> bool:
    if not spec.has_source:
        return checkout.is_dir()
    if not _source_layout_ok(spec, checkout):
        return False
    if not spec.source_revision:
        return True
    try:
        marker = (checkout / _SOURCE_REVISION_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return marker == spec.source_revision


def _download_and_extract(job: dict, url: str, dest: Path, work_root: Path, env_var: str) -> None:
    """Download a GitHub source tarball and move its one top-level directory
    to *dest* (no git required).

    Extraction is member-validated (no absolute paths / parent escapes) and
    never uses symlinks, so it behaves identically on Windows.
    """
    import httpx

    root = work_root
    root.mkdir(parents=True, exist_ok=True)
    _log(job, f"Downloading {url} …")
    fd, tmp_tar = tempfile.mkstemp(suffix=".tar.gz", dir=str(root))
    try:
        with os.fdopen(fd, "wb") as out:
            with httpx.stream(
                "GET", url, follow_redirects=True,
                timeout=_TARBALL_TIMEOUT_S,
            ) as resp:
                resp.raise_for_status()
                for chunk in resp.iter_bytes():
                    out.write(chunk)
        _log(job, "Extracting source tarball …")
        with tempfile.TemporaryDirectory(dir=str(root)) as tmp_dir:
            with tarfile.open(tmp_tar, "r:gz") as tf:
                try:
                    tf.extractall(tmp_dir, members=_members_without_links(tf), filter="data")
                except TypeError:  # pragma: no cover — pre-filter= interpreters
                    _safe_extract_members(tf, tmp_dir)
            entries = [p for p in Path(tmp_dir).iterdir() if p.is_dir()]
            if len(entries) != 1:
                raise _StepError(
                    f"Unexpected tarball layout ({len(entries)} top-level dirs).",
                    "Re-run the install; if it keeps failing, clone the repository "
                    f"manually and set {env_var} (see the engine docs).",
                )
            # os.replace-style move keeps this atomic-ish on the same volume.
            shutil.move(str(entries[0]), str(dest))
    finally:
        try:
            os.unlink(tmp_tar)
        except OSError:
            pass  # temp tarball already gone / locked — harmless leftover


def _fetch_tarball(spec: SidecarSpec, job: dict, checkout: Path) -> None:
    """Download + extract the engine's GitHub source tarball (no git required)."""
    _download_and_extract(job, spec.tarball_url, checkout, managed_root(spec), spec.env_var)


def _extra_source_present(extra: ExtraSource, dest: Path) -> bool:
    if not (dest / extra.required_path).is_file():
        return False
    try:
        marker = (dest / _SOURCE_REVISION_MARKER).read_text(encoding="utf-8").strip()
    except OSError:
        return False
    return marker == extra.revision


def _ensure_extra_sources(spec: SidecarSpec, job: dict, checkout: Path) -> None:
    for extra in spec.extra_sources:
        dest = checkout / extra.path
        if _extra_source_present(extra, dest):
            continue
        shutil.rmtree(dest, ignore_errors=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        _log(job, f"Fetching {extra.path} at {extra.revision[:8]} …")
        _download_and_extract(job, extra.tarball_url, dest, managed_root(spec), spec.env_var)
        if not (dest / extra.required_path).is_file():
            raise _StepError(
                f"Fetched {extra.path} has no {extra.required_path}; the download "
                "appears incomplete or the upstream layout changed.",
                "Re-run the install; if it keeps failing, see the engine docs.",
            )
        (dest / _SOURCE_REVISION_MARKER).write_text(f"{extra.revision}\n", encoding="utf-8")


def _members_without_links(tf: "tarfile.TarFile") -> "list[tarfile.TarInfo]":
    """Every member except links.

    The stdlib "data" filter raises on a link to an absolute path, and the
    pinned Matcha-TTS tarball (a CosyVoice submodule) ships one: ``data``
    points at its author's own training-data folder. That aborted the whole
    fetch. No installer here uses a link from a source tree, symlinks need
    privileges on Windows, and the pre-3.11.4 path below already drops them,
    so both paths behave the same. Everything that IS extracted still goes
    through the "data" filter.
    """
    return [m for m in tf.getmembers() if not (m.issym() or m.islnk())]


def _safe_extract_members(tf: "tarfile.TarFile", dest: str) -> None:
    """Tar-slip-guarded extraction for interpreters without
    ``extractall(filter="data")`` (Python < 3.11.4).

    Mirrors what the "data" filter enforces: only regular files and
    directories (no symlinks/hardlinks/devices — also keeps Windows
    behaviour identical), no absolute paths, and every resolved target must
    stay inside *dest*.
    """
    dest_abs = os.path.abspath(dest)
    for member in tf.getmembers():
        if not (member.isreg() or member.isdir()):
            continue  # drop symlinks/hardlinks/devices/fifos
        name = member.name
        if name.startswith(("/", "\\")) or ".." in name.replace("\\", "/").split("/"):
            continue  # absolute path or parent-dir escape
        target = os.path.abspath(os.path.join(dest, name))
        if os.path.commonpath([dest_abs, target]) != dest_abs:
            continue  # resolved outside the extraction dir
        tf.extract(member, dest)


def _step_create_venv(spec: SidecarSpec, job: dict) -> None:
    step = _job_step(job, "create_venv")
    checkout = managed_checkout(spec)
    venv_dir = checkout / ".venv"
    py = _venv_python(venv_dir)
    if py.is_file():
        step["state"] = "done"
        step["detail"] = "venv already present"
        _log(job, f"Venv already present at {venv_dir} — skipping.")
        return
    uv = _locate_uv()
    _log(job, f"Creating venv at {venv_dir} …")
    # Keep uv's cache on the engines volume (D:-install class) — see
    # uv_subprocess_env. The cache parent is the shared engines root, so
    # every sidecar engine reuses one cache.
    uv_env = uv_subprocess_env(Path(DATA_DIR) / "engines")
    rc = _run_logged(job, [uv, "venv", str(venv_dir), *spec.venv_args],
                     timeout=_UV_VENV_TIMEOUT_S, env=uv_env)
    if rc != 0 or not py.is_file():
        raise _StepError(
            f"uv venv failed (exit {rc}) at {venv_dir}.",
            "Check the log above for the uv error; free disk space or fix "
            "permissions on the data directory, then re-run the install.",
        )
    step["detail"] = "venv created"


def _step_install_deps(spec: SidecarSpec, job: dict) -> None:
    """`uv pip install -e <checkout>` into the dedicated venv.

    Deliberately NOT `uv sync` — sync would apply the sidecar's lockfile
    semantics; `uv pip install -e` resolves the sidecar's own pins
    (e.g. transformers<5) inside ITS venv, never touching the parent app.
    Idempotent: re-running repairs a partial dependency set.
    """
    checkout = managed_checkout(spec)
    py = _venv_python(checkout / ".venv")
    # A reinstall that fails must not leave the previous run's marker.
    (checkout / _INSTALL_COMPLETE_MARKER).unlink(missing_ok=True)
    uv = _locate_uv()
    _log(job, f"Installing {spec.display_name} into its venv (this can take several minutes) …")
    target = [_expand(arg, checkout) for arg in spec.install_args]
    if spec.torch_pins:
        target += _torch_pin_args(spec)
    elif spec.cpu_torch_index:
        from core.torch_indexes import UV_PIP_CPU_ARGS
        target += list(UV_PIP_CPU_ARGS)
    elif spec.uses_cuda_index and _host_family() == "cuda":
        from core.torch_indexes import UV_PIP_CU128_ARGS
        target += list(UV_PIP_CU128_ARGS)
    # Always `--python <this engine's venv>`: the install can only ever land in
    # the venv this engine owns, never the app's interpreter.
    rc = _run_logged(
        job,
        [uv, "pip", "install", "--python", str(py), *target],
        timeout=_UV_PIP_INSTALL_TIMEOUT_S,
        env=uv_subprocess_env(Path(DATA_DIR) / "engines"),
    )
    if rc != 0:
        hint = (
            "Usually a network hiccup — re-run the install to resume. Behind a "
            "proxy, set HTTPS_PROXY in Settings → Environment first."
        )
        if sys.platform == "win32":
            # Packages built from source (openai-whisper, for CosyVoice) nest
            # deep build folders under uv's cache; past Windows' 260-character
            # limit the build fails with "No such file or directory".
            hint += (
                " If the log shows \"No such file or directory\" while building a "
                "package, the path is too long for Windows: turn on Windows "
                "long-path support (the LongPathsEnabled setting) and re-run."
            )
        raise _StepError(f"uv pip install failed (exit {rc}).", hint)
    _job_step(job, "install_deps")["detail"] = "dependencies installed"


def _step_verify(spec: SidecarSpec, job: dict) -> None:
    checkout = managed_checkout(spec)
    py = _venv_python(checkout / ".venv")
    _log(job, f"Verifying `import {spec.probe_module}` inside the venv …")
    try:
        probe = (
            _expand(spec.probe_code, checkout)
            if spec.probe_code
            else f"import {spec.probe_module}"
        )
        proc = subprocess.run(
            [str(py), "-c", probe],
            capture_output=True, timeout=_IMPORT_PROBE_TIMEOUT_S,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise _StepError(
            f"Import probe failed to run: {exc}",
            "Re-run the install; if it keeps failing, delete the engine in "
            "Model Catalogue and install again.",
        ) from exc
    if proc.returncode != 0:
        tail = proc.stderr.decode("utf-8", errors="replace")[-500:]
        raise _StepError(
            f"`import {spec.probe_module}` failed in the new venv: {tail}",
            "Re-run the install — dependency resolution resumes and repairs "
            "partial installs. If it keeps failing, use the manual install in "
            "the engine docs.",
        )
    _job_step(job, "verify")["detail"] = f"import {spec.probe_module} OK"
    marker_text = f"{spec.probe_module}\n"
    if spec.install_revision:
        marker_text += f"{spec.install_revision}\n"
    (checkout / _INSTALL_COMPLETE_MARKER).write_text(marker_text, encoding="utf-8")
    _log(job, "Venv verified.")


# Written into the weights dir after snapshot_download COMPLETES. A partial
# multi-shard download can leave a config + several plausible shards on
# disk, so file heuristics alone would declare a killed-mid-download install
# healthy and never resume it (the sidecar edition of #352). Only this
# installer writes the marker; user-managed clones never hit this path.
_WEIGHTS_COMPLETE_MARKER = ".omnivoice_weights_complete"


def _weights_present(spec: SidecarSpec) -> bool:
    """True only for a COMPLETED weights download: the completion marker
    plus a sanity floor (the expected config + one ≥5 MB weight file — the same
    truncated-download floor the model store uses)."""
    wdir = managed_checkout(spec) / spec.weights_subdir
    try:
        marker = (wdir / _WEIGHTS_COMPLETE_MARKER).read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    expected = [spec.weights_repo_id or "", spec.weights_revision or ""]
    actual = marker[:2] if len(marker) >= 2 else marker + [""]
    if actual != expected:
        return False
    return _weights_floor_ok(wdir, config_names=spec.weights_config_names)


def _weights_floor_ok(wdir: Path, *, config_names: tuple[str, ...] = ("config.yaml",)) -> bool:
    if not any((wdir / name).is_file() for name in config_names):
        return False
    floor = 5 * 1024 * 1024
    try:
        for root, _dirs, files in os.walk(wdir):
            for f in files:
                try:
                    if os.path.getsize(os.path.join(root, f)) >= floor:
                        return True
                except OSError:
                    continue
    except OSError:
        pass  # unreadable weights dir — treat as not present
    return False


def _step_fetch_weights(spec: SidecarSpec, job: dict) -> None:
    step = _job_step(job, "fetch_weights")
    if not spec.weights_repo_id:
        step["state"] = "skipped"
        step["detail"] = "engine has no bundled-weights requirement"
        return
    if _weights_present(spec):
        step["state"] = "done"
        step["detail"] = "weights already present"
        _log(job, "Model weights already present — skipping download.")
        return

    wdir = managed_checkout(spec) / spec.weights_subdir
    wdir.mkdir(parents=True, exist_ok=True)
    _log(job, f"Downloading {spec.weights_repo_id} → {wdir} (several GB — resumable) …")

    from huggingface_hub import snapshot_download
    from services import endpoint_race
    from services.token_resolver import resolve as resolve_token
    from utils import hf_progress

    # Mirror per-file byte progress into the job so the polling UI can show
    # it — same tqdm hook the model store's SSE feed uses.
    def _listener(ev: dict) -> None:
        try:
            # Only mirror events for OUR repo — a concurrent model-store
            # download must not scribble its progress into this job.
            if ev.get("repo_id") not in (None, spec.weights_repo_id):
                return
            job["weights_progress"] = {
                "filename": ev.get("filename"),
                "downloaded": ev.get("downloaded"),
                "total": ev.get("total"),
                "pct": ev.get("pct"),
            }
        except Exception:
            pass  # progress mirroring is advisory — never break the download

    listener_id = hf_progress.register_listener(_listener)
    repo_token = hf_progress.current_repo_id.set(spec.weights_repo_id)
    try:
        # Tracks the repo's default branch on purpose (same policy as every
        # other model download in the app — see setup/download.py): the
        # source checkout is unpinned upstream `main` anyway, and hf_hub
        # checksum-verifies each artifact. Hence the B615 waiver below.
        # Unwrap to the bearer string: snapshot_download takes `token: str |
        # None`, and a ResolvedToken record is ignored in favour of ambient
        # discovery, so gated engine weights 401 for a user whose token lives
        # in VoiceStudio's settings rather than HF's own cache (#2163).
        _resolved = resolve_token()
        kwargs: dict = {
            "repo_id": spec.weights_repo_id,
            "local_dir": str(wdir),
            "token": _resolved.token if _resolved else None,
        }
        if spec.weights_revision:
            kwargs["revision"] = spec.weights_revision
        if spec.weights_allow_patterns:
            kwargs["allow_patterns"] = list(spec.weights_allow_patterns)
        endpoint = endpoint_race.effective_endpoint()
        if endpoint:
            kwargs["endpoint"] = endpoint
        tqdm_cls = hf_progress.tracked_tqdm_class()
        if tqdm_cls is not None:
            kwargs["tqdm_class"] = tqdm_cls
        try:
            snapshot_download(**kwargs)  # nosec B615 — deliberate default-branch policy, see above
        except Exception as exc:
            raise _StepError(
                f"Model weight download failed: {exc}",
                "Re-run the install — the download resumes where it stopped. "
                "Check Settings → Network (HF endpoint / proxy) if it keeps failing.",
            ) from exc
    finally:
        hf_progress.unregister_listener(listener_id)
        hf_progress.current_repo_id.reset(repo_token)

    if not _weights_floor_ok(wdir, config_names=spec.weights_config_names):
        raise _StepError(
            "Weight download finished but no plausible weight files were found — "
            "the download was likely interrupted.",
            "Re-run the install to resume the download.",
        )
    # snapshot_download returned AND the sanity floor holds → mark complete,
    # so _weights_present/_healthy stop treating this dir as a partial.
    (wdir / _WEIGHTS_COMPLETE_MARKER).write_text(
        f"{spec.weights_repo_id}\n{spec.weights_revision or ''}\n{time.time():.0f}\n",
        encoding="utf-8",
    )
    step["detail"] = "weights downloaded"
    _log(job, "Model weights downloaded.")


def _step_persist(spec: SidecarSpec, job: dict) -> None:
    _persist(spec)
    _job_step(job, "persist")["detail"] = f"{spec.env_var}={managed_checkout(spec)}"
    _log(job, f"Saved {spec.env_var} — the engine is ready to use, no restart needed.")


# ── Subprocess runner with live log capture ────────────────────────────────


def _install_containment_kwargs() -> dict:
    """Nested process-group/Job ownership is supplied by ``spawn_owned``."""
    return {}


def _run_logged(job: dict, argv: list[str], *, timeout: float,
                env: "dict[str, str] | None" = None) -> int:
    """Run *argv*, streaming combined stdout+stderr lines into the job log.

    Returns the exit code; -1 on timeout (process tree killed) or spawn
    failure. argv-list only — never a shell string — so paths with spaces
    are safe on every platform. ``env=None`` inherits the parent environment.

    The stdout drain runs on its own daemon thread and the main flow blocks
    on ``proc.wait(timeout=…)``. That bounds the step even when a grandchild
    (uv resolver worker, git helper) inherits the pipe and outlives the
    killed child — a blocking ``for line in proc.stdout`` on this thread
    would hang past the timeout waiting for pipe EOF.
    """
    # ``spawn_owned`` creates the local timeout group/Job before the operation
    # starts. POSIX links it to backend death through a control pipe; Windows
    # retains a kill-on-close Job handle in this backend process.
    popen_kwargs = _install_containment_kwargs()
    try:
        proc = spawn_owned(
            argv,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            encoding="utf-8",
            errors="replace",
            env=env,
            **popen_kwargs,
        )
    except OSError as exc:
        _log(job, f"failed to spawn {argv[0]}: {exc}")
        return -1

    def _drain() -> None:
        try:
            assert proc.stdout is not None
            for line in proc.stdout:
                _log(job, line)
        except (OSError, ValueError):
            pass  # pipe closed by the timeout kill — nothing left to read

    drain = threading.Thread(target=_drain, daemon=True)
    drain.start()
    try:
        rc = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        _kill_tree(proc)
        _log(job, f"process timed out after {timeout:.0f}s — killed")
        return -1
    drain.join(5.0)  # give the drain a moment to flush the tail
    return rc if rc is not None else -1


def _kill_tree(proc: "subprocess.Popen") -> None:
    """Kill an operation through its stable nested group/Job owner."""
    if isinstance(proc, (OwnedPopen, WindowsJobPopen)):
        # The retained supervisor/process-group or nested Job is the stable
        # per-operation owner.  Do not fall back to a direct PID kill.
        proc.kill()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            return
        return
    # A test double or a legacy caller without the nested owner can only be
    # stopped through its stable direct-process handle.
    try:
        proc.kill()
    except OSError:
        pass  # process already exited


__all__ = [
    "SPECS",
    "SidecarSpec",
    "disk_space_error",
    "get_spec",
    "get_status",
    "managed_checkout",
    "managed_root",
    "persistent_env_vars",
    "start_install",
    "uninstall",
]
