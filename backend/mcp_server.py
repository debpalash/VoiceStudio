"""
VoiceStudio MCP Server — expose voice synthesis as AI-agent tools.

Run standalone:
    python -m backend.mcp_server          # stdio transport (Claude Desktop)
    python -m backend.mcp_server --sse    # SSE transport (remote agents)

Tools exposed:
    generate_speech   — text → WAV or Ogg/Opus audio (voice clone or design)
    clone_voice       — reference audio (base64, or a file path) → new voice profile
    transcribe        — audio (base64, or a file path) → text
    list_voices       — enumerate saved voice profiles
    list_languages    — available TTS languages
    list_personalities — voice personality presets
    check_health      — backend status + active GPU device

Resources exposed:
    voice://{profile_id}  — voice profile metadata
    history://recent      — last 20 generated audio items

Output mode (OMNIVOICE_MCP_OUTPUT_MODE):
    resources — generate_speech returns the WAV as base64 inline (the original
                contract; default)
    files     — it returns a URL to the requested format (and, with a base
                path, a file written there); no audio enters agent context
    both      — both of the above

File inputs (OMNIVOICE_MCP_BASE_PATH):
    One directory that agents may read audio from (transcribe / clone_voice
    `*_path` arguments) and receive files in (files mode). It is the security
    boundary: with no base path configured, path-shaped inputs are refused.
"""
from __future__ import annotations

import argparse
import base64
import json
import logging
import os
import re
import stat
import sys

logger = logging.getLogger("omnivoice.mcp")


def _decode_ref_audio(ref_audio_base64: str) -> "bytes | None":
    """Decoded reference audio, or None when the input isn't valid base64.

    LLM agents frequently prepend a data URI (``data:audio/wav;base64,…``)
    when handing audio to file-upload tools — strip it before decoding so
    that common shape round-trips instead of failing validation."""
    import binascii

    if ref_audio_base64.startswith("data:"):
        ref_audio_base64 = ref_audio_base64.split(",", 1)[-1]
    try:
        return base64.b64decode(ref_audio_base64, validate=True)
    except (binascii.Error, ValueError):
        return None


