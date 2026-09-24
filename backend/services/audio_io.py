"""Single audited audio-write path for VoiceStudio — closes BUG-01 / issue #48.

All in-tree audio-write call sites in ``backend/api/routers/`` converge on
the helpers in this module:

* ``_safe_torchaudio_save`` — wraps ``torchaudio.save``. Defends against the
  four documented failure modes that produce silently-corrupt WAVs:

      1. CUDA / MPS tensor handed to a backend that can only serialize CPU
         tensors → header looks valid, data chunk is empty.
      2. Non-contiguous tensor (after ``torch.cat`` of sliced segments) →
         the soundfile backend reads bytes in stride order, the file ends
         up containing interleaved garbage that decodes as noise.
      3. Out-of-range float values (``apply_mastering`` produces transient
         peaks > 1.0 on dynamic input) → TorchCodec 2.9+ clamps to int16
         silently, low-volume tracks become silence after clipping.
      4. Non-float32 dtype (float64 from numpy round-trips, int16 from a
         previous decode) → TorchCodec 2.9+ requires float32-in-[-1, 1]
         and the soundfile backend's dtype handling differs from sox's,
         producing inaudible output on some platforms.

  We also pass ``encoding`` and ``bits_per_sample`` *explicitly* so that
  torchaudio's backend auto-selection (sox → soundfile → TorchCodec in
  2.9+) cannot silently change the on-disk format between versions.

* ``_safe_soundfile_write`` — sibling helper for the one in-tree
  ``sf.write`` site (``dub_core.py``). soundfile's API surface differs
  from torchaudio's (numpy array, ``subtype`` instead of ``encoding`` +
  ``bits_per_sample``) so it gets its own entry point with the same
  sanity checks (dtype / contiguity / shape / range).

* ``atomic_save_wav`` — pre-existing P0 helper (commit fb52140). Writes
  to a sibling temp file in the same directory and ``os.replace()`` into
  place so the target either holds a complete WAV or its previous
  contents — never a truncated one. ``atomic_save_wav`` now delegates
  the actual encode to ``_safe_torchaudio_save`` so the atomicity and
  correctness guarantees compose: every byte that ever lands at the
  target path was produced by the audited helper.

A regression-grep gate in ``tests/backend/test_dub_pipeline_wav.py``
asserts that ``backend/api/routers/`` contains zero direct
``torchaudio.save`` / ``soundfile.write`` / ``sf.write`` calls. Future
code that adds an audio write must go through one of the helpers in
this module.

Closes #48 / BUG-01.
"""
from __future__ import annotations

import io
import logging

from core.render_trace import timed as _render_timed
import os
import shutil
import tempfile
from typing import Any, BinaryIO, Union

import numpy as np
import torch
import torchaudio

logger = logging.getLogger("omnivoice.audio_io")

# A WAV destination is either a filesystem path or a binary stream
# (``io.BytesIO`` for in-memory responses). ``torchaudio.save`` accepts
# both; we forward whichever the caller hands us.
PathOrBuf = Union[str, "os.PathLike[str]", BinaryIO, io.IOBase]

# Opus is carried in Ogg for both .opus and .ogg filenames.
OPUS_CODEC_ARGS = ["-c:a", "libopus", "-b:a", "64k"]
OPUS_SAMPLE_RATE = 48000


async def encode_ogg_opus(wav: bytes | str | os.PathLike[str]) -> bytes:
    """Transcode a WAV render or saved WAV to Ogg/Opus; never return WAV on error."""
    import asyncio
    from core.failure import strip_ffmpeg_banner
    from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg

    ffmpeg = await asyncio.to_thread(find_ffmpeg)
    if not ffmpeg:
        raise RuntimeError(
            "Ogg/Opus output requires ffmpeg. Install it (Settings → Audio tools) "
            "or set FFMPEG_PATH."
        )
    temporary = None
    try:
        if isinstance(wav, bytes):
            fd, temporary = tempfile.mkstemp(suffix=".wav")
            with os.fdopen(fd, "wb") as handle:
                handle.write(wav)
            src = temporary
        else:
            src = os.fspath(wav)
        cmd = [
            ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin",
            "-i", src, "-ar", str(OPUS_SAMPLE_RATE),
            *OPUS_CODEC_ARGS, "-f", "ogg", "pipe:1",
        ]
        rc, out, err = await run_ffmpeg(cmd, timeout=300.0)
    finally:
        if temporary is not None:
            os.unlink(temporary)
    if rc != 0 or not out:
        detail = strip_ffmpeg_banner((err or b"").decode("utf-8", "replace")).strip()[-300:]
        raise RuntimeError(f"Ogg/Opus encoding failed: {detail or f'ffmpeg exit {rc}'}")
    return out


