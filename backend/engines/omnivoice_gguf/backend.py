"""OmniVoice GGUF backend — subprocess host for the `omnivoice-tts` C++ runtime.

Architecture (Phase 4 Plan 04-01):

    Parent (this class) ──spawn──► bin/omnivoice-tts-<platform>
                          ◄─wav─── soundfile.read(<out_path>)

The C++ binary is the isolation boundary; the per-engine venv that
``SubprocessBackend`` would normally create is unnecessary because
``omnivoice-tts`` has no Python deps of its own (it statically links
GGML/GGML-cpu and dlopens GGML-cuda / GGML-vulkan when present).

This module intentionally does NOT subclass :class:`SubprocessBackend`
because that primitive owns a long-lived sidecar with a JSON-over-stdin
protocol that ``omnivoice-tts`` does not speak. Instead, each
``generate()`` call ``subprocess.Popen``s the binary fresh, passes the
text on stdin per the README invocation pattern, and reads the output
WAV from a temp path. The classes share the same "isolation through OS
process" philosophy but the wire protocols are different — the
SubprocessBackend's length-prefixed JSON is a stronger contract than
the `omnivoice-tts` CLI offers.

Security (see plan front-matter threat model):
    T-04-01 — Tampering of bundled binary: SHA-256 manifest verified by
              :meth:`is_available` on every call. Sums live in
              ``bin/checksums.sha256``.
    T-04-02 — Subprocess arg injection: argv is composed from typed
              ``pathlib.Path`` objects rooted in ``$HF_HUB_CACHE`` or a
              ``tempfile.mkstemp()`` output path. ``shell=False`` always.
    T-04-03 — GGUF quant served from network: an installed pair is read
              from the local HuggingFace cache first; only a missing
              pair is fetched, on first generate, through
              ``huggingface_hub`` (SHA-verified at download time) at the
              revision pinned in ``quant_map.json._meta.source_commit_sha``.
    T-04-04 — HF_TOKEN leak into stderr: subprocess stderr is decoded
              through ``services.tts_backend._mask_hf_tokens`` before
              landing in any logger record.
    T-04-05 — Quant override UI loads attacker path: validated by
              :func:`services.settings_store.set_quant_override` against
              the ``quant_map.json`` allow-list before reaching this
              module.
    T-04-06 — DoS via hung subprocess: every spawn enforces a hard
              timeout (5 s for ``probe_load``, 120 s for ``generate``).

Public surface:
    :class:`OmniVoiceGGUFBackend` — the registered TTSBackend class.
    :func:`select_default_engine` — module-level resolver used at app
        start to decide between ``"omnivoice-gguf"`` (when probe + load
        succeed) and ``"omnivoice"`` (the existing in-process fallback).
"""
from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import platform
import re
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from .compute import BACKEND_ENV as BACKEND_ENV, DEVICE_ENV as DEVICE_ENV
from .quant_cache import resolve_installed_pair

if TYPE_CHECKING:
    from core.device_caps import HostCaps

logger = logging.getLogger("omnivoice.gguf")

#: Path to the ``backend/engines/omnivoice_gguf/`` directory. Used as the
#: anchor for ``quant_map.json`` and the ``bin/`` directory which sits a
#: few levels up at the repo root.
_PKG_DIR = Path(__file__).resolve().parent

#: Repo root — used to find ``bin/omnivoice-tts-*`` and
#: ``bin/checksums.sha256``. backend/engines/omnivoice_gguf/ → root.
_REPO_ROOT = _PKG_DIR.parent.parent.parent

#: HuggingFace repo ID for the quants. Pinned by SHA via ``quant_map.json``.
_HF_REPO_ID = "Serveurperso/OmniVoice-GGUF"

#: HF token redaction regex — defense in depth on top of AUTH-05's logger
#: filter, because subprocess stderr is captured into a *str* before it
#: reaches the logger.
_HF_TOKEN_RE = re.compile(r"hf_[A-Za-z0-9]{30,}")
_HF_TOKEN_MASK = "hf_***REDACTED***"

#: Decoded budget for one reference clip. The profile upload path caps a
#: reference at 128 MB on disk (``api.routers.profiles``), but a lossy
#: container decodes to far more than that, and converting a reference
#: decodes it in the backend process. A clip that does not fit is refused
#: with an actionable message instead of exhausting the backend.
_MAX_REFERENCE_DECODED_BYTES = 128 * 1024 * 1024


def _mask_token(s: str) -> str:
    """Belt-and-suspenders token redaction. Used on captured stderr
    before it lands in any logger record."""
    return _HF_TOKEN_RE.sub(_HF_TOKEN_MASK, s)


def _platform_slug() -> str:
    """Return the binary slug for the current host.

    Matches the filenames committed under ``bin/`` and the matrix
    keys in the CI build job:

        darwin-arm64
        darwin-x86_64
        windows-x86_64
        linux-x86_64
        linux-aarch64
    """
    system = platform.system().lower()
    machine = platform.machine().lower()
    if system == "darwin":
        if machine in ("arm64", "aarch64"):
            return "darwin-arm64"
        return "darwin-x86_64"
    if system == "windows":
        return "windows-x86_64"
    if system == "linux" and machine in ("arm64", "aarch64"):
        return "linux-aarch64"
    # Linux + everything else falls into the linux slug.
    return "linux-x86_64"


def _binary_path(slug: Optional[str] = None) -> Path:
    """Resolve the on-disk path for the platform binary."""
    s = slug or _platform_slug()
    name = "omnivoice-tts-" + s
    if s.startswith("windows"):
        name += ".exe"
    return _REPO_ROOT / "bin" / name


def _generate_timeout_s() -> float:
    """Hard per-spawn timeout for the C++ binary, read from the env at call time.

    ``OMNIVOICE_GGUF_GENERATE_TIMEOUT_S`` overrides; the default sits ABOVE
    the GPU-pool generate budget (``OMNIVOICE_GENERATE_TIMEOUT_S``, 300s
    floor) on purpose — the pool guard is the real, well-diagnosed deadline,
    and this one only reaps a truly wedged C++ process. The old hardcoded
    120s killed legitimate CPU-only generates mid-synthesis (#1348).
    """
    raw = os.environ.get("OMNIVOICE_GGUF_GENERATE_TIMEOUT_S")
    if raw:
        try:
            val = float(raw)
        except ValueError:
            val = None
        # inf would disarm the wedge guard entirely; nan poisons max().
        if val is not None and math.isfinite(val):
            return max(1.0, val)
        logger.warning(
            "Ignoring invalid OMNIVOICE_GGUF_GENERATE_TIMEOUT_S=%r", raw
        )
    return 600.0


