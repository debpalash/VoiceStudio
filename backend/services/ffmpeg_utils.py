import asyncio
import errno
import logging
from core.logging_utils import log_safe
import os
import shutil
import subprocess
import sys

# Leaf module (stdlib-only) — safe to import at module top, unlike
# services.dub_pipeline which imports this module and would cycle.
from services.proc_registry import register_proc, unregister_proc
from core.path_security import UnsafePath, resolve_within

logger = logging.getLogger("omnivoice.api")

# Cap concurrent ffmpeg jobs so macOS posix_spawn can't hit EAGAIN under load.
_FFMPEG_SEMAPHORE: "asyncio.Semaphore | None" = None
_FFMPEG_CONCURRENCY = 2

# ── Background-bed mixing (dub voice over the separated no_vocals stem) ──────
#
# Every dub export mixes the synthesized voice track over the original video's
# separated background (music/ambience). Two fidelity bugs lived in the old
# per-site `amix` strings, and they are exactly what "the background music
# doesn't sound like the original" reports describe:
#
#   1. LEVEL — `amix` NORMALIZES: each input is scaled by weight/sum(weights).
#      The old `weights=0.8 1.2` therefore played the music bed at 40% of its
#      original level (−8 dB) and the voice at 60%. (batch.py was worse still:
#      an explicit volume=0.15 plus amix's ÷2 left the bed at 7.5%.) We keep
#      amix for its duration/dropout semantics but multiply the mix by
#      sum(weights) afterwards, which cancels the normalization exactly — the
#      weights below ARE the absolute gains.
#   2. BANDWIDTH — the voice track is synthesized at 24 kHz and amix
#      negotiates one common rate, so the 44.1/48 kHz bed was silently
#      downsampled to 24 kHz: everything above 12 kHz (cymbals, air,
#      brightness) vanished from the music. Both inputs are now explicitly
#      resampled to 48 kHz before the mix, so the bed keeps its top end.
#
# Bed at −0.9 dB (0.9×) keeps the music essentially at the original level
# while letting dialogue sit just above it; the limiter transparently catches
# the rare summed peak that now can exceed full scale (the old normalization
# made clipping impossible by making everything quiet).
BED_MIX_SAMPLE_RATE = 48000
BED_GAIN = 0.9
VOICE_GAIN = 1.1

# Whether the resolved ffmpeg's amix supports `normalize` (added in 5.x).
# Probed once per process; None = not probed yet.
_AMIX_NORMALIZE: "bool | None" = None


def _amix_supports_normalize() -> bool:
    """True when the resolved ffmpeg's ``amix`` accepts ``normalize=0``.

    Matters because amix's normalization is DYNAMIC: it rescales whenever an
    input ends. A constant post-mix compensation is therefore only exact while
    both streams are active — after the (usually marginally shorter) voice
    stream ends, the bed's internal scale jumps from w/sum to 1.0 and a fixed
    multiply would BOOST the tail music into the limiter. ``normalize=0``
    turns amix into a plain sum, immune to stream-end rescaling. Old system
    ffmpegs (<5) lack the option and would reject the whole graph, so probe
    once and fall back to the compensated form there (its tail quirk is the
    lesser evil next to a failed export).
    """
    global _AMIX_NORMALIZE
    if _AMIX_NORMALIZE is None:
        supported = False
        try:
            ff = find_ffmpeg()
            if ff:
                res = subprocess.run(
                    [ff, "-hide_banner", "-h", "filter=amix"],
                    capture_output=True, timeout=10, check=False,
                )
                supported = b"normalize" in (res.stdout or b"")
        except Exception as e:  # noqa: BLE001 — a probe failure must not break exports
            logger.debug("amix normalize probe failed: %s", e)
        _AMIX_NORMALIZE = supported
    return _AMIX_NORMALIZE