def _ensure_audio_parent(path_or_buf: PathOrBuf) -> None:
    """Recover app output folders removed after backend initialization."""
    if isinstance(path_or_buf, (str, os.PathLike)):
        os.makedirs(os.path.dirname(os.path.abspath(path_or_buf)), exist_ok=True)


@_render_timed('save')
def _safe_torchaudio_save(
    path_or_buf: PathOrBuf,
    tensor: torch.Tensor,
    sample_rate: int,
    *,
    format: str = "wav",
    bits_per_sample: int = 16,
) -> None:
    """Single audited torchaudio.save wrapper. Closes BUG-01 / issue #48.

    The caller hands us a tensor that may have come from a GPU model, may
    have been concatenated from non-contiguous slices, may carry transient
    peaks above 1.0 from upstream mastering, and may not even be float32.
    We normalize all of those before delegating to ``torchaudio.save`` so
    the on-disk WAV always has a valid header and audible samples.

    Args:
        path_or_buf: Filesystem path or binary stream. ``io.BytesIO``
            works for in-memory response bodies.
        tensor: Audio. Accepts ``(samples,)`` (1D, mono) or
            ``(channels, samples)`` (2D). Any device, any dtype.
        sample_rate: WAV sample rate in Hz.
        format: Container format. ``"wav"`` (default), ``"flac"``,
            ``"mp3"``, or ``"ogg"`` (passed through to torchaudio).
        bits_per_sample: 16 (default, ``PCM_S``) or 32 (``PCM_F``).
            Ignored for non-WAV formats where the codec controls the
            sample width.

    Raises:
        ValueError: if the tensor is empty (``numel() == 0``). A
            zero-length WAV would decode silently as "no error, no
            audio" — exactly the failure mode #48 was about, so we
            refuse to produce it.
    """
    if not torch.is_tensor(tensor):
        raise TypeError(
            f"_safe_torchaudio_save expects a torch.Tensor, got {type(tensor).__name__}"
        )
    if tensor.numel() == 0:
        raise ValueError(
            "_safe_torchaudio_save refuses to write an empty audio tensor — "
            "a zero-length WAV decodes silently as 'valid but empty', which "
            "is the silent-corruption mode #48 was about."
        )

    # ── Failure mode 1: CUDA / MPS tensor. The soundfile backend cannot
    # serialize a non-CPU tensor; older torchaudio versions raised, newer
    # ones silently fall back to a zero-filled CPU copy.
    if tensor.device.type != "cpu":
        tensor = tensor.cpu()

    # Integer PCM is full-scale, not normalized: casting int16 without
    # scaling clips nearly every sample and libvorbis rejects s16 input.
    if tensor.dtype in (torch.int16, torch.int32):
        scale = 32768.0 if tensor.dtype == torch.int16 else 2147483648.0
        tensor = tensor.to(torch.float32).div_(scale)
    elif tensor.dtype != torch.float32:
        tensor = tensor.to(torch.float32)

    # ── Failure mode 3: out-of-range values. apply_mastering produces
    # transient peaks > 1.0 on dynamic input; the soundfile backend
    # wraps these around (int16 overflow) on some platforms instead of
    # clipping, producing audible pops.
    tensor = tensor.clamp(-1.0, 1.0)

    # Normalize shape to (channels, samples). torchaudio.save accepts
    # both 1D and 2D but the soundfile backend complains on 1D.
    if tensor.ndim == 1:
        tensor = tensor.unsqueeze(0)
    elif tensor.ndim != 2:
        raise ValueError(
            f"_safe_torchaudio_save expects 1D or 2D tensor, got shape {tuple(tensor.shape)}"
        )

    # ── Failure mode 2: non-contiguous. After torch.cat() of sliced
    # segments (the dub_generate.py:390 / batch.py:341 pattern) the
    # result is often non-contiguous; the soundfile backend reads bytes
    # in stride order and writes garbage.
    if not tensor.is_contiguous():
        tensor = tensor.contiguous()

    # Explicit encoding + bits_per_sample defends against torchaudio
    # backend drift. As of 2.9 the default backend selection went
    # sox → soundfile → TorchCodec; with no encoding kwarg the on-disk
    # format depends on which backend was picked at import time. Pass
    # explicit values so the file is bit-identical across versions.
    encoding = "PCM_F" if bits_per_sample == 32 else "PCM_S"

    fmt = (format or "wav").lower()
    try:
        _ensure_audio_parent(path_or_buf)
        if fmt == "wav":
            torchaudio.save(
                path_or_buf,
                tensor,
                sample_rate,
                format=fmt,
                encoding=encoding,
                bits_per_sample=bits_per_sample,
            )
        elif fmt == "ogg":
            # libvorbis only accepts float planar samples, not PCM_S (s16).
            torchaudio.save(path_or_buf, tensor, sample_rate, format=fmt)
        else:
            # FLAC accepts encoding + bits_per_sample; mp3 ignores
            # them on newer torchaudio but older versions raise. Try
            # with kwargs first, then without them for compatibility.
            try:
                torchaudio.save(
                    path_or_buf,
                    tensor,
                    sample_rate,
                    format=fmt,
                    encoding=encoding,
                    bits_per_sample=bits_per_sample,
                )
            except (TypeError, RuntimeError, ValueError) as e:
                # If the buffer was partially written before the error,
                # rewind it so the retry starts at byte 0. (Path inputs
                # are overwritten by torchaudio.save.)
                if hasattr(path_or_buf, "seek") and hasattr(path_or_buf, "truncate"):
                    try:
                        path_or_buf.seek(0)
                        path_or_buf.truncate(0)
                    except (OSError, io.UnsupportedOperation):
                        pass
                logger.debug(
                    "torchaudio.save(format=%s) rejected encoding kwargs (%s), "
                    "retrying without explicit encoding",
                    fmt, e,
                )
                torchaudio.save(path_or_buf, tensor, sample_rate, format=fmt)
    except (ImportError, RuntimeError) as e:
        if isinstance(e, RuntimeError) and "could not load libtorchcodec" not in str(e).lower():
            raise _describe_write_failure(e, path_or_buf) from e
        # torchaudio >= 2.9 routes save() through TorchCodec, which needs
        # FFmpeg *shared libraries* on the system. Where those are absent the
        # write raises ImportError and every generation fails. #1931 guarded
        # set_audio_backend() against that torchaudio but left save() itself
        # unprotected; arm64 CUDA hosts reach it unavoidably, since torch
        # 2.8.0 publishes no aarch64 wheel. soundfile is already a locked
        # dependency and the tensor is normalized by this point, so hand it to
        # the audited sibling helper rather than failing the request.
        logger.warning(
            "torchaudio.save needs TorchCodec (%s); writing via soundfile", e
        )
        if hasattr(path_or_buf, "seek") and hasattr(path_or_buf, "truncate"):
            try:
                path_or_buf.seek(0)
                path_or_buf.truncate(0)
            except (OSError, io.UnsupportedOperation):
                pass  # Non-seekable streams cannot be rewound; preserve fallback behavior.
        _subtype = {
            "wav": "FLOAT" if bits_per_sample == 32 else "PCM_16",
            "flac": "PCM_16",
            "ogg": "VORBIS",
            "mp3": "MPEG_LAYER_III",
        }.get(fmt, "PCM_16")
        try:
            _safe_soundfile_write(
                path_or_buf,
                tensor.transpose(0, 1).contiguous().numpy(),
                sample_rate,
                subtype=_subtype,
                format=fmt.upper(),
            )
        except Exception as e2:
            raise _describe_write_failure(e2, path_or_buf) from e2
    except Exception as e:
        # #1221: libsndfile reports OS-level write failures as a bare
        # "LibsndfileError: System error." — no path, no errno, nothing the
        # user can act on, and it fell through generation.py's classifier to
        # "an error VoiceStudio doesn't recognize". Name the target and what we
        # can observe about it (exists / writable / free space) so the message
        # points at the actual problem: a full disk, a read-only or
        # antivirus-locked output folder, or a removed drive.
        raise _describe_write_failure(e, path_or_buf) from e


