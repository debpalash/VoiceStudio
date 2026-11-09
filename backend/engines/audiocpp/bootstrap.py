"""audio.cpp binary probe + model resolution.

audio.cpp (0xShug0/audio.cpp) is a pure-C++ ggml inference engine with
prebuilt release binaries — no Python venv, no ``transformers`` pin, so
none of the dependency-isolation machinery in ``engines._venv_probe`` or
``services.subprocess_backend`` applies. The parent instead:

1. locates a user-installed ``audiocpp_server`` (env var, user dir, or this
   package's ``bin/``), and
2. resolves an explicitly installed GGUF model file from a direct path or
   the shared Hugging Face cache.

Probe order for the server binary (existing installs win, zero migration):

    1. ``${OMNIVOICE_AUDIOCPP_BIN}`` — absolute path to the binary itself.
    2. ``${OMNIVOICE_AUDIOCPP_DIR}/audiocpp_server[.exe]`` — a user-managed
       install dir (e.g. an extracted release zip, or a self-built tree).
    3. ``backend/engines/audiocpp/bin/audiocpp_server[.exe]`` — an explicitly
       installed local copy.

``is_installed()`` is a cheap file-existence check — no spawn, no network.
VoiceStudio never downloads executable code for this engine.
"""
from __future__ import annotations

import errno
import functools
import logging
import os
import platform
import subprocess  # nosec B404 -- fixed argv probes a user-selected executable
import sys
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger("omnivoice.audiocpp.bootstrap")

#: Pinned audio.cpp release. BreezeTTS-2 support landed in 0.7.2; 0.7.4 adds
#: the current native fixes and Sortformer v2.1 streaming runtime.
VERSION = "v0.7.4"

#: GitHub repo serving the prebuilt binaries.
GH_REPO = "0xShug0/audio.cpp"

#: HuggingFace repo serving the GGUF model packages (not gated).
HF_MODEL_REPO = "audio-cpp/audio.cpp-gguf"

# Immutable repository revision used for the Breeze-TTS-2 package.
# Pinning prevents a later upstream file replacement from silently changing
# the model exercised by this backend.
HF_MODEL_REVISION = "dc6fecccc2b0c6bdda0a8b2f38fa61394fee0b9c"

#: Model id used in the generated ``server.json`` and in speech requests.
MODEL_ID = "breeze-tts-2"

#: audio.cpp family name for BreezeTTS 2 (``--family`` / server ``family``).
FAMILY = "breeze_tts"

#: GGUF package directory inside :data:`HF_MODEL_REPO`.
PACKAGE_DIR = "Breeze-TTS-2-GGUF"

#: Default package (Q8_0, the upstream-recommended GGUF). ``bf16`` is
#: available via ``OMNIVOICE_AUDIOCPP_PACKAGE``.
DEFAULT_PACKAGE = "breeze-tts-2-q8_0.gguf"

#: Env var pointing directly at the ``audiocpp_server`` binary.
BIN_ENV = "OMNIVOICE_AUDIOCPP_BIN"

#: Env var pointing at a directory containing ``audiocpp_server``.
DIR_ENV = "OMNIVOICE_AUDIOCPP_DIR"

#: Env var overriding the GGUF package filename (e.g. the bf16 package).
PACKAGE_ENV = "OMNIVOICE_AUDIOCPP_PACKAGE"

#: Optional advanced overrides for a binary that exposes several runtimes or
#: devices. Device indices are local to the selected backend registry.
BACKEND_ENV = "OMNIVOICE_AUDIOCPP_BACKEND"
DEVICE_ENV = "OMNIVOICE_AUDIOCPP_DEVICE"

#: Env var overriding the loopback port the managed server binds.
PORT_ENV = "OMNIVOICE_AUDIOCPP_PORT"

#: Default loopback port. High and engine-specific to avoid clashing with
#: the app itself or a user-run ``audiocpp_server`` (default 8080).
DEFAULT_PORT = 17860