def _sniff_audio_ext(raw: bytes) -> str:
    """Filename extension matching the audio container's magic bytes.

    The /profiles route stores the reference clip under the uploaded
    filename's extension, and downstream consumers (HTML5 playback of the
    stored ref, ffmpeg pipelines) treat that extension as a format hint — an
    MP3 stored as ``.wav`` can silently fail there. WAV is the documented
    default; MP3/FLAC/OGG/M4A are the other containers the tool invites."""
    if raw.startswith(b"fLaC"):
        return ".flac"
    if raw.startswith(b"ID3") or raw[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return ".mp3"
    if raw.startswith(b"OggS"):
        return ".ogg"
    if raw[4:8] == b"ftyp":
        # ISO-BMFF requires the first box's size at bytes 0-3 and type at 4-7;
        # a leading non-ftyp box (rare, spec-legal) falls through to the .wav
        # default, which downstream decoders sniff by content anyway — the
        # extension is a storage nicety, not a correctness gate (CR, #1198).
        return ".m4a"
    return ".wav"


# ── Output mode + the base path boundary ─────────────────────────────────
# An LLM agent that receives a WAV as base64 pays for every byte in context:
# a 1.4 s clip already brushes per-result token caps, and a paragraph of
# narration blows them outright. The ElevenLabs MCP settled this with an
# OUTPUT_MODE (files / resources / both) and a BASE_PATH that doubles as the
# security boundary for file-shaped inputs; the same two knobs here, named in
# the OMNIVOICE_* family the rest of the server reads.

_OUTPUT_MODES = ("resources", "files", "both")
_MAX_INPUT_BYTES = 200 * 1024 * 1024
_SAFE_AUDIO_ID = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
_SPEECH_FORMATS = ("wav", "ogg", "opus")


def _output_mode() -> str:
    """How generate_speech hands audio back (OMNIVOICE_MCP_OUTPUT_MODE).

    'resources' is the original base64-inline contract and stays the default
    so existing integrations see no change; 'files' returns a URL to the
    render (plus a file under the base path when configured); 'both'
    returns everything. Anything unrecognized falls back to 'resources' with
    a warning rather than failing the tool."""
    mode = os.environ.get("OMNIVOICE_MCP_OUTPUT_MODE", "resources").strip().lower()
    if mode not in _OUTPUT_MODES:
        logger.warning(
            "OMNIVOICE_MCP_OUTPUT_MODE=%r is not one of %s; using 'resources'",
            mode, _OUTPUT_MODES,
        )
        return "resources"
    return mode


def _base_path() -> "str | None":
    """The one directory agents may read audio from and receive files in
    (OMNIVOICE_MCP_BASE_PATH), realpath'd; None when unset."""
    raw = os.environ.get("OMNIVOICE_MCP_BASE_PATH", "").strip()
    if not raw:
        return None
    return os.path.realpath(os.path.expanduser(raw))


def _resolve_under_base(path: str) -> str:
    """Absolute realpath of ``path`` when it lies inside the base path.

    Relative paths resolve against the base; absolute paths must already be
    inside it. Both sides are realpath'd, so a symlink pointing outward cannot
    smuggle a read in. Raises ValueError with an agent-legible reason when no
    base path is configured or the path escapes it."""
    base = _base_path()
    if base is None:
        raise ValueError(
            "OMNIVOICE_MCP_BASE_PATH is not set; file paths are refused until it "
            "names a directory"
        )
    candidate = os.path.realpath(os.path.join(base, os.path.expanduser(path)))
    if not _path_is_under_base(base, candidate):
        raise ValueError(f"{path!r} resolves outside OMNIVOICE_MCP_BASE_PATH")
    return candidate


def _opened_file_is_confined(fd: int, resolved: str, base: str) -> bool:
    """Verify that an opened descriptor still names a file under ``base``."""
    proc_fd = f"/proc/self/fd/{fd}"
    if os.path.exists(proc_fd):
        return _path_is_under_base(base, os.path.realpath(proc_fd))
    try:
        current = os.path.realpath(resolved)
        return _path_is_under_base(base, current) and os.path.samestat(
            os.fstat(fd), os.stat(current, follow_symlinks=False)
        )
    except OSError:
        return False


def _path_is_under_base(base: str, candidate: str) -> bool:
    try:
        common = os.path.commonpath([base, candidate])
    except ValueError:  # different drives on Windows
        return False
    return os.path.normcase(common) == os.path.normcase(base)


def _open_under_base(path: str, flags: int, *, mode: int = 0o600) -> tuple[int, str]:
    """Open ``path`` without following a component replaced after validation."""
    base = _base_path()
    if base is None:
        raise ValueError(
            "OMNIVOICE_MCP_BASE_PATH is not set; file paths are refused until it "
            "names a directory"
        )
    resolved = _resolve_under_base(path)
    relative = os.path.relpath(resolved, base)
    parts = [part for part in relative.split(os.sep) if part not in ("", ".")]
    if not parts or parts[0] == os.pardir:
        raise ValueError(f"{path!r} resolves outside OMNIVOICE_MCP_BASE_PATH")

    no_follow = getattr(os, "O_NOFOLLOW", 0)
    close_on_exec = getattr(os, "O_CLOEXEC", 0)
    binary = getattr(os, "O_BINARY", 0)
    file_flags = flags | no_follow | close_on_exec | binary
    supports_dir_fd = os.open in getattr(os, "supports_dir_fd", ())
    directory_flag = getattr(os, "O_DIRECTORY", 0)

    if supports_dir_fd and directory_flag:
        directory_flags = os.O_RDONLY | directory_flag | no_follow | close_on_exec
        directory_fd = os.open(base, directory_flags)
        try:
            for component in parts[:-1]:
                next_fd = os.open(component, directory_flags, dir_fd=directory_fd)
                os.close(directory_fd)
                directory_fd = next_fd
            fd = os.open(parts[-1], file_flags, mode, dir_fd=directory_fd)
        finally:
            os.close(directory_fd)
    else:
        fd = os.open(resolved, file_flags, mode)

    if not _opened_file_is_confined(fd, resolved, base):
        os.close(fd)
        raise ValueError(f"{path!r} resolves outside OMNIVOICE_MCP_BASE_PATH")
    return fd, resolved


def _read_input_audio(
    audio_base64: "str | None",
    audio_path: "str | None",
    *,
    label: str = "audio_base64",
    too_big: str = "audio exceeds 200 MB limit",
) -> "tuple[bytes | None, str | None]":
    """Audio bytes from exactly one of the two input lanes, or (None, error).

    The base64 lane keeps its data-URI tolerance and 200 MB cap; the path lane
    is honored only inside the base path (the security boundary) and applies
    the same cap to the file's size before reading it."""
    if bool(audio_base64) == bool(audio_path):
        return None, f"pass exactly one of {label} or the matching *_path argument"
    if audio_path:
        try:
            fd, _resolved = _open_under_base(audio_path, os.O_RDONLY)
        except ValueError as e:
            return None, str(e)
        except FileNotFoundError:
            return None, f"no such file under OMNIVOICE_MCP_BASE_PATH: {audio_path!r}"
        except OSError as e:
            return None, f"could not safely read {audio_path!r}: {e}"
        with os.fdopen(fd, "rb") as handle:
            info = os.fstat(handle.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None, f"{audio_path!r} is not a regular file"
            if info.st_size > _MAX_INPUT_BYTES:
                return None, too_big
            raw = handle.read(_MAX_INPUT_BYTES + 1)
        if len(raw) > _MAX_INPUT_BYTES:
            return None, too_big
        if not raw:
            return None, f"{label} is empty"
        return raw, None
    encoded = (
        audio_base64.split(",", 1)[-1]
        if audio_base64.startswith("data:")
        else audio_base64
    )
    max_encoded_bytes = 4 * ((_MAX_INPUT_BYTES + 2) // 3)
    if len(encoded) > max_encoded_bytes:
        return None, too_big
    raw = _decode_ref_audio(audio_base64)
    if raw is None:
        return None, f"{label} is not valid base64"
    if not raw:
        return None, f"{label} is empty"
    if len(raw) > _MAX_INPUT_BYTES:
        return None, too_big
    return raw, None


async def _write_output(audio_id: str, raw: bytes, format: str = "wav") -> str:
    """Write the actual requested container inside the confined base path."""
    if not _SAFE_AUDIO_ID.fullmatch(audio_id):
        raise ValueError("backend returned an invalid X-Audio-Id header")
    if format not in _SPEECH_FORMATS:
        raise ValueError(f"unsupported speech format {format!r}; choose wav, ogg or opus")
    if format != "wav":
        from services.audio_io import encode_ogg_opus
        raw = await encode_ogg_opus(raw)
    base = _base_path()
    os.makedirs(base, exist_ok=True)
    filename = f"{audio_id}.{format}"
    fd, path = _open_under_base(filename, os.O_WRONLY | os.O_CREAT | os.O_EXCL)
    with os.fdopen(fd, "wb") as handle:
        handle.write(raw)
    return path


# Extra seconds a tool waits past the backend's own budget, so the backend's
# error (which says what ran out) reaches the agent instead of an empty
# client-side timeout (#2040).
_BACKEND_GRACE_S = 30.0


def _env_seconds(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        logger.warning("%s=%r is not a number; using %g", name, raw, default)
        return default
    return value if value > 0 else default


def _backend_budget_s(kind: str, text: str = "") -> float | None:
    """The backend's own execution budget for this kind of request, read from
    the environment variables and defaults the backend itself uses.

    The MCP server cannot see which device the backend runs on, so generation
    assumes the larger CPU base. The backend still stops a job at its own
    budget; this only keeps the tool from giving up first.
    """
    if kind == "transcribe":
        # run_transcribe_guarded starts this clock when the job is submitted,
        # so time spent queued in the pool already counts against it.
        return _env_seconds("OMNIVOICE_ASR_TRANSCRIBE_TIMEOUT_S", 300.0)
    if kind == "generate":
        base = max(
            _env_seconds("OMNIVOICE_GENERATE_TIMEOUT_S", 300.0),
            _env_seconds("OMNIVOICE_CPU_GENERATE_TIMEOUT_S", 600.0),
        )
        # As model_manager.generate_timeout_s: +1 s per 40 characters past 1200.
        execution = base + max(0, len(text or "") - 1200) / 40.0
        # A generation first waits in the GPU pool's queue, on its own clock
        # (model_manager.GPU_QUEUE_TIMEOUT_S), before that budget starts.
        return _env_seconds("OMNIVOICE_GPU_QUEUE_TIMEOUT_S", 1800.0) + execution
    return None


def _post_timeout_s(kind: str = "", text: str = "") -> float:
    """Seconds a tool waits on a backend POST.

    An explicit OMNIVOICE_MCP_TIMEOUT_S wins. Otherwise the tool waits for the
    backend's own budget for that request plus a grace period, and never less
    than 120 s. A fixed 120 s used to cut off transcriptions the backend would
    have finished (its ASR budget is 300 s) with an empty error (#2040).
    """
    if os.environ.get("OMNIVOICE_MCP_TIMEOUT_S", "").strip():
        return _env_seconds("OMNIVOICE_MCP_TIMEOUT_S", 120.0)
    budget = _backend_budget_s(kind, text)
    return 120.0 if budget is None else max(120.0, budget + _BACKEND_GRACE_S)


def _maybe_number(value):
    """A response-header number as a number, or the raw text (e.g. '?')."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return value


async def _speech_result(
    audio_id: str, gen_time, duration, raw: bytes, api_base: str, format: str = "wav"
) -> dict:
    """Shape the reply; resource bytes stay WAV, file and URL match format."""
    if format not in _SPEECH_FORMATS:
        raise ValueError(f"unsupported speech format {format!r}; choose wav, ogg or opus")
    if not _SAFE_AUDIO_ID.fullmatch(audio_id):
        raise ValueError("backend returned an invalid X-Audio-Id header")
    mode = _output_mode()
    if format != "wav" and mode == "resources":
        raise ValueError("Ogg/Opus output requires MCP files or both output mode")
    out = {
        "audio_id": audio_id,
        "generation_time_s": gen_time,
        "audio_duration_s": duration,
        "format": format,
        "output_mode": mode,
    }
    if mode in ("files", "both"):
        out["audio_url"] = f"{api_base.rstrip('/')}/audio/{audio_id}.{format}"
        if _base_path() is not None:
            out["output_path"] = await _write_output(audio_id, raw, format)
        else:
            out["note"] = "set OMNIVOICE_MCP_BASE_PATH to also receive the audio as a file"
    if mode in ("resources", "both"):
        out["wav_base64"] = base64.b64encode(raw).decode("ascii")
    return out


# ── Lazy imports — keeps startup fast when not using MCP ────────────────


def _ensure_mcp():
    """Import `mcp` SDK lazily so the rest of the backend doesn't pay
    for the import unless the MCP server is actually started.

    Raises ImportError (never SystemExit — #1156: a sys.exit here escaped
    main.py's best-effort `except Exception` and killed the whole backend
    on startup). The message carries the underlying error because the
    import can fail with the package present — e.g. a broken pywin32
    transitive import on Windows — and "not installed" was a misdiagnosis.
    """
    try:
        from mcp.server.fastmcp import FastMCP  # noqa: F811
        return FastMCP
    except ImportError as e:
        msg = (
            f"MCP SDK import failed ({e}). The `mcp` package ships with the "
            "app environment — the launcher's Clean & Retry (or `uv sync`) "
            "reinstalls it. For a standalone run: pip install 'mcp[cli]'."
        )
        logger.error(msg)
        raise ImportError(msg) from e


def create_mcp_server(app=None):
    """Build and return the FastMCP server instance.

    ``app`` is the backend ASGI app this server is mounted on. When given,
    tool calls reach the API in-process as a loopback caller — independent of
    the bind host/port and never challenged by the share-PIN / API-key gates
    (which a concrete LAN ``OMNIVOICE_BIND_HOST`` would otherwise trigger).
    Standalone runs (no app) call the backend over HTTP.
    """
    FastMCP = _ensure_mcp()
    mcp = FastMCP(
        "VoiceStudio",
        instructions=(
            "AI-agent interface for VoiceStudio — voice cloning, "
            "voice design, and video dubbing in 646 languages."
        ),
    )
    # Serve the Streamable-HTTP transport at the app root so mounting the whole
    # app at "/mcp" on the main FastAPI yields the endpoint at "/mcp". FastMCP's
    # default path is "/mcp", which would double-prefix to "/mcp/mcp" when
    # sub-mounted. Harmless for the standalone CLI run() path.
    try:
        mcp.settings.streamable_http_path = "/"
    except Exception as exc:
        logger.error("MCP transport path configuration failed")
        raise RuntimeError("MCP transport could not be configured.") from exc

    # Extend the MCP SDK's DNS-rebinding allowlist so agents on non-localhost
    # hosts (Docker's host.containers.internal, a LAN IP, a reverse proxy) can
    # reach the /mcp endpoint. The SDK default is localhost-only.
    _mcp_hosts = os.environ.get("OMNIVOICE_MCP_ALLOWED_HOSTS", "")
    if _mcp_hosts.strip():
        hosts = [h.strip() for h in _mcp_hosts.split(",") if h.strip()]
        try:
            mcp.settings.transport_security.allowed_hosts.extend(hosts)
            # Also extend origins for both http and https (browser-based MCP
            # clients behind a proxy send an Origin header — agent clients
            # typically don't, but a reverse proxy may use either scheme).
            origins = [
                f"{scheme}://{h}" for h in hosts for scheme in ("http", "https")
            ]
            mcp.settings.transport_security.allowed_origins.extend(origins)
        except Exception as e:
            logger.warning("OMNIVOICE_MCP_ALLOWED_HOSTS not applied (%s)", e)

    # ── Helpers ─────────────────────────────────────────────────────────

    def _api_base() -> str:
        # Follows the backend's real bind host/port (OMNIVOICE_PORT), not a
        # hard-coded 3900 — Electron moves the port via OMNIVOICE_PORT only.
        # Also the public base of the `audio_url` returned in files mode.
        from services.network_share import backend_self_url
        return backend_self_url()

    def _client(timeout: float):
        import httpx
        # OMNIVOICE_API_URL is an explicit "send tool calls there" override.
        if app is not None and not os.environ.get("OMNIVOICE_API_URL", "").strip():
            return httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app, client=("127.0.0.1", 0)),
                base_url="http://127.0.0.1",
                timeout=timeout,
            )
        from services.network_share import backend_auth_headers
        base = _api_base()
        return httpx.AsyncClient(
            base_url=base, timeout=timeout, headers=backend_auth_headers(base)
        )

    async def _api_get(path: str):
        async with _client(30) as c:
            r = await c.get(path)
            r.raise_for_status()
            return r.json()

    async def _api_post_form(
        path: str, data: dict, files: dict | None = None, *, timeout: float | None = None
    ):
        wait = _post_timeout_s() if timeout is None else timeout
        async with _client(wait) as c:
            r = await c.post(path, data=data, files=files or {})
            r.raise_for_status()
            return r

    # ── Tools ───────────────────────────────────────────────────────────

    def _current_client_id() -> str | None:
        """The X-OmniVoice-Client-Id of the calling MCP client, if any.

        FastMCP exposes the HTTP request via its request context on the
        Streamable-HTTP transport; stdio clients (and any version where the
        accessor differs) simply resolve to None and fall back to the
        global default voice."""
        try:
            req = mcp.get_context().request_context.request
            if req is not None:
                return req.headers.get("x-omnivoice-client-id")
        except Exception:
            pass
        return None

    @mcp.tool()
    async def generate_speech(
        text: str,
        language: str = "Auto",
        profile_id: str | None = None,
        instruct: str | None = None,
        speed: float = 1.0,
        steps: int = 16,
        format: str = "wav",
    ) -> str:
        """Generate speech audio from text.

        Args:
            text: The text to synthesize into speech.
            language: Target language (ISO code or 'Auto'). 646 languages supported.
            profile_id: ID of a saved voice profile to clone. Omit to use this
                agent's bound voice (Settings → MCP), else the global default.
            instruct: Style instruction (e.g. 'whisper', 'excited', 'narrator').
            speed: Speech speed multiplier (0.5–2.0, default 1.0).
            steps: Diffusion steps (8=fast/draft, 16=balanced, 32=quality).
            format: File and URL format: wav (default), ogg or opus. Both
                ogg and opus carry Opus in Ogg; requires files/both mode and ffmpeg.

        Returns:
            JSON with audio_id, generation_time_s, audio_duration_s and the
            audio shaped by OMNIVOICE_MCP_OUTPUT_MODE: base64 WAV data
            ('resources', the default), a URL plus an optional file ('files'),
            or both ('both'). Prefer 'files' for LLM agents.
        """
        if format not in _SPEECH_FORMATS:
            raise ValueError(f"unsupported speech format {format!r}; choose wav, ogg or opus")
        if format != "wav" and _output_mode() == "resources":
            raise ValueError("Ogg/Opus output requires MCP files or both output mode")
        if format != "wav" and _base_path() is not None:
            from services.ffmpeg_utils import find_ffmpeg
            import asyncio
            if not await asyncio.to_thread(find_ffmpeg):
                raise RuntimeError("Ogg/Opus file output requires local ffmpeg; install it or set FFMPEG_PATH")
        # Per-agent voice binding (Wave 2.2): explicit arg wins; otherwise
        # resolve this client's bound profile, then the global default.
        client_id = _current_client_id()
        try:
            from services import mcp_bindings
            resolved = mcp_bindings.resolve_voice(client_id, profile_id)
            profile_id = resolved.get("profile_id")
            mcp_bindings.touch_last_seen(client_id) if client_id else None
        except Exception:
            pass  # binding layer unavailable — use whatever was passed

        form = {
            "text": text,
            "language": language,
            "speed": str(speed),
            "num_step": str(steps),
        }
        if profile_id:
            form["profile_id"] = profile_id
        if instruct:
            form["instruct"] = instruct

        r = await _api_post_form(
            "/generate", data=form, timeout=_post_timeout_s("generate", text)
        )

        audio_id = r.headers.get("X-Audio-Id", "unknown")
        gen_time = _maybe_number(r.headers.get("X-Gen-Time", "?"))
        duration = _maybe_number(r.headers.get("X-Audio-Duration", "?"))
        if format != "wav":
            if not _SAFE_AUDIO_ID.fullmatch(audio_id):
                raise ValueError("backend returned an invalid X-Audio-Id header")
            # The MCP process may run on a different host: only the backend can
            # prove the returned URL is actually encodable. Stream to avoid
            # buffering another full audio copy in the agent process.
            async with _client(300) as c:
                async with c.stream("GET", f"/audio/{audio_id}.{format}") as encoded:
                    encoded.raise_for_status()

        return json.dumps(await _speech_result(
            audio_id, gen_time, duration, r.content, _api_base(), format
        ))

    @mcp.tool()
    async def list_voices() -> str:
        """List all saved voice profiles.

        Returns a JSON array of voice profiles with id, name, type (clone/design),
        and personality.
        """
        profiles = await _api_get("/profiles")
        return str(profiles)

    @mcp.tool()
    async def list_personalities() -> str:
        """List available voice personality presets.

        Returns presets like Narrator, Casual, News Anchor, etc. with their
        instruct text. Use the instruct text with generate_speech.
        """
        presets = await _api_get("/personalities")
        return str(presets)

    @mcp.tool()
    async def list_languages() -> str:
        """List a sample of supported TTS languages.

        VoiceStudio supports 646 languages. This returns the most popular ones
        plus a note about the full count.
        """
        return (
            '{"total":646,"popular":['
            '"en","es","fr","de","it","pt","ru","ja","ko","zh",'
            '"ar","hi","tr","nl","pl","sv","da","fi","no","el"'
            '],"note":"Pass any ISO 639 code or set language=Auto for detection."}'
        )

    @mcp.tool()
    async def transcribe(
        audio_base64: str | None = None,
        audio_path: str | None = None,
        language: str | None = None,
    ) -> str:
        """Transcribe spoken audio to text.

        Pass exactly one of audio_base64 or audio_path.

        Args:
            audio_base64: Base64-encoded audio bytes (wav/mp3/webm/m4a).
            audio_path: Path to an audio file under OMNIVOICE_MCP_BASE_PATH
                (relative to it, or absolute inside it). The base path is the
                security boundary: with none configured, paths are refused.
                Prefer this lane for LLM agents - the audio never enters the
                agent's context.
            language: Optional language hint; omit for auto-detect.

        Returns:
            JSON with the recognized text, language, and duration.
        """
        # 200 MB cap on both lanes — same spirit as voicebox's transcribe
        # gate. Keeps a buggy/hostile agent from posting an unbounded blob.
        raw, err = _read_input_audio(audio_base64, audio_path)
        if err:
            return json.dumps({"error": err})
        data = {}
        if language:
            data["language"] = language
        r = await _api_post_form(
            "/transcribe", data=data,
            files={"audio": (f"audio{_sniff_audio_ext(raw)}", raw,
                             "application/octet-stream")},
            timeout=_post_timeout_s("transcribe"),
        )
        return str(r.json())

    @mcp.tool()
    async def check_health() -> str:
        """Check if the VoiceStudio backend is running and what GPU device is active."""
        info = await _api_get("/health")
        return str(info)

    # ── Resources ───────────────────────────────────────────────────────

    @mcp.resource("voice://{profile_id}")
    async def get_voice(profile_id: str) -> str:
        """Get details of a specific voice profile."""
        profiles = await _api_get("/profiles")
        for p in profiles:
            if p.get("id") == profile_id:
                return str(p)
        return f'{{"error":"Voice profile {profile_id} not found"}}'

    @mcp.resource("history://recent")
    async def get_recent_history() -> str:
        """Get the 20 most recent generation history items."""
        history = await _api_get("/history")
        return str(history[:20])

    @mcp.tool()
    async def clone_voice(
        name: str,
        ref_audio_base64: str | None = None,
        ref_text: str = "",
        instruct: str = "",
        language: str = "Auto",
        ref_audio_path: str | None = None,
    ) -> str:
        """Clone a new voice profile from a reference audio sample.

        The new voice is immediately available for use with generate_speech
        (pass the returned profile_id as the profile_id argument). Pass
        exactly one of ref_audio_base64 or ref_audio_path.

        Args:
            name: A human-friendly name for the cloned voice.
            ref_audio_base64: Base64-encoded audio (WAV, MP3, FLAC, etc.) of
                the reference voice — 5-30 seconds of clean single-speaker
                speech.
            ref_text: Optional transcript of the reference audio (improves
                quality for some engines).
            instruct: Optional style instruction (e.g. 'whisper', 'excited').
            language: Language of the reference audio (ISO code or 'Auto').
            ref_audio_path: Path to the reference audio under
                OMNIVOICE_MCP_BASE_PATH (relative to it, or absolute inside
                it); refused when no base path is configured. Prefer this
                lane for LLM agents - the clip never enters the context.

        Returns:
            JSON with the new profile's id, name, and kind.
        """
        raw, err = _read_input_audio(
            ref_audio_base64, ref_audio_path,
            label="ref_audio_base64", too_big="reference audio exceeds 200 MB limit",
        )
        if err:
            return json.dumps({"error": err})
        import httpx
        try:
            r = await _api_post_form(
                "/profiles",
                data={
                    "name": name,
                    "kind": "clone",
                    "ref_text": ref_text,
                    "instruct": instruct,
                    "language": language,
                },
                files={"ref_audio": (f"ref_audio{_sniff_audio_ext(raw)}", raw,
                                     "application/octet-stream")},
            )
            p = r.json()
        except httpx.HTTPStatusError as exc:
            # Cloning commonly fails validation (duplicate name, audio too
            # short, quality gate) — surface the backend's own detail as the
            # structured error the agent expects, not a framework traceback.
            try:
                detail = exc.response.json().get("detail")
            except ValueError:
                detail = None
            return json.dumps({"error": str(detail or exc.response.text
                                             or f"HTTP {exc.response.status_code}")})
        except (httpx.HTTPError, ValueError) as exc:
            # Transport failures + non-JSON success bodies (proxy error page).
            return json.dumps({"error": f"backend request failed: {exc}"})
        return json.dumps({"profile_id": p["id"], "name": p["name"], "kind": p["kind"]})

    return mcp


class _BareMcpPath:
    """ASGI endpoint for exactly ``/mcp``: re-dispatch as ``/mcp/``.

    A class instance (not a function) so Starlette's ``Route`` treats it as a
    raw ASGI app with no method restriction — GET (SSE), POST and DELETE all
    reach the Streamable-HTTP transport. Re-entering the router keeps the
    mount's own path/root_path handling instead of re-implementing it.
    """

    def __init__(self, router) -> None:
        self._router = router

    async def __call__(self, scope, receive, send) -> None:
        scope = dict(scope)
        scope["path"] = scope["path"] + "/"
        raw = scope.get("raw_path")
        if isinstance(raw, (bytes, bytearray)):
            path, sep, query = bytes(raw).partition(b"?")
            scope["raw_path"] = path + b"/" + sep + query
        await self._router(scope, receive, send)


def mount_mcp(app) -> bool:
    """Best-effort sub-mount of the MCP Streamable-HTTP app at /mcp.

    Returns True on success, False on any failure. Contains SystemExit as
    well as Exception (#1156): an integration dependency written as a CLI
    can call sys.exit, and that must degrade to "/mcp disabled" — never
    take down backend startup (same exit-containment class as the engine
    boundary, #1143).
    """
    try:
        mcp = create_mcp_server(app)
        mcp_app = mcp.streamable_http_app()
        app.state.mcp_session_manager = mcp.session_manager
        app.mount("/mcp", mcp_app)
        # The mount only matches "/mcp/..."; a bare "/mcp" would fall through
        # to the SPA StaticFiles mount at "/" (405 on POST) or, without a
        # built SPA, to a 307 that many MCP clients won't re-POST. Serve the
        # published "/mcp" URL directly, for every method, ahead of "/".
        from starlette.routing import Route
        app.router.routes.append(
            Route("/mcp", endpoint=_BareMcpPath(app.router), include_in_schema=False)
        )
        logger.info("MCP app mounted at /mcp")
        return True
    except (Exception, SystemExit) as err:  # noqa: BLE001
        logger.info("MCP server not mounted (%s); /mcp disabled.", err)
        return False


# ── CLI entrypoint ──────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="VoiceStudio MCP Server")
    parser.add_argument(
        "--sse", action="store_true",
        help="Use SSE transport instead of stdio (for remote agents)",
    )
    parser.add_argument(
        "--port", type=int, default=8765,
        help="Port for SSE transport (default: 8765)",
    )
    args = parser.parse_args()

    # `python -m backend.mcp_server` runs from the repo root, where the
    # backend's own packages (services.*, core.*) are not importable; the
    # embedded mount runs with backend/ on sys.path already.
    _backend_dir = os.path.dirname(os.path.abspath(__file__))
    if _backend_dir not in sys.path:
        sys.path.insert(0, _backend_dir)

    try:
        mcp = create_mcp_server()
    except ImportError as e:
        # Standalone run: a missing SDK is fatal, and a nonzero exit is the
        # right contract for a CLI (the embedded path uses mount_mcp above).
        logger.exception("%s", e)
        sys.exit(1)

    if args.sse:
        logger.info("Starting MCP server on SSE transport, port %d", args.port)
        mcp.run(transport="sse", port=args.port)
    else:
        logger.info("Starting MCP server on stdio transport")
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