#: Stable, language-independent marker prefixed onto every enriched audio-write
#: failure. ``core.failure.classify`` matches on THIS rather than on generic
#: wording like "error opening", which also appears when a model, archive or
#: config file fails to open and would hand those failures the audio remedy.
AUDIO_WRITE_FAILED_MARKER = "Writing the audio file failed"


def _describe_write_failure(e: Exception, path_or_buf: PathOrBuf) -> Exception:
    """``e`` re-raised as a RuntimeError that names the write target, or ``e``
    itself when there is nothing to add.

    The type is deliberately NOT preserved: ``LibsndfileError.__init__`` takes
    an integer libsndfile code, so ``type(e)(message)`` builds an exception
    whose ``str()`` raises. Every caller of ``_safe_torchaudio_save`` catches
    broadly, and the original stays reachable as ``__cause__``.

    Best-effort — a failure to diagnose must never replace the real error."""
    try:
        if not isinstance(path_or_buf, (str, os.PathLike)):
            return e  # in-memory buffer: nothing to inspect
        path = os.fspath(path_or_buf)
        if getattr(e, "filename", None) or path in str(e):
            return e  # already self-describing
        directory = os.path.dirname(os.path.abspath(path)) or "."
        facts = []
        if not os.path.isdir(directory):
            facts.append("the folder does not exist")
        else:
            if not os.access(directory, os.W_OK):
                facts.append("the folder is not writable")
            try:
                free_mb = shutil.disk_usage(directory).free / (1024 ** 2)
                facts.append(f"{free_mb:,.0f} MB free on its drive")
            except OSError:
                facts.append("free space could not be read")
        return RuntimeError(
            f"{AUDIO_WRITE_FAILED_MARKER}: {type(e).__name__}: {e} — target "
            f"{path} ({'; '.join(facts)}). An audio write failing at the OS "
            f"level is usually a full drive, a read-only or removed folder, or "
            f"antivirus/OneDrive locking the file; add a VoiceStudio exclusion "
            f"if you use one."
        )
    except Exception:
        return e