def bed_mix_filter(
    bed_in: str,
    voice_in: str,
    *,
    out: str = "aout",
    duration: str = "longest",
    tail: str = "",
    uniq: str = "",
    bed_gain: float = BED_GAIN,
) -> str:
    """One ffmpeg filter chain mixing `voice_in` over `bed_in` at original level.

    `bed_in`/`voice_in` are filtergraph input labels ("0:a", "1:a", …); `out`
    is the output label (without brackets). `tail` appends extra filters after
    the gain stage (e.g. ",apad=whole_dur=…"). `uniq` disambiguates internal
    labels when several chains share one filtergraph.
    """
    b, v = f"bmb{uniq}", f"bmv{uniq}"
    # Both legs are forced to STEREO before amix. The synthesized voice is
    # mono, and amix negotiates one common layout for all inputs — without
    # this, the negotiation collapsed the stereo music bed to mono (measured
    # on a real dub: L/R correlation 1.000 vs the original's 0.754 — the
    # entire stereo image gone). Upmixing the mono voice duplicates it into
    # both channels (dead center, where dubbed dialogue belongs) so the bed
    # keeps its width.
    stereo = "aformat=channel_layouts=stereo"
    if _amix_supports_normalize():
        # Gains applied per input, amix reduced to a plain sum: levels are
        # exact for the whole timeline, including after either stream ends.
        return (
            f"[{bed_in}]aresample={BED_MIX_SAMPLE_RATE},{stereo},volume={bed_gain:g}[{b}];"
            f"[{voice_in}]aresample={BED_MIX_SAMPLE_RATE},{stereo},volume={VOICE_GAIN:g}[{v}];"
            f"[{b}][{v}]amix=inputs=2:duration={duration}:dropout_transition=2:"
            f"normalize=0,alimiter=level=false:limit=0.98:latency=1{tail}[{out}]"
        )
    # Legacy ffmpeg (<5, no `normalize`): cancel amix's normalization with a
    # compensating multiply. Exact while both streams run; if one ends early
    # the tail is over-boosted into the limiter until the graph ends — a known
    # quirk accepted only on old ffmpeg, where the alternative is no export.
    total = bed_gain + VOICE_GAIN
    return (
        f"[{bed_in}]aresample={BED_MIX_SAMPLE_RATE},{stereo}[{b}];"
        f"[{voice_in}]aresample={BED_MIX_SAMPLE_RATE},{stereo}[{v}];"
        f"[{b}][{v}]amix=inputs=2:duration={duration}:dropout_transition=2:"
        f"weights={bed_gain:g} {VOICE_GAIN:g},volume={total:g},"
        f"alimiter=level=false:limit=0.98:latency=1{tail}[{out}]"
    )


def _get_semaphore() -> asyncio.Semaphore:
    global _FFMPEG_SEMAPHORE
    if _FFMPEG_SEMAPHORE is None:
        _FFMPEG_SEMAPHORE = asyncio.Semaphore(_FFMPEG_CONCURRENCY)
    return _FFMPEG_SEMAPHORE


def windows_tool_candidates(tool: str) -> "list[str]":
    """Well-known Windows install locations for *tool* (ffmpeg/ffprobe).

    Derived from the environment instead of hardcoding ``C:\\`` so machines
    whose Windows/Program Files live on another drive still resolve (the
    non-system-drive class): ``%ProgramFiles%``/``%ProgramW6432%`` for the
    relocatable Program Files, ``%SystemDrive%``+D: for the conventional
    ``<drive>:\\ffmpeg\\bin`` layout. Empty on non-Windows."""
    if os.name != "nt":
        return []
    out: list[str] = []
    drives = {os.environ.get("SystemDrive", "C:"), "C:", "D:"}
    for drive in sorted(drives):
        out.append(f"{drive}\\ffmpeg\\bin\\{tool}.exe")
    pf_dirs = {
        os.environ.get("ProgramFiles", "C:\\Program Files"),
        os.environ.get("ProgramW6432", "C:\\Program Files"),
    }
    for pf in sorted(pf_dirs):
        out.append(os.path.join(pf, "ffmpeg", "bin", f"{tool}.exe"))
    return out


# Candidate paths that exist but won't run (validated once per process).
# Windows users hit this as `[WinError 193] %1 is not a valid Win32
# application` (#360/#361/#362): a corrupt/wrong-arch imageio-ffmpeg
# download or a WindowsApps alias stub passes `os.path.isfile` / `which`
# but explodes at spawn. Probe each candidate with `-version` and fall
# through to the next source instead of returning a time bomb.
_BINARY_OK: dict[str, bool] = {}


def _binary_runs(path: str) -> bool:
    cached = _BINARY_OK.get(path)
    if cached is not None:
        return cached
    try:
        result = subprocess.run(
            [path, "-version"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            timeout=10, check=False,
        )
        ok = result.returncode == 0
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError) as e:
        logger.warning(
            "Rejecting non-runnable ffmpeg/ffprobe candidate %s: %s",
            os.path.basename(str(path)), e,
        )
        ok = False
    _BINARY_OK[path] = ok
    return ok


