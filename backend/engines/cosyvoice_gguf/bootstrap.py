"""Pinned runtime discovery and verified native-device selection."""

from __future__ import annotations

import os
import re
import subprocess  # nosec B404 -- fixed probe arguments
import time
from dataclasses import dataclass, replace
from pathlib import Path

from core.config import DATA_DIR
from services.hf_revisions import revision_for

ENGINE_ID = "cosyvoice-gguf"
SOURCE_REVISION = "67d45feb2f654bbc66cca25bff78464b3ec08fa1"
HF_MODEL_REPO = "Lourdle/Fun-CosyVoice3-0.5B-2512-GGUF"
# One pin for downloads, preflight and this engine: the central registry.
HF_MODEL_REVISION = revision_for(HF_MODEL_REPO)
MODEL_FILE = "CosyVoice3-2512_Q4_K_M.gguf"
FRONTEND_FILES = (
    "frontend-onnx/campplus.int8.onnx",
    "frontend-onnx/speech_tokenizer_v3.int8.onnx",
)
DIR_ENV = "OMNIVOICE_COSYVOICE_GGUF_DIR"
BIN_ENV = "OMNIVOICE_COSYVOICE_GGUF_SERVER"
CLI_ENV = "OMNIVOICE_COSYVOICE_GGUF_CLI"
MODEL_ENV = "OMNIVOICE_COSYVOICE_GGUF_MODEL"
FRONTEND_ENV = "OMNIVOICE_COSYVOICE_GGUF_FRONTEND"
BACKEND_ENV = "OMNIVOICE_COSYVOICE_GGUF_BACKEND"
MODEL_ID = "cosyvoice3-2512"
SAMPLE_RATE = 24000


@dataclass(frozen=True)
class NativeBackend:
    name: str
    device_name: str
    target: str
    fallback_reason: str | None = None
    #: Why the probe reported no Vulkan row (exit code, the runtime's own
    #: Vulkan line), so a CPU fallback names its cause.
    probe_detail: str | None = None


def managed_dir() -> Path:
    return Path(DATA_DIR) / "engines" / ENGINE_ID / "runtime"


def _binary(kind: str) -> Path:
    env = BIN_ENV if kind == "server" else CLI_ENV
    name = f"cosyvoice-{kind}.exe" if os.name == "nt" else f"cosyvoice-{kind}"
    if os.environ.get(env):
        override = Path(os.environ[env])
        if override.is_file():
            return override.resolve()
        raise RuntimeError(
            f"CosyVoice GGUF native runtime: {env} points to a missing {kind} binary: {override}"
        )
    candidates = []
    configured = os.environ.get(DIR_ENV, "").strip()
    root = Path(configured) if configured else managed_dir()
    # CMake's Visual Studio generator writes to a per-config subfolder
    # (build/bin/Release), so a user who points at the build tree still works.
    for sub in ("bin", "", "Release", "bin/Release"):
        candidates.append(root / sub / name if sub else root / name)
    for candidate in candidates:
        if candidate.is_file():
            return candidate.resolve()
    if configured and not root.is_dir():
        where = f"{DIR_ENV} points to a folder that does not exist: {root}"
    elif configured:
        where = f"{name} is not in {DIR_ENV} ({root}) or its bin/Release subfolders"
    else:
        where = f"{DIR_ENV} is not set and {name} is not in {root}"
    raise RuntimeError(
        f"CosyVoice GGUF native runtime: {kind} binary not found; {where}. "
        f"Build pinned source {SOURCE_REVISION} and point {DIR_ENV} at the folder containing it."
    )


def resolve_server() -> Path:
    return _binary("server")


def resolve_cli() -> Path:
    return _binary("cli")


def _hf_cache_root() -> Path:
    try:
        from services.hf_cache_repair import hf_cache_home

        return Path(hf_cache_home())
    except Exception:  # noqa: BLE001 - diagnostics only
        return Path("?")


def _installed_snapshot() -> Path:
    """The local snapshot of the pinned repo, never touching the network.

    The pinned revision is tried first. Installs made by older builds recorded
    the revision they actually fetched (``voicestudio-revision`` / ``refs/main``),
    so that recorded snapshot is the second candidate. Every miss says which
    cache root and revision were searched and why the lookup failed.
    """
    attempts = []
    try:
        from huggingface_hub import snapshot_download

        return Path(
            snapshot_download(
                HF_MODEL_REPO, revision=HF_MODEL_REVISION, local_files_only=True
            )
        )
    except Exception as exc:  # Hugging Face offline/cache errors vary by version
        attempts.append(f"pinned {HF_MODEL_REVISION[:12]}: {type(exc).__name__}")
    cache = _hf_cache_root()
    try:
        from services.hf_revisions import installed_revision

        recorded = installed_revision(HF_MODEL_REPO, str(cache))
        candidate = cache / ("models--" + HF_MODEL_REPO.replace("/", "--")) / "snapshots" / recorded
        if candidate.is_dir():
            return candidate
        attempts.append(f"recorded {recorded[:12]}: no such snapshot folder")
    except Exception as exc:  # noqa: BLE001 - never mask the original miss
        attempts.append(f"recorded revision: {type(exc).__name__}")
    raise RuntimeError(
        f"CosyVoice GGUF model files were not found in the local Hugging Face cache {cache} "
        f"({'; '.join(attempts)}). Install {HF_MODEL_REPO} from Model Catalogue "
        f"(no network download is attempted here)."
    )