def _safe_soundfile_write(
    path: PathOrBuf,
    samples: np.ndarray,
    sample_rate: int,
    *,
    subtype: str = "PCM_16",
    format: str | None = None,
) -> None:
    """Sibling helper for the one in-tree ``sf.write`` site.

    ``soundfile`` is a different library than ``torchaudio`` — numpy
    arrays instead of tensors, ``subtype`` instead of
    ``encoding`` + ``bits_per_sample`` — so it gets its own entry point.
    The correctness invariants are the same: contiguous, finite, in
    range, non-empty.

    Args:
        path: Filesystem path or file-like object.
        samples: 1D ``(samples,)`` or 2D ``(samples, channels)`` numpy
            array — soundfile's native shape, opposite of torchaudio's.
        sample_rate: WAV sample rate in Hz.
        subtype: Soundfile subtype string. ``"PCM_16"`` (default) for
            standard 16-bit PCM WAV; ``"PCM_24"``, ``"FLOAT"`` etc.
            also work.
        format: Container format (``"WAV"``, ``"FLAC"``, ``"OGG"``,
            ``"MP3"``). ``None`` lets soundfile infer it from the path's
            extension — which it cannot do for a file-like object, so
            callers passing a buffer must name it.

    Raises:
        ValueError: if the array is empty.
    """
    # Import here so this module doesn't fail to import when soundfile
    # is somehow absent (it's a transitive dep but we don't want a hard
    # import-time coupling).
    import soundfile as sf

    if not isinstance(samples, np.ndarray):
        # Accept memoryview / list / torch tensor inputs by coercing.
        samples = np.asarray(samples)

    if samples.size == 0:
        raise ValueError(
            "_safe_soundfile_write refuses to write an empty array — "
            "a zero-length WAV is exactly the #48 silent-corruption mode."
        )

    # Coerce to a soundfile-friendly dtype. soundfile accepts
    # float32 / float64 / int16 / int32; we normalize anything else to
    # float32 so the clamp below is well-defined.
    if samples.dtype not in (np.float32, np.float64, np.int16, np.int32):
        samples = samples.astype(np.float32)

    # Out-of-range protection for float inputs.
    if samples.dtype in (np.float32, np.float64):
        # ``np.clip`` with ``out=`` requires the out array to be
        # writable + same dtype. ``np.ascontiguousarray`` may return
        # the original array (writable) or a copy (also writable), so
        # clipping in place is safe after it.
        samples = np.ascontiguousarray(samples)
        np.clip(samples, -1.0, 1.0, out=samples)
    else:
        samples = np.ascontiguousarray(samples)

    _ensure_audio_parent(path)
    sf.write(path, samples, sample_rate, subtype=subtype, format=format)