def _spawn_env() -> dict[str, str]:
    """Environment for spawning the binary, with ``bin/`` on the loader path.

    A source-built binary can be dynamically linked against the ``libggml*``
    shared libraries the build script now drops next to it; without the
    loader path the spawn dies with exit 127 — ``libggml.so.0: cannot open
    shared object file`` (#1348). Windows resolves DLLs from the exe's own
    directory already; Linux and macOS need it stated. Both variables are
    set unconditionally — each OS ignores the other's.
    """
    env = os.environ.copy()
    bin_dir = str(_binary_path().parent)
    for var in ("LD_LIBRARY_PATH", "DYLD_FALLBACK_LIBRARY_PATH"):
        prev = env.get(var)
        env[var] = bin_dir if not prev else bin_dir + os.pathsep + prev
    from .compute import apply_compute_env
    return apply_compute_env(env)


#: Shared ggml runtime libraries the build drops next to the binary. With
#: ``GGML_BACKEND_DL`` the runtime picks its CPU/Vulkan backend from these
#: at load time, so they are as much "the runtime" as the executable.
_GGML_LIB_RE = re.compile(r"^(?:lib)?ggml[\w.-]*\.(?:so(?:\.\d+)*|dylib|dll)$", re.IGNORECASE)


def _mismatched_runtime_library(bin_dir: Path, manifest: dict[str, str]) -> str | None:
    """Return the first listed ggml library whose bytes differ from the manifest.

    Every listed library that is present must match. A listed library that
    is absent belongs to another platform's build and is skipped; a present
    library the manifest does not list is logged — manifests written before
    #3042 listed only the executable, and refusing those installs would break
    working runtimes.
    """
    for name, digest in sorted(manifest.items()):
        if not _GGML_LIB_RE.match(name):
            continue
        path = bin_dir / name
        if path.is_file() and _sha256_of_file(path).lower() != digest.lower():
            return name
    unlisted = sorted(
        p.name for p in bin_dir.iterdir()
        if p.is_file() and _GGML_LIB_RE.match(p.name) and p.name not in manifest
    ) if manifest else []
    if unlisted:
        logger.warning(
            "GGUF runtime libraries not covered by bin/checksums.sha256: %s",
            ", ".join(unlisted),
        )
    return None


#: Exit statuses meaning "this CPU cannot execute an instruction in the
#: runtime": a negative signal number from subprocess on POSIX (SIGILL), the
#: shell's 128+SIGILL, and Windows STATUS_ILLEGAL_INSTRUCTION in both its
#: unsigned and signed spellings. A runtime compiled for newer CPU features
#: than the host has (#2780) fails this way on every generate.
_ILLEGAL_INSTRUCTION_EXITS = frozenset({-4, 132, 0xC000001D, 0xC000001D - (1 << 32)})

#: Binaries that hit an illegal instruction, keyed by path, valued by the
#: file identity they had then. Until the file changes (rebuilt/reinstalled),
#: the engine reports itself unavailable instead of failing every generate.
_CPU_INCOMPATIBLE: dict[str, tuple[int, int]] = {}


def _file_identity(path: Path) -> tuple[int, int] | None:
    try:
        st = path.stat()
    except OSError:
        return None
    return (st.st_size, st.st_mtime_ns)


def _cpu_incompatible_reason(bin_path: Path) -> str:
    return (
        f"GGUF binary {bin_path.name} stopped with an illegal instruction: it "
        f"was built for CPU features this processor does not have. Rebuild it "
        f"with `scripts/build-omnivoice-tts.sh --platform {_platform_slug()}` "
        f"(portable x86-64 build), reinstall VoiceStudio, or use the default "
        f"in-process OmniVoice engine (Model Catalogue)."
    )


def _raise_if_illegal_instruction(bin_path: Path, returncode) -> None:
    """Record and raise a typed error when the runtime cannot run on this CPU."""
    if returncode not in _ILLEGAL_INSTRUCTION_EXITS:
        return
    identity = _file_identity(bin_path)
    if identity is not None:
        _CPU_INCOMPATIBLE[str(bin_path)] = identity
    from services.binary_preflight import InvalidBinaryError

    raise InvalidBinaryError(
        bin_path,
        "it stopped with an illegal instruction (built for CPU features this "
        "processor does not have)",
        _binary_repair_hint(),
    )


def _binary_repair_hint() -> str:
    """One actionable sentence for a broken/placeholder GGUF binary (#1172).

    Appended to every InvalidBinaryError this engine raises so the user
    always learns what to do, whether the failure surfaced from preflight
    or from the OS at exec time.
    """
    return (
        f"the bundled GGUF runtime is not usable on this machine — build it "
        f"with `scripts/build-omnivoice-tts.sh --platform {_platform_slug()}`, "
        f"reinstall VoiceStudio, or switch to the default in-process "
        f"OmniVoice engine (Model Catalogue)"
    )


def _load_quant_map() -> dict:
    """Read and validate ``quant_map.json``.

    Raises ``RuntimeError`` if the file is missing or malformed (the
    SPIKE-01 ADR requires real 40-char hex SHAs in ``_meta``; we enforce
    that here so a corrupted JSON can't sneak past the registry).
    """
    p = _PKG_DIR / "quant_map.json"
    # JSON is UTF-8 by definition. A bare open() decodes in the locale code
    # page instead, and the table's VRAM notes carry em dashes that
    # cp932/cp936/cp949/cp950 cannot decode — so on a Chinese, Japanese or
    # Korean Windows picking a quant raised UnicodeDecodeError and every
    # generation failed with an error the app could not classify.
    with p.open(encoding="utf-8") as f:
        data = json.load(f)
    meta = data.get("_meta") or {}
    if meta.get("schema_version") != 1:
        raise RuntimeError(
            f"quant_map.json schema_version != 1 (got {meta.get('schema_version')!r})"
        )
    for key in ("source_commit_sha", "runtime_commit_sha"):
        sha = meta.get(key, "")
        if not isinstance(sha, str) or not re.fullmatch(r"[0-9a-fA-F]{40}", sha):
            raise RuntimeError(
                f"quant_map.json._meta.{key} is not a 40-char hex SHA "
                f"(got {sha!r}); ADR requires a real pin"
            )
    return data