@dataclass(frozen=True)
class ModelPackage:
    """One audio.cpp model family served from :data:`HF_MODEL_REPO`.

    Every family shares the managed runtime and the pinned repository
    revision; each has its own package, loopback port and server.json
    directory so two loaded engines never collide.
    """

    family: str
    model_id: str
    package_dir: str
    default_package: str
    label: str
    default_port: int
    package_env: str
    port_env: str
    model_env: str
    workdir: str


#: Breeze-TTS-2 — the original package. Its constants stay as above so
#: existing installs, env overrides and settings keep working unchanged.
BREEZE = ModelPackage(
    family=FAMILY,
    model_id=MODEL_ID,
    package_dir=PACKAGE_DIR,
    default_package=DEFAULT_PACKAGE,
    label="Breeze-TTS-2",
    default_port=DEFAULT_PORT,
    package_env=PACKAGE_ENV,
    port_env=PORT_ENV,
    model_env="OMNIVOICE_AUDIOCPP_MODEL",
    workdir="audiocpp",
)

#: dots.tts SOAR (Apache-2.0, multilingual voice cloning). The pinned v0.7.4
#: runtime ships the ``dots_tts`` family, and the pinned repository revision
#: holds the matching Q8_0 package (#2665).
DOTS = ModelPackage(
    family="dots_tts",
    model_id="dots-tts-soar",
    package_dir="DotTTS-SOAR-GGUF",
    default_package="dots-tts-soar-q8_0.gguf",
    label="dots.tts",
    default_port=DEFAULT_PORT + 1,
    package_env="OMNIVOICE_AUDIOCPP_DOTS_PACKAGE",
    port_env="OMNIVOICE_AUDIOCPP_DOTS_PORT",
    model_env="OMNIVOICE_AUDIOCPP_DOTS_MODEL",
    workdir="audiocpp-dots",
)


#: This package's owned binary dir (probe 3).
_PKG_BIN_DIR: Path = Path(__file__).parent / "bin"

# Recommended (asset filename, sha256) per platform slug, from the v0.7.4
# release. Windows and Linux use the vendor-neutral Vulkan build, which also
# exposes the native CPU backend. Upstream publishes the macOS builds under
# the Metal package name. No linux-aarch64 prebuilt exists in v0.7.4.
_ASSETS: dict[str, tuple[str, str]] = {
    "windows-x64": (
        "audio-v0.7.4-bin-windows-x64-vulkan.zip",
        "057332f9e3fb37706a8ecb5075ac1797efcd85fdccd739f7b65761a5920f2828",
    ),
    "linux-x64": (
        "audio-v0.7.4-bin-ubuntu-x64-vulkan.tar.gz",
        "e0ef3123a9f94e130ad463db0db5a69b65485ef8db1b46edead00c03a86fa787",
    ),
    "darwin-arm64": (
        "audio-v0.7.4-bin-macos-arm64-metal.tar.gz",
        "639926715b1cb537f82aa31656aabbae5d9a85ac36568c402026968f3072e2b3",
    ),
    "darwin-x64": (
        "audio-v0.7.4-bin-macos-x64-metal.tar.gz",
        "bdb797d54dcf8416bd5ac0fac282ce5500dd08843f8f22e20e9fc378ebc24c1f",
    ),
}
_ASSET_SIZES = {
    "windows-x64": 56_818_905,
    "linux-x64": 71_551_673,
    "darwin-arm64": 25_270_657,
    "darwin-x64": 26_718_959,
}

#: Binary filename per platform.
_BINARY_NAMES = {"windows-x64": "audiocpp_server.exe"}

_REGISTRY_BACKENDS = {
    "CPU": "cpu",
    "CUDA": "cuda",
    "MUSA": "cuda",
    "HIP": "hip",
    "ROCm": "hip",
    "Vulkan": "vulkan",
    "Metal": "metal",
    "MTL": "metal",
}
_BACKEND_ALIASES = {
    "cpu": "cpu",
    "cuda": "cuda",
    "hip": "hip",
    "rocm": "hip",
    "vulkan": "vulkan",
    "metal": "metal",
}


@dataclass(frozen=True)
class AudioCPPDevice:
    """One immutable device from audio.cpp's backend-local registry."""

    registry: str
    backend: str
    index: int
    name: str
    kind: str
    target: str
    hardware_family: str