def atomic_save_wav(
    target_path: str,
    audio: torch.Tensor,
    sample_rate: int,
    *,
    durable: bool = False,
    **kwargs: Any,
) -> None:
    """Write a WAV to ``target_path`` atomically.

    Implementation: write to a sibling temp file in the same directory, then
    ``os.replace()`` into place. Cross-filesystem renames are *not* atomic
    on POSIX, so the temp file must live next to the target — that is why
    we use ``dir=target_dir`` instead of the system temp dir.

    The actual encode delegates to ``_safe_torchaudio_save`` so the file
    that ends up at ``target_path`` carries both guarantees: atomic
    publication AND audited tensor normalization.

    Args:
        target_path: Final destination. Missing parent directories are recreated.
        audio: ``(channels, samples)`` or ``(samples,)`` tensor.
        sample_rate: WAV sample rate in Hz.
        durable: Also survive a power loss — flush the data before the rename
            and the directory entry after it (``core.durable_io``). Costs a
            disk flush per file, so it is for files that are expensive to
            recreate (longform chapter/segment cache, #2279), not every write.
        **kwargs: Forwarded to ``_safe_torchaudio_save`` (``format``,
            ``bits_per_sample``). Legacy callers that pass other kwargs
            are tolerated for back-compat.

    Raises:
        Whatever ``_safe_torchaudio_save`` raises. The temp file is
        unlinked on failure so we do not leak ``.tmp`` files in
        ``DUB_DIR``.
    """
    _ensure_audio_parent(target_path)
    target_dir = os.path.dirname(target_path) or "."
    target_base = os.path.basename(target_path)
    # The temp file must end in ``.wav`` even though it is conceptually a
    # ``.tmp`` file. torchaudio.save infers the output format from the path
    # suffix and *ignores* the ``format=`` kwarg with the soundfile backend
    # — a ``.tmp`` suffix raises ``ValueError: Unsupported format: tmp``.
    # The leading dot + ``target_base`` prefix still marks the file as
    # transient and groups it next to its target in directory listings.
    fd, tmp_path = tempfile.mkstemp(
        prefix=f".{target_base}.",
        suffix=".wav",
        dir=target_dir,
    )
    os.close(fd)  # torchaudio reopens by path; we just needed a unique name.
    try:
        # Filter to kwargs _safe_torchaudio_save accepts; drop anything
        # legacy callers might have passed (e.g. ``encoding=``) so we
        # don't double-pass.
        safe_kwargs: dict[str, Any] = {}
        if "format" in kwargs:
            safe_kwargs["format"] = kwargs["format"]
        if "bits_per_sample" in kwargs:
            safe_kwargs["bits_per_sample"] = kwargs["bits_per_sample"]
        _safe_torchaudio_save(tmp_path, audio, sample_rate, **safe_kwargs)
        if durable:
            from core.durable_io import flush_file
            flush_file(tmp_path)
        os.replace(tmp_path, target_path)
        if durable:
            from core.durable_io import flush_dir
            flush_dir(target_dir)
    except BaseException:
        # BaseException so we clean up on KeyboardInterrupt + SystemExit too.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise
