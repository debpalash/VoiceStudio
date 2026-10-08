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
    T-04-03 — GGUF quant served from network: ``huggingface_hub`` enforces
              SHA verification at download time; revision is pinned to
              ``quant_map.json._meta.source_commit_sha``.
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
from typing import Optional

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
    return env


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


def _sha256_of_file(p: Path, *, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with p.open("rb") as f:
        for buf in iter(lambda: f.read(chunk), b""):
            h.update(buf)
    return h.hexdigest()


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

        ``compute_class`` → quant selection is driven by
        ``hardware_probe.detect_capabilities()`` and the shipped
        ``quant_map.json`` table. The user can override the selection
        from Settings (allow-listed against the same JSON).
        """

        id = "omnivoice-gguf"
        display_name = "OmniVoice (GGUF, hardware-adaptive)"
        instruct_vocabulary = "tags"
        gpu_compat = ("cuda", "mps", "cpu")
        supports_voice_design = False
        # Every generate() spawns the external binary — allocations live in
        # that process, invisible to parent-side accelerator counters.
        runs_out_of_process = True

        # 24 kHz mono Higgs Audio v2 — same as the in-process OmniVoice.
        _SAMPLE_RATE = 24_000

        # Quick probe at startup to confirm the binary spawns at all.
        _PROBE_TIMEOUT_S = 5.0

        def __init__(self, quant_override: Optional[str] = None):
            """Construct without spawning the binary.

            ``quant_override`` — if provided, force this quant filename
            (must be in ``quant_map.json``'s allow-list). If ``None`` or
            ``"auto"``, the probe + ``quant_map.json`` decide.
            """
            self._quant_override = quant_override
            self._quant_map: Optional[dict] = None

        # ── TTSBackend protocol ───────────────────────────────────────

        @classmethod
        def is_available(cls) -> tuple[bool, str]:
            """Verify the binary is present, checksum matches, not quarantined.

            Never raises — failures roll up as ``(False, reason)`` so
            ``list_backends()`` can render the picker even when the
            engine is broken.
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
            quant the current hardware should use (or the override).

            Always returns a dict — falls back to ``cpu`` if the probe
            returns an unknown compute_class (defense in depth).
            """
            q = self._ensure_quant_map()

            # User override wins (allow-listed at write time in settings_store).
            if self._quant_override and self._quant_override != "auto":
                allowed = _allowed_quant_filenames(q)
                if self._quant_override in allowed:
                    # Walk both top-level and _extras entries looking for
                    # a quant whose ``base`` matches the override.
                    for _key, entry in _walk_quant_entries(q):
                        if entry.get("base") == self._quant_override:
                            return entry

            # Auto-select by compute class.
            from .hardware_probe import detect_capabilities

            caps = detect_capabilities()
            entry = q.get(caps.compute_class)
            if isinstance(entry, dict):
                return entry
            # Probe returned a class we don't have a row for — fall back.
            return q.get("cpu", {"base": "", "tokenizer": ""})

        def _resolve_quant_paths(self) -> tuple[Path, Path, dict]:
            """Download (cached) the base+tokenizer quants and return paths.

            Raises ``RuntimeError`` if the download fails (e.g. network
            block, HF auth required for a gated upstream the GGUF derives
            from). The caller's ``is_available`` / ``probe_load`` is
            expected to short-circuit before reaching here when network
            is unreachable.
            """
            entry = self._select_quant_entry()
            base_name = entry.get("base")
            tok_name = entry.get("tokenizer")
            if not base_name or not tok_name:
                raise RuntimeError(
                    "quant_map.json entry missing base/tokenizer filenames; "
                    "cannot resolve quants"
                )

            from huggingface_hub import hf_hub_download

            meta = self._ensure_quant_map()["_meta"]
            rev = meta["source_commit_sha"]
            base_path = Path(
                hf_hub_download(
                    repo_id=_HF_REPO_ID,
                    filename=base_name,
                    revision=rev,
                )
            )
            tok_path = Path(
                hf_hub_download(
                    repo_id=_HF_REPO_ID,
                    filename=tok_name,
                    revision=rev,
                )
            )
            return base_path, tok_path, entry

        @classmethod
        def probe_load(cls, *, quant: Optional[str] = None, timeout: float = 5.0) -> None:
            """Spawn the binary with ``--help`` to confirm it loads cleanly.

            Lightweight check — does not download quants, does not run
            inference. The actual quant load is deferred to the first
            ``generate()`` call (lazy) so a host with no network at
            startup still passes the probe.

            Raises:
                RuntimeError — binary is unavailable, quarantined, or
                    exited non-zero on ``--help``.
                FileNotFoundError — binary missing.
                subprocess.TimeoutExpired — binary hung past ``timeout``.
            """
            ok, reason = cls.is_available()
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
            """Synthesize ``text`` and return a ``torch.Tensor`` of shape (1, n_samples).

            Recognized kwargs (others are silently ignored — keeps the
            API surface lean and prevents accidental misuse):
              * ``ref_audio`` (str/Path) — speaker reference WAV for cloning.
              * ``ref_text`` (str) — transcript of ``ref_audio``.
              * ``language`` (str) — ISO code or omnivoice-tts lang label.
              * ``instruct`` (str) — style instruction.
              * ``duration`` (float) — target duration in seconds.
              * ``seed`` (int) — deterministic sampling seed.
              * ``denoise`` (bool) — omit denoise token when false.
              * ``preprocess_prompt`` (bool) — skip prompt preprocessing when false.
              * ``chunk_duration`` / ``chunk_threshold`` (float) — binary long-form controls.
            """
            import soundfile as sf  # local import keeps module import cheap
            import torch

            base_path, tok_path, _entry = self._resolve_quant_paths()

            # Write the WAV to a temp file; we'll read it back as a tensor.
            fd, out_str = tempfile.mkstemp(prefix="omnivoice-gguf-", suffix=".wav")
            os.close(fd)
            out_path = Path(out_str)
            ref_text_path: Optional[Path] = None

            try:
                ref_text = kw.get("ref_text")
                if kw.get("ref_audio") and ref_text:
                    text_fd, text_str = tempfile.mkstemp(
                        prefix="omnivoice-gguf-ref-", suffix=".txt"
                    )
                    os.close(text_fd)
                    ref_text_path = Path(text_str)
                    ref_text_path.write_text(str(ref_text), encoding="utf-8")

                argv = self._build_argv(
                    base=base_path,
                    tokenizer=tok_path,
                    out_path=out_path,
                    ref_audio=kw.get("ref_audio"),
                    ref_text=str(ref_text_path) if ref_text_path else None,
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
                wav, sr = sf.read(str(out_path))
            finally:
                try:
                    out_path.unlink()
                except OSError:
                    pass
                if ref_text_path is not None:
                    try:
                        ref_text_path.unlink()
                    except OSError:
                        pass

            # soundfile returns (n,) for mono or (n, c) for multichannel.
            # OmniVoice/Higgs Audio v2 is mono → (n,). Wrap to (1, n).
            if wav.ndim == 1:
                tensor = torch.from_numpy(wav).unsqueeze(0).float()
            else:
                # Defensive: if the binary ever returns stereo, take mean.
                tensor = torch.from_numpy(wav.mean(axis=1)).unsqueeze(0).float()

            # SubprocessBackend convention: return float32 in [-1, 1] at
            # ``self.sample_rate`` (which we declare as 24_000). If the
            # binary ever returns a different rate, the caller can resample
            # — we don't second-guess here.
            return tensor

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
            """Compose argv from typed Path objects only (T-04-02)."""
            argv: list[str] = [
                str(_binary_path()),
                "--model", str(base),
                "--codec", str(tokenizer),
                "-o", str(out_path),
            ]
            lang = _iso_to_omnivoice_lang(language)
            if lang:
                argv += ["--lang", lang]
            if instruct:
                argv += ["--instruct", str(instruct)]
            if duration is not None:
                argv += ["--duration", str(float(duration))]
            if seed is not None:
                argv += ["--seed", str(int(seed))]
            if denoise is False:
                argv += ["--no-denoise"]
            if preprocess_prompt is False:
                argv += ["--no-preprocess-prompt"]
            if chunk_duration is not None:
                argv += ["--chunk-duration", str(float(chunk_duration))]
            if chunk_threshold is not None:
                argv += ["--chunk-threshold", str(float(chunk_threshold))]
            if ref_audio:
                # Two-stage validation (defense in depth):
                #   (a) Reject anything outside the project's voices /
                #       dub-jobs trees + the system temp dir. Test
                #       `test_generate_blocks_freeform_ref_audio` proved
                #       on Linux that an existence-only check accepts
                #       sensitive paths like /etc/shadow.
                #   (b) Then confirm the constrained path actually exists.
                ref_path = Path(str(ref_audio)).resolve()
                from core.config import VOICES_DIR, DUB_DIR
                allowed_roots = [
                    Path(VOICES_DIR).resolve(),
                    Path(DUB_DIR).resolve(),
                    Path(tempfile.gettempdir()).resolve(),
                ]
                inside_allowed = any(
                    ref_path == root or root in ref_path.parents
                    for root in allowed_roots
                )
                if not inside_allowed:
                    # FileNotFoundError keeps the failure mode consistent
                    # with the existing test's expectation and the prior
                    # validation contract (callers never differentiate
                    # "outside policy" from "missing file" — both are bad).
                    raise FileNotFoundError(
                        f"ref_audio outside allowed roots: {ref_path}"
                    )
                if not ref_path.is_file():
                    raise FileNotFoundError(
                        f"ref_audio not found: {ref_path}"
                    )
                argv += ["--ref-wav", str(ref_path)]
                if ref_text:
                    # The C++ runtime expects a transcript file path.
                    # generate() creates this file in the system temp dir.
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
                    capture_output=True,
                    timeout=timeout_s,
                    check=False,
                    env=_spawn_env(),
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