def find_ffmpeg():
    """Locate an ffmpeg binary.

    Resolution order:
      1. ``FFMPEG_PATH`` env var (set by Tauri when a sidecar is bundled, or
         by the user's Settings → Audio tools override via prefs).
      2. ``imageio-ffmpeg`` pip package (ships a static binary per platform).
      3. VoiceStudio-acquired static bundle (``services.media_tools``) — the
         checksummed build the app downloads itself when nothing else
         resolves; the only bundled tier that also ships ffprobe.
      4. Common system paths / ``PATH``.

    Returns the path string, or ``None`` if nothing found.
    """
    # 1. Env var injected by Tauri host
    env_path = os.environ.get("FFMPEG_PATH")
    if env_path:
        resolved = shutil.which(env_path)
        if resolved and _binary_runs(resolved):
            return resolved
    # 2. imageio-ffmpeg bundled static binary
    try:
        import imageio_ffmpeg
        candidate = imageio_ffmpeg.get_ffmpeg_exe()
        if candidate and os.path.isfile(candidate) and _binary_runs(candidate):
            return candidate
        logger.debug("imageio_ffmpeg binary not usable at %s", candidate)
    except Exception as e:
        logger.debug("imageio_ffmpeg unavailable: %s", e)
    # 3. VoiceStudio-acquired bundled static binary (never downloads here —
    # acquisition is media_tools' background job; this only picks up an
    # already-installed build).
    candidate = _acquired_bundled("ffmpeg")
    if candidate:
        return candidate
    # 4. Well-known system paths + PATH lookup
    common = [
        "/opt/homebrew/bin/ffmpeg",
        "/usr/local/bin/ffmpeg",
        *windows_tool_candidates("ffmpeg"),
        "ffmpeg",
    ]
    for path in common:
        resolved = shutil.which(path)
        if resolved and _binary_runs(resolved):
            return resolved
    logger.warning("ffmpeg not found (or not runnable) in env, imageio, or system PATH")
    return None


def _acquired_bundled(tool: str) -> "str | None":
    """Already-acquired media_tools static binary, validated — or None.

    Lazy import: media_tools imports from this module at its top, so this
    module must only reach back at call time (no cycle).
    """
    try:
        from services.media_tools import bundled_tool_path
        candidate = bundled_tool_path(tool)
        if candidate and _binary_runs(candidate):
            return candidate
    except Exception as e:
        logger.debug("media_tools bundled %s unavailable: %s", tool, e)
    return None


def resolve_ffprobe() -> str | None:
    """Resolve an ffprobe binary path.

    Resolution order (per issue #76 and 01-03-PLAN.md must_haves):
      1. ``OMNIVOICE_FFPROBE_PATH`` env var — the canonical, namespaced path
         injected by Tauri pointing at the bundled sidecar (e.g.
         ``/usr/lib/omnivoice-studio/bin/ffprobe`` on .deb installs).
      2. ``FFPROBE_PATH`` env var — legacy alias kept for backward
         compatibility with older Tauri shells / dev environments; also the
         key Settings → Audio tools persists a user override under.
      3. VoiceStudio-acquired static bundle (``services.media_tools``) —
         imageio-ffmpeg ships no ffprobe, so this is the bundled tier that
         closes the source-install gap.
      4. ``shutil.which("ffprobe")`` — system ``PATH`` fallback.

    Returns the resolved path string, or ``None`` if nothing found. Callers
    that need a hard failure should use :func:`find_ffprobe` instead.
    """
    for env_key in ("OMNIVOICE_FFPROBE_PATH", "FFPROBE_PATH"):
        path = os.environ.get(env_key)
        if not path:
            continue
        # The env var may carry either an absolute path to a file OR a bare
        # command name (legacy). Accept both shapes — file first.
        if os.path.isfile(path) and _binary_runs(path):
            return path
        resolved = shutil.which(path)
        if resolved and _binary_runs(resolved):
            return resolved

    bundled = _acquired_bundled("ffprobe")
    if bundled:
        return bundled

    system_probe = shutil.which("ffprobe")
    if system_probe and _binary_runs(system_probe):
        return system_probe
    return None