@dataclass(frozen=True)
class AudioCPPSelection:
    """The runtime/device chosen for the next managed server."""

    device: AudioCPPDevice
    fallback_reason: str | None = None
    verified_vram_gb: float = 0.0


@dataclass(frozen=True)
class _ProbeOutcome:
    devices: tuple[AudioCPPDevice, ...] = ()
    error: str | None = None


def _cpu_probe_fallback(error: RuntimeError) -> AudioCPPSelection:
    """A usable automatic fallback when native device discovery fails."""
    return AudioCPPSelection(
        AudioCPPDevice(
            registry="CPU",
            backend="cpu",
            index=0,
            name="Host CPU",
            kind="CPU",
            target="cpu",
            hardware_family="cpu",
        ),
        f"{error}; running on CPU",
    )


def _vulkan_hardware_family(name: str) -> str:
    low = name.casefold()
    if any(token in low for token in ("nvidia", "geforce", "quadro", "tesla")):
        return "cuda"
    if any(token in low for token in ("amd", "radeon")):
        return "rocm"
    if any(token in low for token in ("intel", "arc ")):
        return "xpu"
    return "vulkan"


def _device_families(registry: str, name: str, kind: str) -> tuple[str, str]:
    # Software adapters such as Vulkan llvmpipe may be listed by a GPU
    # registry but still execute on the CPU. Keep their runtime backend for
    # explicit overrides while reporting and routing them as CPU work.
    if kind == "CPU":
        return "cpu", "cpu"
    if registry in {"CUDA", "MUSA"}:
        return "cuda", "cuda"
    if registry in {"HIP", "ROCm"}:
        return "rocm", "rocm"
    if registry in {"Metal", "MTL"}:
        return "mps", "mps"
    if registry == "Vulkan":
        return "vulkan", _vulkan_hardware_family(name)
    return "cpu", "cpu"


def parse_device_list(output: str) -> tuple[AudioCPPDevice, ...]:
    """Parse the stable stdout contract of ``--list-devices``.

    Backend diagnostics are emitted on stderr and deliberately never enter
    this parser. Unknown future registries are ignored; malformed entries for
    a registry we understand fail closed instead of selecting the wrong GPU.
    """
    devices: list[AudioCPPDevice] = []
    seen: set[tuple[str, int]] = set()
    for raw in str(output or "").splitlines():
        line = raw.strip()
        registry, colon, detail = line.partition(":")
        if not colon or registry not in _REGISTRY_BACKENDS:
            continue
        index_text, space, remainder = detail.strip().partition(" ")
        if not space or not index_text.isascii() or not index_text.isdecimal():
            raise RuntimeError(
                f"malformed audio.cpp {registry} device entry"
            )
        index = int(index_text)
        remainder = remainder.strip()
        kind_start = remainder.rfind("[")
        if kind_start < 0 or not remainder.endswith("]"):
            raise RuntimeError(
                f"malformed audio.cpp {registry} device entry"
            )
        name_field = remainder[:kind_start].strip()
        if name_field:
            if len(name_field) < 2 or name_field[0] != '"' or name_field[-1] != '"':
                raise RuntimeError(
                    f"malformed audio.cpp {registry} device entry"
                )
            name = name_field[1:-1]
        else:
            name = ""
        kind = remainder[kind_start + 1:-1].strip().upper()
        if kind not in {"CPU", "GPU", "IGPU", "ACCEL", "META"}:
            raise RuntimeError("unknown audio.cpp device kind")
        # Registry aliases such as HIP/ROCm share one backend-local index
        # namespace and therefore cannot safely describe different devices.
        key = (_REGISTRY_BACKENDS[registry], index)
        if key in seen:
            raise RuntimeError(
                f"duplicate audio.cpp device entry: {registry}:{index}"
            )
        seen.add(key)
        target, hardware_family = _device_families(registry, name, kind)
        devices.append(AudioCPPDevice(
            registry=registry,
            backend=_REGISTRY_BACKENDS[registry],
            index=index,
            name=name,
            kind=kind,
            target=target,
            hardware_family=hardware_family,
        ))
    if not devices:
        raise RuntimeError("audio.cpp reported no recognized compute devices")
    return tuple(devices)