def resolve_model() -> Path:
    direct = os.environ.get(MODEL_ENV, "").strip()
    if direct:
        # An explicit choice never silently falls back to the pinned cache.
        if Path(direct).is_file():
            return Path(direct).resolve()
        raise RuntimeError(f"CosyVoice GGUF model files: {MODEL_ENV} points to a missing model file: {direct}")
    root = _installed_snapshot()
    model = root / MODEL_FILE
    if not model.is_file():
        raise RuntimeError(f"CosyVoice GGUF model files: {MODEL_FILE} is missing from {root}")
    return model


def frontend_dir() -> Path:
    override = os.environ.get(FRONTEND_ENV, "").strip()
    if override:
        # An explicit choice never silently mixes with the pinned cache.
        root = Path(override)
        missing = [item for item in FRONTEND_FILES if not (root / item).is_file()]
        if missing:
            raise RuntimeError(
                f"CosyVoice GGUF model files: {FRONTEND_ENV} is missing frontend files: {', '.join(missing)}"
            )
        return root
    cache = _installed_snapshot()
    missing = [item for item in FRONTEND_FILES if not (cache / item).is_file()]
    if not missing:
        return cache
    raise RuntimeError(
        f"CosyVoice GGUF model files: frontend ONNX files missing from {cache}: "
        f"{', '.join(missing)}. Reinstall the model from Model Catalogue or set {FRONTEND_ENV}."
    )


# One row of ``--list-backends``: name, bus id (``-`` when the device has
# none, a PCI address such as ``0000:01:00.0`` for a GPU), description.
#   Vulkan0    0000:01:00.0         NVIDIA GeForce GTX 1080 Ti
#   CPU        -                    Intel(R) Core(TM) i5-10400F CPU @ 2.90GHz
# The bus column must be ``-`` or a hex bus address, so the header and ggml's own
# ``ggml_vulkan: ...`` log lines never parse as rows. Leading whitespace,
# tabs and CRLF endings are tolerated; stdout and stderr are both read.
_BACKEND_ROW = re.compile(
    r"^\s*([A-Za-z][\w.-]*)\s+(?:-|[0-9A-Fa-f]+(?::[0-9A-Fa-f]+)+(?:\.[0-9A-Fa-f]+)?)\s+(\S.*?)\s*$"
)


def parse_backends(output: str) -> list[NativeBackend]:
    rows = []
    for line in (output or "").replace("\ufeff", "").splitlines():
        match = _BACKEND_ROW.match(line)
        if match:
            name, device = match.groups()
            lowered = name.lower()
            target = (
                "vulkan"
                if lowered.startswith("vulkan")
                else "cpu"
                if lowered == "cpu"
                else lowered
            )
            rows.append(NativeBackend(name, device, target))
    return rows


PROBE_TIMEOUT_S = 120  # first run can compile Vulkan shaders / scan DLLs
# Verdict per binary (path, mtime, size): the device list, or the scrubbed
# failure text. Failures are cached too, so a hung probe costs one timeout.
#: A failed verdict expires so a transient failure (a driver still loading, a
#: busy disk) is retried without restarting the backend; a good one never does.
PROBE_FAILURE_TTL_S = 300
_PROBE_CACHE: dict[tuple, list[NativeBackend] | tuple[str, float]] = {}


def _tail(text: str, lines: int = 4) -> str:
    from core.scrub import scrub_text

    rows = [row.strip() for row in (text or "").splitlines() if row.strip()]
    return scrub_text(" | ".join(rows[-lines:]))[:500]


def probe_backends(cli: Path | None = None, refresh: bool = False) -> list[NativeBackend]:
    """Ask the runtime which devices it exposes.

    A non-zero exit still counts when the device list printed (Vulkan drivers
    often crash on teardown). Every real failure names the runtime, the exit
    code or timeout and the scrubbed last lines of its output. Successful
    and failed verdicts are cached per binary file (a changed binary or
    ``refresh=True`` probes again), so the slow first run happens once.
    """
    cli = Path(cli or resolve_cli())
    try:
        stat = cli.stat()
        key = (str(cli), stat.st_mtime_ns, stat.st_size)
    except OSError:
        key = None
    if key is not None and refresh:
        _PROBE_CACHE.pop(key, None)
    # Read once and drop with pop(): two threads may see the same expired entry.
    cached = _PROBE_CACHE.get(key) if key is not None else None
    if cached is not None:
        if not isinstance(cached, tuple):
            return list(cached)
        if time.monotonic() < cached[1]:
            raise RuntimeError(cached[0])
        _PROBE_CACHE.pop(key, None)
    try:
        devices = _probe_once(cli)
    except RuntimeError as exc:
        if key is not None:
            _PROBE_CACHE[key] = (str(exc), time.monotonic() + PROBE_FAILURE_TTL_S)
        raise
    if key is not None:
        _PROBE_CACHE[key] = list(devices)
    return devices