def find_ffprobe():
    """Locate an ffprobe binary (legacy wrapper around :func:`resolve_ffprobe`).

    Falls back to deriving the path from ``find_ffmpeg()`` so the
    co-located ffprobe in an ffmpeg-bundle download (e.g. BtbN, evermeet.cx)
    is still picked up when only ffmpeg has been resolved.
    """
    resolved = resolve_ffprobe()
    if resolved:
        return resolved
    try:
        ffmpeg_path = find_ffmpeg()
        if ffmpeg_path:
            candidate = os.path.join(
                os.path.dirname(ffmpeg_path),
                os.path.basename(ffmpeg_path).replace("ffmpeg", "ffprobe"),
            )
            if os.path.isfile(candidate) and _binary_runs(candidate):
                return candidate
    except Exception:
        pass
    return None


def ensure_media_tools_on_path() -> list[str]:
    """Put the resolved ffmpeg/ffprobe on ``PATH`` for third-party code (#1256).

    VoiceStudio's own call sites always resolve an explicit path, so a bundled
    sidecar that was never on ``PATH`` works fine for us. Our dependencies do
    not get that courtesy: a library that shells out to ``ffprobe`` by bare
    name dies with ``FileNotFoundError: [Errno 2] No such file or directory:
    'ffprobe'``. The reporter of #1256 hit that mid-synthesis and was told the
    engine had "stopped with an error VoiceStudio doesn't recognize", on a Mac
    where the app's OWN ffprobe was sitting on disk, resolvable, the whole
    time.

    Prepending the resolved binaries' directories fixes every such dependency
    at once, rather than chasing them one import at a time. Prepended (not
    appended) so the copy we validated wins over a broken system one.

    Returns the directories added. Idempotent, best-effort, never raises.
    """
    added: list[str] = []
    try:
        directories: list[str] = []
        for resolve in (find_ffmpeg, find_ffprobe):
            try:
                path = resolve()
            except Exception:
                continue
            if not path:
                continue
            directory = os.path.dirname(os.path.abspath(path))
            if directory and directory not in directories:
                directories.append(directory)

        current = os.environ.get("PATH", "")
        entries = current.split(os.pathsep) if current else []
        # Case-insensitive comparison on Windows/macOS, where PATH is not
        # case-sensitive and "already present" must not depend on casing.
        normalize = os.path.normcase
        present = {normalize(e) for e in entries if e}
        for directory in directories:
            if normalize(directory) in present:
                continue
            entries.insert(0, directory)
            present.add(normalize(directory))
            added.append(directory)

        if added:
            os.environ["PATH"] = os.pathsep.join(entries)
            # Count, not paths: a user-set FFMPEG_PATH resolves under their home
            # directory, and absolute home paths must not reach the log
            # (#1256 review). find_ffmpeg/find_ffprobe already log their own
            # resolution at debug level when that detail is wanted.
            logger.info(
                "Published %d media-tool director%s on PATH so dependencies can "
                "find ffmpeg/ffprobe (#1256)",
                len(added), "y" if len(added) == 1 else "ies",
            )
    except Exception as e:  # diagnosis must never break the thing it helps
        logger.debug("ensure_media_tools_on_path failed (non-fatal): %s", e)
    return added


async def _spawn_async(cmd, **kwargs):
    """Try asyncio subprocess; fall back to thread-based subprocess on Windows
    where ProactorEventLoop may not be available (e.g. under uvicorn --reload)."""
    try:
        return await asyncio.create_subprocess_exec(*cmd, **kwargs)
    except NotImplementedError:
        logger.debug("asyncio subprocess not supported, falling back to thread-based subprocess")
        return await _spawn_thread_fallback(cmd, **kwargs)