def _platform_slug() -> str:
    system = sys.platform
    machine = platform.machine().lower()
    if system == "win32":
        return "windows-x64"
    if system == "darwin":
        return "darwin-arm64" if machine in ("arm64", "aarch64") else "darwin-x64"
    if machine in ("x86_64", "amd64"):
        return "linux-x64"
    return f"linux-{machine}"


def binary_name(slug: str | None = None) -> str:
    """``audiocpp_server`` filename for ``slug`` (``.exe`` on Windows)."""
    return _BINARY_NAMES.get(slug or _platform_slug(), "audiocpp_server")


def _probe_paths() -> list[Path]:
    out: list[Path] = []
    direct = os.environ.get(BIN_ENV, "").strip()
    if direct:
        out.append(Path(direct))
    user_dir = os.environ.get(DIR_ENV, "").strip()
    if user_dir:
        out.append(Path(user_dir) / binary_name())
    out.append(managed_runtime_dir() / binary_name())
    out.append(_PKG_BIN_DIR / binary_name())
    return out


def platform_slug() -> str:
    """Stable release-platform key used by the managed runtime installer."""
    return _platform_slug()


def managed_runtime_dir() -> Path:
    """Update-surviving location for the checksummed app-managed runtime."""
    from core.config import get_engines_dir

    return Path(get_engines_dir()) / "audio-cpp" / VERSION.lstrip("v") / _platform_slug()


def is_installed() -> bool:
    """Cheap precedence-aware check for a usable server binary."""
    try:
        resolve_server_binary()
    except RuntimeError:
        return False
    return True


def resolve_server_binary() -> Path:
    """Resolve the ``audiocpp_server`` binary. Raises ``RuntimeError`` with
    install instructions when none is found."""
    for cand in _probe_paths():
        if cand.is_file():
            if os.name == "nt" or os.access(cand, os.X_OK):
                return cand
            raise RuntimeError(
                "audiocpp_server is not executable. Run `chmod +x "
                "audiocpp_server` on the configured binary, then restart "
                "VoiceStudio. See docs/engines/audio-cpp.md."
            )
    slug = _platform_slug()
    asset = _ASSETS.get(slug)
    if asset is None:
        raise RuntimeError(
            f"audio.cpp ships no prebuilt binary for this platform ({slug}). "
            "Build from https://github.com/0xShug0/audio.cpp and set "
            f"{BIN_ENV} to your audiocpp_server binary. See "
            "docs/engines/audio-cpp.md."
        )
    raise RuntimeError(
        "audiocpp_server not found. Download "
        f"https://github.com/{GH_REPO}/releases/download/{VERSION}/{asset[0]} "
        f"(SHA-256 {asset[1]}), verify and extract it, and set {BIN_ENV} to the "
        "audiocpp_server binary (or "
        f"{DIR_ENV} to its directory). See docs/engines/audio-cpp.md."
    )