def _probe_once(cli: Path) -> list[NativeBackend]:
    from core.scrub import scrub_text

    try:
        result = subprocess.run(  # nosec B603 -- fixed argv, resolved runtime binary
            [str(cli), "--list-backends"],
            capture_output=True,
            # Same folder as the server launch, so DLLs beside the exe resolve
            # identically; no console window flashes from the GUI backend.
            cwd=str(cli.parent),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=PROBE_TIMEOUT_S,
            check=False,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(
            f"CosyVoice GGUF native runtime: backend probe of {cli.name} timed out after {PROBE_TIMEOUT_S} s"
        ) from None
    except OSError as exc:
        raise RuntimeError(
            scrub_text(
                f"CosyVoice GGUF native runtime: {cli} could not be started ({type(exc).__name__}: {exc}); "
                "a missing DLL or a wrong-architecture build is the usual cause"
            )
        ) from None
    output = (result.stdout or "") + "\n" + (result.stderr or "")
    devices = parse_backends(output)
    if not devices:
        detail = _tail(output)
        if result.returncode:
            raise RuntimeError(
                f"CosyVoice GGUF native runtime: backend probe of {cli.name} failed "
                f"(exit code {result.returncode}){': ' + detail if detail else ''}"
            )
        raise RuntimeError(
            f"CosyVoice GGUF native runtime: {cli.name} --list-backends printed no recognisable "
            f"device list{': ' + detail if detail else ''}"
        )
    if not any(row.target == "vulkan" for row in devices):
        note = _no_vulkan_detail(result.returncode, result.stderr or "", output)
        devices = [replace(row, probe_detail=note) for row in devices]
    return devices


def _no_vulkan_detail(returncode: int, stderr: str, output: str) -> str:
    """Why the runtime listed no Vulkan device, scrubbed of paths."""
    from core.scrub import scrub_text

    parts = [f"exit code {returncode}"] if returncode else []
    lines = [row.strip() for row in output.splitlines() if row.strip()]
    said = next((row for row in lines if "vulkan" in row.lower()), None)
    if said is None:
        said = next((row.strip() for row in stderr.splitlines() if row.strip()), None)
    if said:
        parts.append(f"runtime said: {said}")
    else:
        parts.append("the runtime printed no Vulkan line (a CPU-only build?)")
    return scrub_text("; ".join(parts))[:300]


def choose_backend(rows: list[NativeBackend], requested: str = "auto") -> NativeBackend:
    requested = requested.strip()
    if requested.lower() == "auto":
        gpu = next((row for row in rows if row.target == "vulkan"), None)
        if gpu:
            return gpu
        cpu = next((row for row in rows if row.name.upper() == "CPU"), None)
        if cpu:
            why = f" ({cpu.probe_detail})" if cpu.probe_detail else ""
            return replace(
                cpu,
                fallback_reason=f"No Vulkan backend was reported by the native runtime{why}; using CPU.",
            )
        return _missing(requested)
    for row in rows:
        if row.name.casefold() == requested.casefold():
            return row
    raise RuntimeError(
        f"CosyVoice GGUF native runtime: requested backend {requested!r} is unavailable; detected: {', '.join(row.name for row in rows)}"
    )


def _missing(requested: str):
    raise RuntimeError(f"No supported native backend for selection {requested!r}")


def selected_backend() -> tuple[NativeBackend, str | None]:
    requested = os.environ.get(BACKEND_ENV, "auto").strip() or "auto"
    if requested.upper() == "CPU":
        # Forcing CPU needs no device probe, so a broken Vulkan stack cannot block it.
        return NativeBackend("CPU", "CPU", "cpu"), None
    try:
        rows = probe_backends()
    except RuntimeError as exc:
        if requested.lower() != "auto":
            raise  # an explicit device choice never silently falls back
        from core.scrub import scrub_text

        reason = scrub_text(
            f"{exc}. Falling back to the runtime's CPU path; "
            f"set {BACKEND_ENV}=CPU to skip the device probe."
        )
        return NativeBackend("CPU", "CPU", "cpu", fallback_reason=reason), reason
    selected = choose_backend(rows, requested)
    return selected, selected.fallback_reason