async def _spawn_thread_fallback(cmd, **kwargs):
    """Run a subprocess synchronously in a thread via subprocess.Popen."""
    stdout = kwargs.pop("stdout", asyncio.subprocess.PIPE)
    stderr = kwargs.pop("stderr", asyncio.subprocess.PIPE)
    stdin = kwargs.pop("stdin", None)
    loop = asyncio.get_running_loop()

    def _run():
        return subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE if stdout == asyncio.subprocess.PIPE else stdout,
            stderr=subprocess.PIPE if stderr == asyncio.subprocess.PIPE else stderr,
            stdin=subprocess.PIPE if stdin == asyncio.subprocess.PIPE else stdin,
            **kwargs,  # forward cwd / env / etc. so the fallback matches the async call
        )

    proc = await loop.run_in_executor(None, _run)
    # Wrap the Popen process to match asyncio.subprocess.Process interface
    class _AsyncCompatProc:
        def __init__(self, popen):
            self._popen = popen
            self.returncode = popen.returncode
            self.stdin = popen.stdin
            self.stdout = popen.stdout
            self.stderr = popen.stderr
            self.pid = popen.pid
            # These are plain SYNC pipes (io.BufferedReader), NOT asyncio
            # StreamReaders — so callers must not `await proc.stderr.read()` on
            # this wrapper. `communicate()`/`wait()` below are the only async
            # entry points. run_proc_streaming_stderr checks this flag and
            # degrades to communicate() on the fallback loop instead of awaiting
            # the sync pipe (which raised "a coroutine or an awaitable is
            # required" and crashed the demucs step under uvicorn --reload).
            self.uses_sync_pipes = True

        async def communicate(self, input=None):
            out, err = await loop.run_in_executor(None, self._popen.communicate, input)
            self.returncode = self._popen.returncode
            return out, err

        async def wait(self):
            return await loop.run_in_executor(None, self._popen.wait)

        def kill(self):
            self._popen.kill()

        def terminate(self):
            self._popen.terminate()

    return _AsyncCompatProc(proc)


async def spawn_subprocess(*args, **kwargs):
    """Drop-in replacement for ``asyncio.create_subprocess_exec``.

    Falls back to a thread-based ``subprocess.Popen`` (wrapped to match the
    asyncio Process interface) on event loops without subprocess support —
    notably the Windows ``SelectorEventLoop`` that uvicorn forces under
    ``--reload``/multi-worker (``use_subprocess=True``), where the native call
    raises ``NotImplementedError`` (GH #122). Also inherits the EAGAIN retry.
    On loops that DO support subprocesses (Proactor, posix) the native path is
    used unchanged, so there is no behavior change off the broken loop.
    """
    return await _spawn_with_retry(list(args), **kwargs)


async def _spawn_with_retry(cmd, **kwargs):
    """Spawn a subprocess, retrying briefly on EAGAIN (posix_spawn resource pressure)."""
    delay = 0.1
    last_err = None
    for _ in range(5):
        try:
            return await _spawn_async(cmd, **kwargs)
        except BlockingIOError as e:
            last_err = e
            if e.errno != errno.EAGAIN:
                raise
            await asyncio.sleep(delay)
            delay *= 2
        except OSError as e:
            if e.errno == errno.EAGAIN:
                last_err = e
                await asyncio.sleep(delay)
                delay *= 2
                continue
            raise
        except Exception:
            raise
    raise last_err if last_err else RuntimeError("spawn failed")


def _atempo_chain(ratio: float) -> str:
    """Build an `atempo=…,atempo=…` filter chain for arbitrary ratios.

    ffmpeg's atempo filter is limited to [0.5, 2.0] per stage. Chaining
    multiple stages multiplies the effective ratio while keeping each
    individual stage inside the well-behaved range. Pitch is preserved
    (WSOLA-style time-domain stretching). ratio > 1 speeds up, < 1
    slows down.
    """
    stages: list[str] = []
    remaining = ratio
    while remaining > 2.0:
        stages.append("atempo=2.0")
        remaining /= 2.0
    while remaining < 0.5:
        stages.append("atempo=0.5")
        remaining /= 0.5
    stages.append(f"atempo={remaining:.6f}")
    return ",".join(stages)