def _walk_quant_entries(quant_map: dict):
    """Yield every ``{base, tokenizer, ...}`` entry in the quant map.

    Walks the top-level compute-class keys plus any nested entries
    under ``_extras`` (used for override-only quants like F32 that the
    auto-selector doesn't choose but the UI dropdown allows).
    """
    for key, entry in quant_map.items():
        if key == "_meta":
            continue
        if not isinstance(entry, dict):
            continue
        if "base" in entry and "tokenizer" in entry:
            yield key, entry
        if key == "_extras":
            for sub_key, sub_entry in entry.items():
                if isinstance(sub_entry, dict) and "base" in sub_entry and "tokenizer" in sub_entry:
                    yield f"_extras.{sub_key}", sub_entry


def _allowed_quant_filenames(quant_map: Optional[dict] = None) -> set[str]:
    """Return the set of valid quant filenames listed in ``quant_map.json``.

    Used by ``services.settings_store.set_quant_override`` to allow-list
    the UI override against attacker-controlled paths (T-04-05).
    Includes both compute-class auto-select entries and ``_extras``
    override-only entries (e.g. F32).
    """
    q = quant_map if quant_map is not None else _load_quant_map()
    out: set[str] = set()
    for _key, entry in _walk_quant_entries(q):
        for k in ("base", "tokenizer"):
            fn = entry.get(k)
            if isinstance(fn, str):
                out.add(fn)
    return out


#: Env pin for the quant: ``auto`` or a tag (``Q4_K_M``, ``Q8_0``, ``BF16``,
#: ``F32``), or a base filename from quant_map.json.
QUANT_ENV = "OMNIVOICE_GGUF_QUANT"


def env_quant_override() -> Optional[str]:
    """The base filename ``OMNIVOICE_GGUF_QUANT`` names, or None when unset /
    ``auto``. An unknown value is returned as-is so generation reports it
    (a typo must not silently fall back to another precision)."""
    raw = os.environ.get(QUANT_ENV, "").strip()
    if not raw or raw.lower() == "auto":
        return None
    if raw.lower().endswith(".gguf"):
        return raw
    return f"omnivoice-base-{raw.upper()}.gguf"


def _persisted_quant_override() -> Optional[str]:
    """The Settings quant override, or None (auto). Never raises — an
    unreadable settings DB must not stop generation; auto-select applies."""
    try:
        from services.settings_store import get_quant_override

        return get_quant_override()
    except Exception:  # noqa: BLE001
        logger.debug("GGUF quant override unreadable; using auto-select", exc_info=True)
        return None


def _sha256_of_file(p: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


def _is_riff_wave(path: Path) -> bool:
    """True when ``path`` carries the RIFF/WAVE header the CLI can parse.

    The bundled runtime reads WAV containers only (``omnivoice.cpp``
    ``src/wav.h``: ``read_wav_buf`` rejects anything whose first bytes are
    not ``RIFF``/``WAVE``), so anything else has to be converted first.
    """
    try:
        with path.open("rb") as handle:
            header = handle.read(12)
    except OSError:
        return False
    return len(header) == 12 and header[:4] == b"RIFF" and header[8:12] == b"WAVE"


def _reference_audio_properties(prompt: Path) -> Optional[tuple[float, int, int]]:
    """``(duration_s, channels, sample_rate)`` of a reference clip, or None.

    Metadata only — nothing is decoded, so an oversized clip is refused
    before anything is allocated.
    """
    try:
        import soundfile as sf

        info = sf.info(str(prompt))
        return float(info.duration), int(info.channels), int(info.samplerate)
    except Exception:  # noqa: BLE001 — libsndfile cannot read every container
        # M4A/AAC and video files: ask ffprobe for the audio stream's
        # properties instead.
        pass
    try:
        from services.ffmpeg_utils import find_ffprobe, local_inputs_only

        ffprobe = find_ffprobe()
        if not ffprobe:
            return None
        proc = subprocess.run(
            local_inputs_only(
                [ffprobe, "-v", "error", "-select_streams", "a:0",
                 "-show_entries", "stream=channels,sample_rate:format=duration",
                 "-of", "json", str(prompt)],
                tool="ffprobe",
            ),
            capture_output=True, timeout=60, check=False,
        )
        if proc.returncode != 0:
            return None
        payload = json.loads(proc.stdout.decode("utf-8", errors="replace"))
        streams = payload.get("streams") or [{}]
        stream = streams[0]
        duration = float((payload.get("format") or {}).get("duration") or 0.0)
        channels = int(stream.get("channels") or 0)
        sample_rate = int(stream.get("sample_rate") or 0)
        if duration <= 0 or channels <= 0 or sample_rate <= 0:
            return None
        return duration, channels, sample_rate
    except Exception:  # noqa: BLE001 — an unknown size is not a refusal
        return None


def _reference_decoded_bytes(prompt: Path) -> Optional[int]:
    """Decoded size of a reference clip in bytes, or None when unknown."""
    properties = _reference_audio_properties(prompt)
    if properties is None:
        return None
    duration, channels, sample_rate = properties
    return int(duration * sample_rate * channels * 4)  # float32 samples


def _convert_reference_to_wav(prompt: Path) -> Path:
    """Decode ``prompt`` into a temporary WAV the GGUF CLI can read.

    A voice profile keeps the reference clip in whatever container the user
    uploaded (the profile editor accepts MP3, M4A, ...), but the CLI's WAV
    reader only understands RIFF/WAVE — an MP3 reference killed the spawn
    with "[WAV] Not a valid WAV buffer" (#3655). Decode through the app's
    own loader (torchaudio → soundfile → ffmpeg) and write a WAV next to
    the other per-call temp files. The CLI resamples and downmixes the
    prompt itself, so the clip keeps its own rate and channel layout.
    """
    from services.audio_io import atomic_save_wav, load_audio

    decoded = _reference_decoded_bytes(prompt)
    if decoded is not None and decoded > _MAX_REFERENCE_DECODED_BYTES:
        # The upload path caps a reference at 128 MB on disk, but a lossy
        # container decodes to far more than that — and the conversion
        # decodes it in the backend process, while the CLI would do it in
        # its own. Refuse instead of exhausting the backend.
        raise ValueError(
            "The voice profile's reference clip is too long to convert. "
            "Trim it to a shorter clip and upload it again."
        )
    try:
        wav, rate = load_audio(prompt)
    except Exception as exc:  # noqa: BLE001 — any decode failure is "unreadable"
        raise ValueError(
            "The voice profile's reference clip could not be read as audio. "
            "Re-upload the clip as a WAV, MP3, M4A, FLAC or OGG file."
        ) from exc
    fd, name = tempfile.mkstemp(prefix="omnivoice-gguf-ref-", suffix=".wav")
    os.close(fd)
    target = Path(name)
    try:
        atomic_save_wav(str(target), wav, rate)
    except BaseException:
        # BaseException so an interrupted write does not leave the temp file
        # behind either. A write failure keeps its own cause — the user
        # cannot fix it by re-uploading the clip.
        try:
            target.unlink()
        except OSError:
            # Already gone, or not ours to remove.
            pass
        raise
    return target


def _validated_reference(ref_audio: str) -> Path:
    """Resolve a reference clip and check it against the engine's read policy.

    The prompt clip must live under a directory this app owns (voices,
    dubbing workspace or the temp dir) so a request cannot point the
    binary at arbitrary files on disk.
    """
    from core.config import DUB_DIR, VOICES_DIR

    prompt = Path(ref_audio).resolve()
    roots = [
        Path(VOICES_DIR).resolve(),
        Path(DUB_DIR).resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    if not any(prompt == root or prompt.is_relative_to(root) for root in roots):
        raise FileNotFoundError(
            f"Reference audio is outside the directories this engine may read: {prompt}"
        )
    if not prompt.is_file():
        raise FileNotFoundError(f"Reference audio file does not exist: {prompt}")
    return prompt


def _automatic_entry(quant_map: dict) -> dict:
    """The quant entry the automatic compute policy selects.

    Sized on the Vulkan adapter the native runtime will open when one is
    verified (``compute.automatic_compute_class``); the conservative tier
    otherwise. An explicit override is handled by the caller.
    """
    from .compute import automatic_compute_class

    entry = quant_map.get(automatic_compute_class())
    if isinstance(entry, dict):
        return entry
    # No matching quant class — fall back.
    return quant_map.get("cpu", {"base": "", "tokenizer": ""})


def _weights_install_command(entry: dict, revision: str) -> str:
    """The pinned command that pre-installs one quant pair by hand.

    The engine downloads a missing pair itself on first generate; this
    is the fallback for a host that cannot reach Hugging Face from the
    app (it must land in the cache this app reads).
    """
    return (
        f"hf download {_HF_REPO_ID} {entry.get('base')} "
        f"{entry.get('tokenizer')} --revision {revision} "
        f"--cache-dir \"<the cache directory this app reads>\""
    )


def _weights_missing_reason(entry: dict, revision: str, exc: BaseException | None = None) -> str:
    """Actionable reason when the pinned pair is neither cached nor fetchable."""
    cause = f" ({type(exc).__name__}: {exc})" if exc is not None else ""
    return (
        f"Could not download the GGUF weights {entry.get('base')} + "
        f"{entry.get('tokenizer')}{cause}. Check the connection to "
        "huggingface.co (or set a mirror in Settings) and try again, or "
        "pre-install the pinned pair with:\n\n    "
        f"{_weights_install_command(entry, revision)}"
    )


class ChecksumManifestError(RuntimeError):
    """``bin/checksums.sha256`` exists but cannot be trusted as written."""


_SHA256_RE = re.compile(r"[0-9a-fA-F]{64}")
_BSD_LINE_RE = re.compile(r"SHA256 \((?P<name>.+)\) = (?P<digest>\S+)")


def _load_checksum_manifest() -> dict[str, str]:
    """Load ``bin/checksums.sha256`` if present.

    Accepts ``sha256sum`` output (``<hex>  [*]<filename>``) and the BSD
    ``SHA256 (<filename>) = <hex>`` form. File names are compared by base name.

    A missing manifest means verification was not requested — source
    checkouts never have one. A manifest that exists but cannot be read, or
    that has a malformed or conflicting line, raises
    :class:`ChecksumManifestError`: an entry that may have declared a checksum
    must never be skipped silently.
    """
    manifest = _REPO_ROOT / "bin" / "checksums.sha256"
    out: dict[str, str] = {}
    if not manifest.is_file():
        return out
    try:
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ChecksumManifestError("bin/checksums.sha256 could not be read") from exc
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        bsd = _BSD_LINE_RE.fullmatch(line)
        if bsd:
            digest, name = bsd.group("digest"), bsd.group("name")
        else:
            parts = line.split(None, 1)
            if len(parts) != 2:
                raise ChecksumManifestError("bin/checksums.sha256 has a malformed line")
            digest, name = parts
            # Strip the BSD-style "*" binary marker if present.
            name = name.lstrip("*").strip()
        name = os.path.basename(name.replace("\\", "/"))
        if not name or not _SHA256_RE.fullmatch(digest):
            raise ChecksumManifestError("bin/checksums.sha256 has a malformed line")
        digest = digest.lower()
        if out.get(name, digest) != digest:
            raise ChecksumManifestError(
                f"bin/checksums.sha256 lists conflicting checksums for {name}"
            )
        out[name] = digest
    return out


def _is_macos_quarantined(p: Path) -> bool:
    """Return True if the macOS quarantine xattr is set on ``p``.

    No-op on non-Darwin platforms (returns False). Uses ``/usr/bin/xattr``
    via subprocess because the Python ``os.getxattr`` API is not present
    on macOS (it's Linux-only). Catches errors silently — if we can't tell,
    we assume the binary is usable rather than blocking on a false-positive
    quarantine flag.
    """
    if sys.platform != "darwin":
        return False
    try:
        proc = subprocess.run(
            ["/usr/bin/xattr", "-p", "com.apple.quarantine", str(p)],
            capture_output=True,
            text=True,
            errors="replace",
            timeout=2,
        )
        # Exit code 0 means the xattr exists; non-zero usually means
        # "no such xattr" (the binary is fine).
        return proc.returncode == 0 and bool(proc.stdout.strip())
    except Exception:
        return False


# ── TTSBackend subclass ─────────────────────────────────────────────────────


def _import_tts_backend():
    """Lazy-import ``services.tts_backend.TTSBackend``.

    The import has to be lazy because ``services.tts_backend`` registers
    this module in ``_LAZY_REGISTRY`` and is therefore on this module's
    import path. Pulling it eagerly would cycle through the registry.
    """
    from services.tts_backend import TTSBackend

    return TTSBackend


# A module-level class object that subclasses TTSBackend, created on
# first attribute access via __getattr__ below. This lets us register
# the class in ``_LAZY_REGISTRY`` without forcing the (heavy) `torch`
# import that ``tts_backend`` triggers transitively.
_OmniVoiceGGUFBackend_cls = None


def _make_backend_class():
    """Construct the OmniVoiceGGUFBackend class on first access."""
    global _OmniVoiceGGUFBackend_cls
    if _OmniVoiceGGUFBackend_cls is not None:
        return _OmniVoiceGGUFBackend_cls

    TTSBackend = _import_tts_backend()

    class OmniVoiceGGUFBackend(TTSBackend):
        """Hardware-adaptive GGUF wrapper for `Serveurperso/OmniVoice-GGUF`.

        Each ``generate()`` spawns the bundled ``bin/omnivoice-tts-<platform>``
        binary fresh; the binary loads the SHA-pinned quants from
        ``$HF_HUB_CACHE`` and writes a 24 kHz mono WAV to a temp path
        which we then load back into a ``torch.Tensor``.

        ``compute_class`` → quant selection is driven by the native
        capacity policy in ``compute.automatic_compute_class()`` and the shipped
        ``quant_map.json`` table. The user can override the selection
        from Settings (allow-listed against the same JSON).
        """

        id = "omnivoice-gguf"
        display_name = "OmniVoice (GGUF, hardware-adaptive)"
        instruct_vocabulary = "tags"
        gpu_compat = ("cuda", "vulkan", "mps", "cpu")

        supports_voice_design = False
        # The seed reaches the binary as --seed; the parent's torch RNG never
        # does, so /generate must forward it (#3413).
        supports_generation_seed = True
        # Every generate() spawns the external binary — allocations live in
        # that process, invisible to parent-side accelerator counters.
        runs_out_of_process = True

        # 24 kHz mono Higgs Audio v2 — same as the in-process OmniVoice.
        _SAMPLE_RATE = 24_000

        # Quick probe at startup to confirm the binary spawns at all.
        _PROBE_TIMEOUT_S = 5.0

        @classmethod
        def runtime_compute_profile(cls, caps: HostCaps) -> dict:
            """Keep budgets conservative until the native device is verified."""
            from .compute import runtime_profile
            return runtime_profile(cls, caps)

        def __init__(self, quant_override: Optional[str] = None):
            """Construct without spawning the binary.

            ``quant_override`` — if provided, force this quant filename
            (must be in ``quant_map.json``'s allow-list). If ``None`` or
            ``"auto"``, the native capacity policy + ``quant_map.json`` decide.
            """
            self._quant_override = quant_override
            self._quant_map: Optional[dict] = None

        # ── TTSBackend protocol ───────────────────────────────────────

        @classmethod
        def _binary_status(cls) -> tuple[bool, str]:
            """Verify the bundled runtime binary.

            Existence, executable-magic, SHA-256 manifest, macOS
            quarantine and the POSIX execute bit. Never raises — failures
            roll up as ``(False, reason)``.
            """
            try:
                bin_path = _binary_path()
                if not bin_path.is_file():
                    return False, (
                        f"GGUF binary missing at {bin_path.name} — "
                        f"this build does not bundle the runtime for "
                        f"{_platform_slug()}. Fall back to OmniVoice in-process."
                    )
                # #1172: a source checkout ships zero-byte placeholders in
                # bin/ (real binaries come from CI / the installer), and the
                # checksum manifest is absent there — so without this check a
                # placeholder passed as "ready", got chmod +x'd by the #437
                # self-heal below, and died at spawn time with a bare
                # "[Errno 8] Exec format error" 500. Validate BEFORE the
                # checksum/quarantine/exec-bit steps so we never bless (or
                # chmod) a file that isn't a real executable.
                from services.binary_preflight import looks_like_executable
                bin_ok, bin_why = looks_like_executable(bin_path)
                if not bin_ok:
                    return False, (
                        f"GGUF binary {bin_path.name} is not a usable "
                        f"executable: {bin_why}. Source checkouts ship "
                        f"zero-byte placeholders until a real binary is "
                        f"built — run `scripts/build-omnivoice-tts.sh "
                        f"--platform {_platform_slug()}`, reinstall "
                        f"VoiceStudio, or use the default in-process "
                        f"OmniVoice engine (Model Catalogue)."
                    )
                known_bad = _CPU_INCOMPATIBLE.get(str(bin_path))
                if known_bad is not None:
                    if _file_identity(bin_path) == known_bad:
                        return False, _cpu_incompatible_reason(bin_path)
                    _CPU_INCOMPATIBLE.pop(str(bin_path), None)  # replaced: re-check
                # Manifest-based SHA-256 verification (T-04-01).
                try:
                    manifest = _load_checksum_manifest()
                except ChecksumManifestError as exc:
                    return False, (
                        f"GGUF binary {bin_path.name} could not be verified: "
                        f"{exc}. Reinstall VoiceStudio or use the default "
                        f"in-process OmniVoice engine."
                    )
                expected = manifest.get(bin_path.name)
                if expected is not None:
                    actual = _sha256_of_file(bin_path)
                    if actual.lower() != expected.lower():
                        return False, (
                            f"GGUF binary checksum mismatch for "
                            f"{bin_path.name} — manifest says "
                            f"{expected[:12]}…, actual is {actual[:12]}…. "
                            f"Reinstall or run the bundled `xattr -cr` cleanup."
                        )
                if manifest:
                    bad_lib = _mismatched_runtime_library(bin_path.parent, manifest)
                    if bad_lib is not None:
                        return False, (
                            f"GGUF runtime library checksum mismatch for "
                            f"{bad_lib} — the runtime would load a library "
                            f"other than the one that was built. Reinstall "
                            f"VoiceStudio or rebuild with "
                            f"`scripts/build-omnivoice-tts.sh`."
                        )
                # macOS Gatekeeper quarantine detection (Pitfall 3).
                if _is_macos_quarantined(bin_path):
                    return False, (
                        f"GGUF binary {bin_path.name} is quarantined by "
                        f"macOS Gatekeeper. Run:\n\n"
                        f"    xattr -cr '/Applications/VoiceStudio.app'\n\n"
                        f"This clears the quarantine on the .app and its "
                        f"bundled binaries. See docs/install/macos.md."
                    )
                # Execute bit (issue #437). A `git clone` / zip extract on POSIX
                # can drop +x, which only surfaces at spawn time as
                # "[Errno 13] Permission denied" — and the generic synth handler
                # then mislabels it as out-of-memory. Self-heal here, AFTER the
                # SHA check has confirmed this is the right file (so we never
                # chmod a foreign binary). No-op on Windows.
                if os.name == "posix" and not os.access(bin_path, os.X_OK):
                    try:
                        bin_path.chmod(bin_path.stat().st_mode | 0o111)
                    except OSError:
                        return False, (
                            f"GGUF binary {bin_path.name} isn't executable and "
                            f"couldn't be made so — run `chmod +x {bin_path}` "
                            f"and retry."
                        )
                return True, "ready"
            except Exception as exc:
                return False, f"{type(exc).__name__}: {exc}"

        @classmethod
        def is_available(cls) -> tuple[bool, str]:
            """Verify the bundled runtime; weights are not a readiness gate.

            A missing weight pair is fetched on first generate (see
            ``_resolve_quant_paths``), so a fresh install with a working
            binary is a usable engine — the same contract as before
            #3042. Never raises — failures roll up as ``(False, reason)``
            so ``list_backends()`` can render the picker even when the
            engine is broken.
            """
            return cls._binary_status()

        @property
        def sample_rate(self) -> int:
            return self._SAMPLE_RATE

        @property
        def supported_languages(self) -> list[str]:
            # Same multilingual surface as the in-process OmniVoice —
            # the model is the same. ``omnivoice-tts --lang`` accepts
            # English, French, German, etc. mapped from ISO codes by
            # ``_iso_to_omnivoice_lang``.
            return ["multi"]

        # ── helpers ───────────────────────────────────────────────────

        def _ensure_quant_map(self) -> dict:
            if self._quant_map is None:
                self._quant_map = _load_quant_map()
            return self._quant_map

        def _select_quant_entry(self) -> dict:
            """Return the ``{base, tokenizer, rationale}`` dict for the
            quant the selected compute device should use (or the override).

            Always returns a dict — falls back to ``cpu`` if the selected
            compute class has no quant entry (defense in depth).
            """
            q = self._ensure_quant_map()

            # User override wins (allow-listed at write time in settings_store).
            override = self._explicit_override()
            if override:
                allowed = _allowed_quant_filenames(q)
                if override in allowed:
                    # Walk both top-level and _extras entries looking for
                    # a quant whose ``base`` matches the override.
                    for _key, entry in _walk_quant_entries(q):
                        if entry.get("base") == override:
                            return entry

            # The parent's GPU memory cannot prove the native adapter's capacity.
            return _automatic_entry(q)

        def _explicit_override(self) -> str | None:
            """The quant filename the user pinned, if any.

            Constructor argument > ``OMNIVOICE_GGUF_QUANT`` > the Settings
            choice. Env wins over Settings, like every engine choice, so a
            power-user pin is never silently undone by the UI. Before the
            Settings read existed the registry (which constructs this class
            without arguments) never applied a stored override at all.
            """
            if self._quant_override is not None:
                value = self._quant_override
            else:
                value = env_quant_override() or _persisted_quant_override()
            if not value or value == "auto":
                return None
            return value

        def _resolve_quant_paths(self, *, download: bool = True) -> tuple[Path, Path, dict]:
            """Return the base+tokenizer paths for this engine.

            An installed pair at the pinned revision always wins, so an
            existing installation is never re-downloaded. Only when no
            usable pair is cached — and ``download`` is true — is the
            selected pair fetched from Hugging Face (first generate):

            * an explicit quant override is strict — only that exact
              pair counts, and a missing one is reported instead of
              silently running a different precision;
            * an explicit CPU choice keeps the CPU tier's quant policy,
              so a larger cached quant is not used in its place;
            * otherwise the selected tier is used when installed, and an
              already-installed automatic pair is accepted as a fallback
              so an existing installation is never re-downloaded.

            Raises ``RuntimeError`` with the pinned install command when
            no usable pair is present and it cannot be downloaded.
            """
            q = self._ensure_quant_map()
            revision = q["_meta"]["source_commit_sha"]
            entry = self._select_quant_entry()
            override = self._explicit_override()
            if override and entry.get("base") != override:
                raise RuntimeError(
                    f"the configured GGUF quant override {override!r} names no "
                    f"known quant base file — clear the override to use the "
                    f"automatic selection"
                )
            if not entry.get("base") or not entry.get("tokenizer"):
                raise RuntimeError(
                    "quant_map.json entry missing base/tokenizer filenames; "
                    "cannot resolve quants"
                )
            from .compute import cpu_selection_explicit

            allow_fallback = not override and not cpu_selection_explicit()
            pair = resolve_installed_pair(
                q,
                entry,
                repo_id=_HF_REPO_ID,
                revision=revision,
                allow_fallback=allow_fallback,
            )
            if pair is not None:
                return pair[0], pair[1], pair[2]
            if not download:
                raise RuntimeError(_weights_missing_reason(entry, revision))
            try:
                from huggingface_hub import hf_hub_download

                base_path = Path(hf_hub_download(
                    repo_id=_HF_REPO_ID, filename=entry["base"], revision=revision,
                ))
                tok_path = Path(hf_hub_download(
                    repo_id=_HF_REPO_ID, filename=entry["tokenizer"], revision=revision,
                ))
            except Exception as exc:
                raise RuntimeError(_weights_missing_reason(entry, revision, exc)) from exc
            return base_path, tok_path, entry

        @classmethod
        def probe_load(cls, *, quant: str | None = None, timeout: float = 5.0) -> None:
            """Spawn the binary with ``--help`` to confirm it loads cleanly.

            Lightweight check of the *runtime* only — it does not read
            the weight cache, does not download quants and does not run
            inference. Whether usable weights are installed is
            :meth:`is_available`'s job; the two are separate questions
            about separate files. The actual quant load is deferred to
            the first ``generate()`` call.

            Raises:
                RuntimeError — binary is unavailable, quarantined, or
                    exited non-zero on ``--help``.
                FileNotFoundError — binary missing.
                subprocess.TimeoutExpired — binary hung past ``timeout``.
            """
            ok, reason = cls._binary_status()
            if not ok:
                raise RuntimeError(f"GGUF engine unavailable: {reason}")
            bin_path = _binary_path()
            try:
                proc = subprocess.run(
                    [str(bin_path), "--help"],
                    capture_output=True,
                    timeout=timeout,
                    check=False,
                    env=_spawn_env(),
                    # ggml's dynamic backend loader also scans the working
                    # directory; pin it to the verified bin/ directory.
                    cwd=str(bin_path.parent),
                )
            except FileNotFoundError:
                raise
            except subprocess.TimeoutExpired:
                raise
            except OSError as exc:
                # ENOEXEC / EACCES etc. — the file exists but the OS refused
                # to exec it (#1172 class: placeholder or wrong-arch binary
                # that slipped past preflight). Typed + actionable, never a
                # bare errno.
                from services.binary_preflight import InvalidBinaryError
                raise InvalidBinaryError(
                    bin_path, f"the OS refused to execute it ({exc})",
                    _binary_repair_hint(),
                ) from exc
            _raise_if_illegal_instruction(bin_path, proc.returncode)
            # `--help` typically exits 0 or 1 (some CLIs use 1 to signal
            # "help shown, no work done"). We accept both as long as the
            # binary actually emitted something.
            if proc.returncode not in (0, 1) and not proc.stdout and not proc.stderr:
                raise RuntimeError(
                    f"GGUF binary {bin_path.name} exited "
                    f"{proc.returncode} with no output on --help; "
                    f"binary may be corrupt"
                )

        # ── generation ────────────────────────────────────────────────

        def generate(self, text: str, **kw):
            """Synthesise ``text`` by running the GGUF CLI once.

            Keyword arguments understood (anything else is ignored):

            * ``ref_audio`` – path of a voice prompt clip to clone from.
            * ``ref_text`` – transcript of that clip; only used together with
              ``ref_audio`` and passed to the CLI through a temporary file.
            * ``language`` – ISO code or name; unknown values let the CLI guess.
            * ``instruct`` – free-text voice-design instruction.
            * ``duration`` – target length in seconds.
            * ``seed`` – integer seed for reproducible sampling.
            * ``denoise`` – set False to skip prompt denoising (default True).
            * ``preprocess_prompt`` – set False to feed the prompt as-is
              (default True).
            * ``chunk_duration`` / ``chunk_threshold`` – long-text chunking
              controls forwarded unchanged.

            Returns a float32 tensor shaped ``(1, n_samples)`` at the model's
            native rate.
            """
            import soundfile as sf
            import torch

            base, tokenizer, _ = self._resolve_quant_paths()
            ref_audio = kw.get("ref_audio")
            ref_text = kw.get("ref_text")

            fd, out_name = tempfile.mkstemp(suffix=".wav")
            os.close(fd)
            out_path = Path(out_name)
            transcript_path: Optional[Path] = None
            converted_prompt: Optional[Path] = None
            try:
                if ref_audio and ref_text:
                    # A file keeps arbitrary transcript text out of argv.
                    t_fd, t_name = tempfile.mkstemp(suffix=".transcript")
                    transcript_path = Path(t_name)
                    with os.fdopen(t_fd, "w", encoding="utf-8") as handle:
                        handle.write(str(ref_text))
                # The CLI reads WAV containers only, so a profile reference
                # stored as MP3/M4A/... has to be converted before it is
                # handed over (#3655). The original path is validated first —
                # the conversion reads the file.
                if ref_audio:
                    prompt = _validated_reference(ref_audio)
                    if not _is_riff_wave(prompt):
                        converted_prompt = _convert_reference_to_wav(prompt)
                        ref_audio = str(converted_prompt)
                argv = self._build_argv(
                    base=base,
                    tokenizer=tokenizer,
                    out_path=out_path,
                    ref_audio=ref_audio,
                    ref_text=str(transcript_path) if transcript_path else None,
                    language=kw.get("language"),
                    instruct=kw.get("instruct"),
                    duration=kw.get("duration"),
                    seed=kw.get("seed"),
                    denoise=kw.get("denoise", True),
                    preprocess_prompt=kw.get("preprocess_prompt", True),
                    chunk_duration=kw.get("chunk_duration"),
                    chunk_threshold=kw.get("chunk_threshold"),
                )
                self._run_subprocess(argv, stdin_text=text)
                samples, _rate = sf.read(str(out_path), dtype="float32")
            finally:
                for leftover in (out_path, transcript_path, converted_prompt):
                    if leftover is None:
                        continue
                    try:
                        leftover.unlink(missing_ok=True)
                    except OSError:
                        pass

            if samples.ndim > 1:
                samples = samples.mean(axis=1)
            return torch.from_numpy(samples.astype("float32", copy=False)).unsqueeze(0)

        def _build_argv(
            self,
            *,
            base: Path,
            tokenizer: Path,
            out_path: Path,
            ref_audio: Optional[str],
            ref_text: Optional[str],
            language: Optional[str],
            instruct: Optional[str] = None,
            duration: Optional[float] = None,
            seed: Optional[int] = None,
            denoise: bool = True,
            preprocess_prompt: bool = True,
            chunk_duration: Optional[float] = None,
            chunk_threshold: Optional[float] = None,
        ) -> list[str]:
            """Assemble the CLI argument list; ``ref_text`` is a staged file path.

            The prompt clip must live under a directory this app owns (voices,
            dubbing workspace or the temp dir) so a request cannot point the
            binary at arbitrary files on disk.
            """
            argv = [
                str(_binary_path()),
                "--model", str(base),
                "--codec", str(tokenizer),
                "-o", str(out_path),
            ]
            lang = _iso_to_omnivoice_lang(language)
            if lang:
                argv += ["--lang", lang]
            if isinstance(instruct, str) and instruct:
                argv += ["--instruct", instruct]
            if duration is not None:
                argv += ["--duration", str(float(duration))]
            if seed is not None:
                argv += ["--seed", str(int(seed))]
            if chunk_duration is not None:
                argv += ["--chunk-duration", str(float(chunk_duration))]
            if chunk_threshold is not None:
                argv += ["--chunk-threshold", str(float(chunk_threshold))]
            if denoise is False:
                argv.append("--no-denoise")
            if preprocess_prompt is False:
                argv.append("--no-preprocess-prompt")

            if ref_audio:
                # Two-stage validation (defense in depth) — see
                # :func:`_validated_reference`.
                prompt = _validated_reference(ref_audio)
                argv += ["--ref-wav", str(prompt)]
                if ref_text:
                    argv += ["--ref-text", str(ref_text)]
            return argv

        def _run_subprocess(self, argv: list[str], *, stdin_text: str) -> None:
            """Spawn the binary, feed ``stdin_text``, enforce timeout.

            Never uses ``shell=True``. Stderr is captured and HF-token-redacted
            before being logged at warning level.
            """
            # #1172 class: validate the binary immediately before exec (the
            # engine may have been selected explicitly, bypassing
            # is_available; or the file changed since the last probe). A
            # placeholder/corrupt binary raises the typed, actionable
            # InvalidBinaryError instead of "[Errno 8] Exec format error".
            from services.binary_preflight import (
                InvalidBinaryError,
                validate_executable,
            )
            validate_executable(Path(argv[0]), hint=_binary_repair_hint())
            timeout_s = _generate_timeout_s()
            try:
                proc = subprocess.run(
                    argv,
                    input=stdin_text,
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    capture_output=True,
                    timeout=timeout_s,
                    check=False,
                    env=_spawn_env(),
                    # See probe_load: keep ggml's backend scan inside bin/.
                    cwd=str(_binary_path().parent),
                )
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError(
                    f"GGUF subprocess timed out after "
                    f"{timeout_s:.0f}s (T-04-06; raise "
                    f"OMNIVOICE_GGUF_GENERATE_TIMEOUT_S if this host is "
                    f"genuinely that slow)"
                ) from exc
            except OSError as exc:
                raise InvalidBinaryError(
                    argv[0], f"the OS refused to execute it ({exc})",
                    _binary_repair_hint(),
                ) from exc

            _raise_if_illegal_instruction(Path(argv[0]), proc.returncode)
            if proc.returncode != 0:
                stderr = _mask_token(proc.stderr or "")
                raise RuntimeError(
                    f"GGUF subprocess exited {proc.returncode}: "
                    f"{stderr.strip() or '<no stderr>'}"
                )
            # On success the binary may still write progress/info to
            # stderr — log at info, redacted.
            if proc.stderr:
                logger.info("[omnivoice-gguf] %s", _mask_token(proc.stderr.strip()))

    _OmniVoiceGGUFBackend_cls = OmniVoiceGGUFBackend
    return _OmniVoiceGGUFBackend_cls


def _iso_to_omnivoice_lang(code: Optional[str]) -> Optional[str]:
    """Map ISO-639 codes (and a few aliases) to omnivoice-tts language labels.

    ``omnivoice-tts`` expects names like ``English``, ``French``, etc.
    per the README example. Anything we don't recognize is dropped (the
    binary will auto-detect from the prompt).
    """
    if not code:
        return None
    c = code.strip().lower()
    if c in ("auto", "multi", ""):
        return None
    return _ISO_TO_OV.get(c, c.capitalize())


_ISO_TO_OV = {
    "en": "English",
    "fr": "French",
    "de": "German",
    "es": "Spanish",
    "it": "Italian",
    "pt": "Portuguese",
    "zh": "Chinese",
    "ja": "Japanese",
    "ko": "Korean",
    "ar": "Arabic",
    "ru": "Russian",
}


def __getattr__(name: str):
    """Lazy-construct the backend class on first access.

    Mirrors the pattern used by ``backend/engines/indextts/__init__.py`` —
    keeps ``import engines.omnivoice_gguf.backend`` cheap (no torch /
    huggingface_hub pulled at import) while still supporting
    ``from .backend import OmniVoiceGGUFBackend``.
    """
    if name == "OmniVoiceGGUFBackend":
        return _make_backend_class()
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# ── module-level default-engine resolver (GGUF-05) ──────────────────────────


def select_default_engine() -> str:
    """Return the engine id for the default cloning engine on this host.

    Returns ``"omnivoice-gguf"`` when:
        * the binary is present, checksum matches, not Gatekeeper-quarantined
        * ``probe_load`` succeeds (binary actually executes)

    Returns ``"omnivoice"`` (the existing in-process default) on any
    failure. The fallback is deliberately silent — a user who hits this
    code path still gets a working cloning engine; the failure surfaces
    in the Model Catalogue Compatibility Matrix (Plan 02-04) so the
    user can investigate if they care to.
    """
    cls = _make_backend_class()
    try:
        ok, reason = cls.is_available()
        if not ok:
            logger.info(
                "GGUF default-engine probe: not available (%s); falling back",
                reason,
            )
            return "omnivoice"
        cls.probe_load(timeout=cls._PROBE_TIMEOUT_S)
        return "omnivoice-gguf"
    except (RuntimeError, FileNotFoundError, subprocess.TimeoutExpired) as exc:
        logger.warning(
            "GGUF default-engine probe failed; falling back to in-process: %s", exc,
        )
        return "omnivoice"
    except Exception:
        # Last-resort net so a bug in the probe can never block app start.
        logger.exception(
            "GGUF default-engine probe raised unexpectedly; falling back"
        )
        return "omnivoice"


__all__ = [
    "OmniVoiceGGUFBackend",
    "select_default_engine",
    "_allowed_quant_filenames",
    "_load_quant_map",
    "_platform_slug",
    "_binary_path",
]