@functools.lru_cache(maxsize=4)
def _probe_device_outcome(binary: str) -> _ProbeOutcome:
    try:
        proc = subprocess.run(  # nosec B603 -- executable is the resolved engine binary
            [binary, "--list-devices"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _ProbeOutcome(
            error="audiocpp_server device discovery timed out after 10 seconds"
        )
    except OSError as exc:
        return _ProbeOutcome(
            error=(
                "audiocpp_server device discovery could not start: "
                f"{type(exc).__name__}"
            )
        )
    if proc.returncode != 0:
        return _ProbeOutcome(
            error=(
                "audiocpp_server device discovery failed "
                f"(code {proc.returncode}). Check the audio.cpp server log "
                "for details."
            )
        )
    try:
        return _ProbeOutcome(devices=parse_device_list(proc.stdout))
    except RuntimeError as exc:
        return _ProbeOutcome(error=str(exc))


def _probe_devices(binary: str) -> tuple[AudioCPPDevice, ...]:
    outcome = _probe_device_outcome(binary)
    if outcome.error:
        raise RuntimeError(outcome.error)
    return outcome.devices


def probe_devices() -> tuple[AudioCPPDevice, ...]:
    """Return the installed binary's devices without loading a model."""
    return _probe_devices(str(resolve_server_binary()))


def _priority(device: AudioCPPDevice) -> tuple[int, int]:
    if device.kind == "META":
        # Tensor-parallel meta devices are valid explicit targets, but their
        # resource footprint is not safe to choose implicitly over CPU.
        rank = 8
    elif device.backend != "cpu" and device.kind == "CPU":
        # Native CPU is the predictable fallback. Software adapters remain
        # available to an explicit backend override but never win auto mode.
        rank = 7
    elif device.backend == "cuda":
        rank = 0
    elif device.backend == "hip":
        rank = 1
    elif device.backend == "metal":
        rank = 2
    elif device.backend == "vulkan" and device.kind == "GPU":
        rank = 3
    elif device.backend == "vulkan" and device.kind in {"IGPU", "ACCEL"}:
        rank = 4
    elif device.backend == "cpu":
        rank = 6
    else:
        rank = 5
    return rank, device.index


def select_device(
    devices: tuple[AudioCPPDevice, ...],
    *,
    requested_family: str = "auto",
    backend_override: str | None = None,
    device_override: int | None = None,
    preferred_name: str = "",
) -> AudioCPPSelection:
    """Resolve one device with explicit overrides and discrete-GPU priority."""
    if backend_override:
        normalized = _BACKEND_ALIASES.get(backend_override.strip().lower())
        if normalized is None:
            valid = ", ".join(_BACKEND_ALIASES)
            raise RuntimeError(
                f"unknown audio.cpp backend '{backend_override}' (valid: {valid})"
            )
        candidates = [device for device in devices if device.backend == normalized]
        if device_override is not None:
            candidates = [
                device for device in candidates if device.index == device_override
            ]
        if not candidates:
            suffix = "" if device_override is None else f" device {device_override}"
            available = ", ".join(
                f"{device.backend}:{device.index}" for device in devices
            )
            raise RuntimeError(
                f"audio.cpp backend '{backend_override}'{suffix} is unavailable "
                f"(available: {available})"
            )
        # An explicit runtime request should still prefer a compute device to
        # a software adapter when no backend-local index was supplied. META is
        # valid here because the user explicitly chose this registry.
        return AudioCPPSelection(min(
            candidates,
            key=lambda device: (device.kind == "CPU", _priority(device)),
        ))

    if device_override is not None:
        raise RuntimeError(
            f"{DEVICE_ENV} requires {BACKEND_ENV} because device indices are "
            "backend-local"
        )

    family = (requested_family or "auto").strip().lower()
    if family != "auto":
        candidates = [
            device for device in devices if device.hardware_family == family
        ]
        if candidates:
            preferred = preferred_name.casefold().strip()
            if preferred:
                named = [
                    device for device in candidates
                    if device.name
                    and (
                        preferred in device.name.casefold()
                        or device.name.casefold() in preferred
                    )
                ]
                if named:
                    candidates = named
            return AudioCPPSelection(min(candidates, key=_priority))
        cpu = [device for device in devices if device.backend == "cpu"]
        if cpu:
            return AudioCPPSelection(
                min(cpu, key=_priority),
                f"requested {family.upper()} device is not exposed by the "
                "installed audio.cpp binary; running on CPU",
            )
        raise RuntimeError(
            f"requested {family.upper()} device is not exposed by the "
            "installed audio.cpp binary"
        )

    return AudioCPPSelection(min(devices, key=_priority))


def resolve_compute_selection(caps=None) -> AudioCPPSelection:
    """Select the runtime from engine env overrides, Settings, then auto."""
    backend_override = os.environ.get(BACKEND_ENV, "").strip() or None
    raw_device = os.environ.get(DEVICE_ENV, "").strip()
    device_override: int | None = None
    if raw_device:
        try:
            device_override = int(raw_device)
        except ValueError as exc:
            raise RuntimeError(
                f"{DEVICE_ENV} must be a non-negative integer"
            ) from exc
        if device_override < 0:
            raise RuntimeError(f"{DEVICE_ENV} must be a non-negative integer")

    if caps is None:
        from core.device_caps import detect_host_caps

        caps = detect_host_caps()
    requested = getattr(caps, "requested_family", "auto") or "auto"
    try:
        devices = probe_devices()
    except RuntimeError as exc:
        if backend_override or raw_device or requested != "auto":
            raise
        return _cpu_probe_fallback(exc)
    selection = select_device(
        devices,
        requested_family=requested,
        backend_override=backend_override,
        device_override=device_override,
        preferred_name=getattr(caps, "device_name", "") or "",
    )
    # HostCaps measures the preferred accelerator's device 0. Reuse that VRAM
    # only when the selected native registry has exactly one device with the
    # same normalized name. Multi-GPU peers with identical names stay unknown.
    selected_name = " ".join(selection.device.name.casefold().split())
    host_name = " ".join(
        str(getattr(caps, "device_name", "") or "").casefold().split()
    )
    peers = [
        device for device in devices
        if device.backend == selection.device.backend
        and " ".join(device.name.casefold().split()) == host_name
    ]
    if (
        selected_name
        and selected_name == host_name
        and len(peers) == 1
        and float(getattr(caps, "vram_gb", 0.0) or 0.0) > 0
    ):
        return AudioCPPSelection(
            selection.device,
            selection.fallback_reason,
            float(caps.vram_gb),
        )
    return selection


def runtime_targets(devices: tuple[AudioCPPDevice, ...] | None = None) -> tuple[str, ...]:
    """Actual compute backends compiled into the selected binary."""
    if devices is not None:
        found = devices
    else:
        try:
            found = probe_devices()
        except RuntimeError:
            if (
                os.environ.get(BACKEND_ENV, "").strip()
                or os.environ.get(DEVICE_ENV, "").strip()
            ):
                raise
            return ("cpu",)
    ordered: list[str] = []
    for device in sorted(found, key=_priority):
        if device.target not in ordered:
            ordered.append(device.target)
    return tuple(ordered)


def invalidate() -> None:
    """Forget cached binary capability discovery after an install change."""
    _probe_device_outcome.cache_clear()


def default_asset() -> tuple[str, str] | None:
    """``(filename, sha256)`` of the release asset for this host, or None
    when upstream ships no prebuilt for it."""
    return _ASSETS.get(_platform_slug())


def default_asset_size() -> int | None:
    """Published byte size of this host's pinned release archive."""
    return _ASSET_SIZES.get(_platform_slug())


def server_port(package: ModelPackage = BREEZE) -> int:
    """Loopback port for the managed server (env override or default)."""
    raw = os.environ.get(package.port_env, "").strip()
    if raw:
        try:
            port = int(raw)
            if 1 <= port <= 65535:
                return port
            logger.warning("Ignoring %s=%r: out of range.", package.port_env, raw)
        except ValueError:
            logger.warning("Ignoring %s=%r: not a number.", package.port_env, raw)
    return package.default_port


def package_filename(package: ModelPackage = BREEZE) -> str:
    """GGUF package filename (env override or the package default)."""
    return os.environ.get(package.package_env, "").strip() or package.default_package


def _materialize_gguf_cache_path(model_file: Path) -> Path:
    """Return a real ``.gguf`` path when the HF snapshot is a symlink.

    audio.cpp canonicalizes model paths before inspecting the suffix. The
    Hugging Face cache points the friendly ``.gguf`` snapshot name at an
    extensionless content-addressed blob, so passing that symlink makes the
    server reject a valid model. A hard link beside the snapshot keeps the
    required suffix without copying a multi-gigabyte model or escaping the
    snapshot's cleanup lifecycle.
    """
    resolved = model_file.resolve()
    if resolved.suffix.lower() == ".gguf":
        return model_file
    if model_file.suffix.lower() != ".gguf":
        raise RuntimeError(f"audio.cpp model must be a .gguf file: {model_file}")

    def _link(alias: Path) -> Path:
        for attempt in range(2):
            try:
                os.link(resolved, alias)
            except FileExistsError:
                if (
                    not alias.is_symlink()
                    and alias.is_file()
                    and os.path.samefile(resolved, alias)
                ):
                    return alias
                if attempt == 0 and alias.is_symlink():
                    alias.unlink()
                    continue
                raise RuntimeError(
                    f"audio.cpp model alias points at a different file: {alias}"
                ) from None
            return alias
        raise RuntimeError(f"audio.cpp model alias could not be created: {alias}")

    alias = model_file.with_name(
        f".{model_file.stem}-{HF_MODEL_REVISION[:12]}.audiocpp.gguf"
    )
    try:
        return _link(alias)
    except OSError as exc:
        if exc.errno == errno.EXDEV:
            # An explicit symlink may live on a different filesystem from its
            # target. Put the suffix-preserving hard link beside the resolved
            # file so no multi-gigabyte copy is needed.
            target_alias = resolved.with_name(
                f".{resolved.name}-{HF_MODEL_REVISION[:12]}.audiocpp.gguf"
            )
            try:
                return _link(target_alias)
            except OSError as target_exc:
                exc = target_exc
        raise RuntimeError(
            "audio.cpp cannot materialize the Hugging Face cache symlink as "
            f"a .gguf hard link: {exc}"
        ) from exc


def resolve_model_file(package: ModelPackage = BREEZE) -> Path:
    """Resolve an explicitly installed GGUF file for one model package.

    An explicit path in the package's model env var (``OMNIVOICE_AUDIOCPP_MODEL``
    for Breeze-TTS-2) wins (file or directory containing the package file).
    Otherwise only the local Hugging Face cache is inspected. Downloads must be
    started explicitly from Model Catalogue → Models, so generation can never
    silently transfer a multi-gigabyte package.
    """
    filename = package_filename(package)
    override = os.environ.get(package.model_env, "").strip()
    if override:
        cand = Path(override)
        if cand.is_file():
            return _materialize_gguf_cache_path(cand)
        if cand.is_dir():
            inner = cand / filename
            if inner.is_file():
                return _materialize_gguf_cache_path(inner)
        raise RuntimeError(
            f"{package.model_env}={override} is not a GGUF file or a "
            "directory containing one."
        )

    from huggingface_hub import snapshot_download
    from huggingface_hub.utils import LocalEntryNotFoundError

    try:
        cached = Path(
            snapshot_download(
                repo_id=HF_MODEL_REPO,
                # Full immutable commit SHA declared above; Bandit cannot follow
                # the module constant through this call.
                revision=HF_MODEL_REVISION,  # nosec B615
                allow_patterns=[f"{package.package_dir}/{filename}"],
                local_files_only=True,
            )
        )
    except (LocalEntryNotFoundError, OSError) as exc:
        raise RuntimeError(
            f"{package.label} is not installed. Install the audio.cpp "
            f"{package.label} model from the engine's Weights list in Model "
            f"Catalogue, or set {package.model_env} to an existing GGUF file."
        ) from exc
    model_file = cached / package.package_dir / filename
    if not model_file.is_file():
        raise RuntimeError(
            f"{package.label} package {filename} is not completely "
            "installed. Reinstall it from the engine's Weights list in Model Catalogue."
        )
    return _materialize_gguf_cache_path(model_file)


__all__ = [
    "BREEZE",
    "DOTS",
    "ModelPackage",
    "AudioCPPDevice",
    "AudioCPPSelection",
    "BACKEND_ENV",
    "BIN_ENV",
    "DEFAULT_PACKAGE",
    "DEFAULT_PORT",
    "DEVICE_ENV",
    "DIR_ENV",
    "FAMILY",
    "HF_MODEL_REPO",
    "HF_MODEL_REVISION",
    "MODEL_ID",
    "PACKAGE_DIR",
    "PACKAGE_ENV",
    "PORT_ENV",
    "VERSION",
    "_materialize_gguf_cache_path",
    "binary_name",
    "default_asset",
    "default_asset_size",
    "invalidate",
    "is_installed",
    "managed_runtime_dir",
    "package_filename",
    "platform_slug",
    "parse_device_list",
    "probe_devices",
    "resolve_compute_selection",
    "resolve_model_file",
    "resolve_server_binary",
    "runtime_targets",
    "server_port",
]