async def _pitch_preserving_stretch(wav, target_samples: int, sr: int):
    """Time-stretch a (1, samples) tensor to `target_samples` while
    preserving pitch, by piping the audio through `ffmpeg atempo`.

    Async so it never blocks the event loop: it's awaited from the dub
    generate `_stream` generator, and each ffmpeg call is ~50-100 ms — a
    synchronous ``subprocess.run`` here froze health-checks / SSE / every
    concurrent request for the whole multi-segment job.

    Returns a (1, target_samples) tensor on the same device as input.
    Raises RuntimeError when ffmpeg fails — callers should fall back to
    naive linear interpolation, accepting the pitch shift, to ensure the
    output isn't silent.
    """
    # Lazy imports keep this module importable in torch-free contexts
    # (setup scripts, smoke probes) — only the stretch path needs them.
    import numpy as np
    import torch

    wl = int(wav.shape[-1])
    if target_samples <= 0 or wl == target_samples:
        return wav
    ratio = wl / target_samples
    filter_str = _atempo_chain(ratio)

    # Mono float32 via stdin → ffmpeg → stdout. One subprocess per segment,
    # run off the event loop so concurrent requests stay responsive.
    arr = wav.detach().cpu().to(torch.float32).numpy().reshape(-1).astype(np.float32, copy=False)
    proc = await spawn_subprocess(
        find_ffmpeg(), "-hide_banner", "-loglevel", "error", "-y",
        "-f", "f32le", "-ar", str(sr), "-ac", "1", "-i", "pipe:0",
        "-af", filter_str,
        "-f", "f32le", "-ar", str(sr), "-ac", "1", "pipe:1",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate(input=arr.tobytes())
    if proc.returncode != 0 or not stdout:
        raise RuntimeError(
            (stderr.decode(errors="replace") or "atempo failed")[:200]
        )
    out_arr = np.frombuffer(stdout, dtype=np.float32)
    # atempo rarely lands exactly on the integer sample count, so
    # pad/trim to the requested slot length.
    if len(out_arr) < target_samples:
        pad = np.zeros(target_samples - len(out_arr), dtype=np.float32)
        out_arr = np.concatenate([out_arr, pad])
    elif len(out_arr) > target_samples:
        out_arr = out_arr[:target_samples]
    return torch.from_numpy(out_arr.copy()).unsqueeze(0).to(wav.device)


async def probe_duration(path: str, *, allowed_root: str) -> float | None:
    """Return a media file's duration in seconds via ffprobe, or None.

    Used by the Smart Fit pipeline to sanity-check source/track lengths
    without loading the media. Never raises — probing is best-effort.
    """
    ffprobe = find_ffprobe()
    try:
        media_path = resolve_within(allowed_root, path)
    except UnsafePath:
        return None
    if not ffprobe or not media_path.is_file():
        return None
    try:
        proc = await spawn_subprocess(
            ffprobe, "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            str(media_path),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        return float(stdout.decode().strip())
    except Exception as e:
        logger.debug("probe_duration failed for %s: %s", log_safe(os.path.basename(str(path))), log_safe(e))
        return None


async def probe_frame_rates(path: str) -> "tuple[str, str] | None":
    """Return (r_frame_rate, avg_frame_rate) strings for the first video
    stream (e.g. ``("30000/1001", "2997/100")``), or None on any failure.

    A mismatch between the two is the practical VFR signature — used by the
    Smart Fit retime pipeline to decide whether to normalise with ``fps=``
    before trim/setpts. Never raises — probing is best-effort.
    """
    ffprobe = find_ffprobe()
    if not ffprobe or not os.path.isfile(path):
        return None
    try:
        proc = await spawn_subprocess(
            ffprobe, "-v", "error",
            "-select_streams", "v:0",
            "-show_entries", "stream=r_frame_rate,avg_frame_rate",
            "-of", "csv=p=0",
            path,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return None
        parts = stdout.decode().strip().split(",")
        if len(parts) < 2:
            return None
        return parts[0].strip(), parts[1].strip()
    except Exception as e:
        logger.debug("probe_frame_rates failed for %s: %s", log_safe(os.path.basename(str(path))), log_safe(e))
        return None


def has_audio_stream(path: str) -> "bool | None":
    """Whether a media file carries at least one audio stream.

    ``True``/``False`` only when a probe actually read the container; ``None``
    when that could not be determined (no ffprobe/ffmpeg, unreadable or
    unrecognized file) — callers then let the real decode report its own
    error rather than block a file on a failed probe. Never raises. Blocking;
    call it from a worker thread on async paths.
    """
    try:
        return _probe_audio_stream(path)
    except Exception as e:  # noqa: BLE001 — a probe must not replace the real error
        logger.debug("audio-stream probe failed: %s", log_safe(e))
        return None


def _probe_audio_stream(path: str) -> "bool | None":
    if not path or not os.path.isfile(path):
        return None
    ffprobe = find_ffprobe()
    if ffprobe:
        try:
            proc = subprocess.run(
                [ffprobe, "-v", "error", "-select_streams", "a",
                 "-show_entries", "stream=index", "-of", "csv=p=0", path],
                capture_output=True, timeout=60, check=False,
            )
            if proc.returncode == 0:
                return bool(proc.stdout.strip())
            return None
        except (OSError, subprocess.SubprocessError) as e:
            logger.debug("ffprobe audio-stream probe failed: %s", log_safe(e))
    ffmpeg = find_ffmpeg()
    if not ffmpeg:
        return None
    # No ffprobe: `ffmpeg -i` lists the input's streams on stderr (and exits 1
    # for want of an output), which is enough to tell audio from no audio.
    try:
        proc = subprocess.run(
            [ffmpeg, "-hide_banner", "-nostdin", "-i", path],
            capture_output=True, timeout=60, check=False,
        )
    except (OSError, subprocess.SubprocessError) as e:
        logger.debug("ffmpeg audio-stream probe failed: %s", log_safe(e))
        return None
    listing = proc.stderr.decode("utf-8", errors="replace")
    streams = [line for line in listing.splitlines() if line.strip().startswith("Stream #")]
    if not streams:
        return None
    return any(": Audio:" in line for line in streams)


def require_audio_stream(path: str) -> None:
    """Raise :class:`core.failure.NoAudioTrackError` when ``path`` has no audio.

    Only a positive "no audio stream" answer raises; an undetermined probe
    passes so the decode that follows reports its own failure.
    """
    from core.failure import NoAudioTrackError

    if has_audio_stream(path) is False:
        logger.info(
            "Refusing %s: the file has no audio stream",
            log_safe(os.path.basename(str(path))),
        )
        raise NoAudioTrackError()


def raise_for_audio_extract_failure(stderr, path: str) -> None:
    """After a failed audio decode, raise ``NoAudioTrackError`` when the cause
    was a missing audio stream (ffmpeg's own wording, or a positive probe).

    Returns normally for every other failure so the caller keeps its own
    diagnosis; the raw stderr stays in the log, never in the user message.
    """
    from core.failure import NO_AUDIO_TRACK_MESSAGE, NoAudioTrackError, is_no_audio_stream_stderr

    # An engine may already have raised NoAudioTrackError (via the ASR decoder's
    # stderr check) and the caller passes its text back here: keep that answer
    # even when the probe cannot run.
    already = NO_AUDIO_TRACK_MESSAGE in (stderr if isinstance(stderr, str) else "")
    if already or is_no_audio_stream_stderr(stderr) or has_audio_stream(path) is False:
        text = stderr.decode("utf-8", errors="replace") if isinstance(stderr, bytes) else str(stderr or "")
        logger.info(
            "Audio decode of %s failed because it has no audio stream: %s",
            log_safe(os.path.basename(str(path))), log_safe(text[-500:]),
        )
        raise NoAudioTrackError()


# Windows CreateProcess rejects command lines over 32,767 chars with
# `[WinError 206] The filename or extension is too long`. The dub-export mux
# argv scales with track/segment count (per-track -i/-map/-metadata plus the
# bed-mix/apad -filter_complex graph), so a big multi-language export can hit
# it (#1152). Externalize below this threshold — comfortably under the hard
# limit so the remaining argv always fits.
_WIN_ARGV_SOFT_LIMIT = 30_000


def externalize_long_filter_complex(cmd, limit=_WIN_ARGV_SOFT_LIMIT, tmp_dir=None):
    """If ``cmd``'s total length exceeds ``limit`` and it carries a
    -filter_complex graph, move the graph into a temp file and switch the
    flag to -filter_complex_script (identical semantics, reads the graph
    from a file). Returns ``(cmd, script_path)`` — script_path is None when
    nothing changed; the caller deletes it after the run (#1152).
    """
    total = sum(len(str(a)) + 1 for a in cmd)
    if total <= limit or "-filter_complex" not in cmd:
        return cmd, None
    idx = cmd.index("-filter_complex")
    if idx + 1 >= len(cmd):
        return cmd, None
    import tempfile

    fd, script_path = tempfile.mkstemp(
        suffix=".ffgraph", prefix="omnivoice_filter_", dir=tmp_dir
    )
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(str(cmd[idx + 1]))
    out = list(cmd)
    out[idx : idx + 2] = ["-filter_complex_script", script_path]
    logger.info(
        "ffmpeg argv was %d chars — moved the %d-char filter graph to %s "
        "to stay under the Windows command-line limit (#1152)",
        total, len(str(cmd[idx + 1])), script_path,
    )
    return out, script_path


def explain_ffmpeg_failure(e, what, cmd=None):
    """Turn an export-time ffmpeg failure into an honest, actionable message.

    #1152: a spawn-time `[WinError 206]` used to be concatenated with
    "Verify ffmpeg is installed…" — the user was told their (short) filename
    was too long AND that a working ffmpeg might be missing. Distinguish the
    three real failure modes; never give one mode another mode's advice.
    """
    if isinstance(e, OSError):
        too_long = (
            getattr(e, "winerror", None) == 206
            or e.errno in (errno.ENAMETOOLONG, getattr(errno, "E2BIG", None))
            or "too long" in str(e).lower()
        )
        if too_long:
            size = f" ({sum(len(str(a)) + 1 for a in cmd)} chars)" if cmd else ""
            return (
                f"Couldn't {what}: the assembled ffmpeg command line{size} exceeded the "
                "Windows 32,767-character limit — this happens on exports "
                "with very many tracks/segments, not because of your file's name. "
                "Try exporting fewer languages per file, and please report this with "
                "the backend log so we can shrink the command further."
            )
        return (
            f"Couldn't {what}: ffmpeg could not be launched ({e}). Verify ffmpeg is "
            "installed and runnable (`ffmpeg -version`), or set FFMPEG_PATH to a "
            "working binary."
        )
    return f"Couldn't {what}: ffmpeg reported an error: {e}"


async def run_ffmpeg(cmd, timeout: float = 1800.0, capture: bool = True,
                     job_id: "str | None" = None):
    """Run an ffmpeg subprocess with concurrency cap, timeout, and proper cleanup.

    Returns (returncode, stdout_bytes, stderr_bytes). Raises asyncio.TimeoutError
    on hard timeout (after killing + reaping the process).

    ``job_id`` (optional) registers the process with the dub pipeline's
    process tracker (``services.proc_registry``) so ``/dub/abort`` can kill
    long export encodes (used by the Smart Fit batched retime).

    Path-injection note: every filesystem path placed in ``cmd`` by callers
    is realpath-normalised and containment-checked against its workspace
    root (e.g. DUB_DIR) at the call site before the argv is assembled —
    see api.routers.dub_export and services.video_retime.
    """
    stdout = asyncio.subprocess.PIPE if capture else asyncio.subprocess.DEVNULL
    stderr = asyncio.subprocess.PIPE
    # #1152: on Windows an oversized argv (multi-track mux filter graphs)
    # fails CreateProcess with WinError 206 before ffmpeg even starts —
    # move a long -filter_complex into a script file first.
    script_path = None
    if sys.platform == "win32":
        cmd, script_path = externalize_long_filter_complex(cmd)
    try:
        async with _get_semaphore():
            for attempt in range(2):
                proc = await _spawn_with_retry(cmd, stdout=stdout, stderr=stderr)
                if job_id:
                    try:
                        register_proc(job_id, proc)
                    except Exception as e:
                        # Newline-strip the id inline — it can originate from a path
                        # param, and the log stream must stay one-event-per-line.
                        logger.debug("register_proc failed for %s: %s",
                                     job_id.replace("\n", " ").replace("\r", " "), e)
                try:
                    try:
                        out, err = await asyncio.wait_for(proc.communicate(), timeout=timeout)
                    except asyncio.TimeoutError:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            # It exited between the timeout check and kill.
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            # Reaping is bounded; preserve the original error.
                            pass
                        raise
                    if (
                        attempt == 0 and proc.returncode
                        and "-filter_complex_script" in cmd
                        and b"Unrecognized option 'filter_complex_script'" in (err or b"")
                    ):
                        # New FFmpeg builds removed the legacy spelling. Parsing
                        # failed before any processing; retry once with the modern
                        # file-argument syntax, retaining the short Windows argv.
                        cmd = ["-/filter_complex" if arg == "-filter_complex_script" else arg for arg in cmd]
                        continue
                    return proc.returncode, out, err
                finally:
                    if job_id:
                        try:
                            unregister_proc(job_id, proc)
                        except Exception as e:
                            logger.debug("unregister_proc failed for %s: %s",
                                         job_id.replace("\n", " ").replace("\r", " "), e)
                    # Guarantee reaping — prevents zombie pileup under timeouts or errors.
                    if proc.returncode is None:
                        try:
                            proc.kill()
                        except ProcessLookupError:
                            # It exited between the timeout check and kill.
                            pass
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=5.0)
                        except asyncio.TimeoutError:
                            # Reaping is bounded; preserve the original error.
                            pass
    finally:
        if script_path:
            try:
                os.remove(script_path)
            except OSError:
                pass
