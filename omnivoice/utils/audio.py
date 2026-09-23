#!/usr/bin/env python3
# Copyright    2026  Xiaomi Corp.        (authors:  Han Zhu)
#
# See ../../LICENSE for clarification regarding multiple authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

"""Audio I/O and processing utilities.

Provides functions for loading, resampling, silence removal, chunking,
cross-fading, and format conversion. Used by ``OmniVoice.generate()`` during
inference post-processing.
"""

import logging
import math

import numpy as np
import torch
import torchaudio
from pydub import AudioSegment
from pydub.silence import detect_leading_silence, detect_nonsilent, split_on_silence

logger = logging.getLogger(__name__)

# Machine-readable marker prefixed to the "reference clip has no usable audio"
# ValueError so UI layers can recognize the class and show localized guidance
# instead of the raw English detail (issue #1188). The frontend matches this
# exact string (frontend/src/utils/errorToast.jsx) — change both or neither.
CLONE_REF_UNUSABLE_MARKER = "[clone_ref_unusable]"
CLONE_REF_TOO_LONG_MARKER = "[clone_ref_too_long]"
CLONE_REF_NO_SPEECH_MARKER = "[clone_ref_no_speech]"

# Reference-length policy (#1578, #2281). A transcript is aligned against the
# whole clip, so a clip paired with one is used as-is only up to
# CLONE_REF_TEXT_MAX_SECONDS. Without a transcript, a longer clip is split into
# CLONE_REF_WINDOW_SECONDS windows (at most CLONE_REF_MAX_WINDOWS of them) and
# the passage with the most detected speech is cloned. Backends advertise the
# first value as OmniVoice's max_ref_seconds, so the UI states the real limit.
CLONE_REF_TEXT_MAX_SECONDS = 20.0
CLONE_REF_WINDOW_SECONDS = 15.0
CLONE_REF_MAX_WINDOWS = 5


def clone_ref_transcript_too_long_message(duration_s: float) -> str:
    """The actionable error for a supplied transcript on an over-long clip."""
    return (
        f"{CLONE_REF_TOO_LONG_MARKER} Reference audio is {duration_s:.1f} "
        f"seconds long; supplied transcripts support at most "
        f"{CLONE_REF_TEXT_MAX_SECONDS:.0f} seconds. Trim both the audio and "
        "transcript to the same 3-10 second passage, or omit the transcript "
        "so VoiceStudio can pick and transcribe the best passage automatically."
    )


def load_audio(audio_path: str, sampling_rate: int):
    """
    Load the waveform with torchaudio and resampling if needed.

    Parameters:
        audio_path: path of the audio.
        sampling_rate: target sampling rate.

    Returns:
        Loaded prompt waveform with target sampling rate,
        PyTorch tensor of shape (1, T)
    """
    try:
        waveform, prompt_sampling_rate = torchaudio.load(
            audio_path, backend="soundfile"
        )
    except (ImportError, RuntimeError, OSError):
        # Fallback via pydub+ffmpeg for formats torchaudio can't handle.
        # ImportError belongs here too: torchaudio >= 2.9 routes load()
        # through TorchCodec and raises ImportError when its FFmpeg shared
        # libraries are absent. The ``backend="soundfile"`` argument above
        # does NOT avoid that — 2.9 accepts and ignores it.
        aseg = AudioSegment.from_file(audio_path)
        waveform = audiosegment_to_tensor(aseg)
        prompt_sampling_rate = aseg.frame_rate

    if prompt_sampling_rate != sampling_rate:
        waveform = torchaudio.functional.resample(
            waveform,
            orig_freq=prompt_sampling_rate,
            new_freq=sampling_rate,
        )
    if waveform.shape[0] > 1:
        waveform = torch.mean(waveform, dim=0, keepdim=True)

    return waveform


def remove_silence(
    audio: torch.Tensor,
    sampling_rate: int,
    mid_sil: int = 300,
    lead_sil: int = 100,
    trail_sil: int = 300,
    silence_thresh: float = -50,
):
    """
    Remove middle silences longer than mid_sil ms, and edge silences longer than edge_sil ms

    Parameters:
        audio: PyTorch tensor with shape (C, T).
        sampling_rate: sampling rate of the audio.
        mid_sil: the duration of silences in the middle of audio to be removed in ms.
            if mid_sil <= 0, no middle silence will be removed.
        edge_sil: the duration of silences in the edge of audio to be removed in ms.
        trail_sil: the duration of added trailing silence in ms.
        silence_thresh: dBFS level below which a window counts as silence.
            The historical hardcoded value (-50) is the default; gentler
            (more negative) values keep quieter audio.

    Returns:
        PyTorch tensor with shape (C, T), where C is number of channels
            and T is number of audio samples
    """
    # Load audio file
    wave = tensor_to_audiosegment(audio, sampling_rate)

    if mid_sil > 0:
        # Split audio using silences longer than mid_sil
        non_silent_segs = split_on_silence(
            wave,
            min_silence_len=mid_sil,
            silence_thresh=silence_thresh,
            keep_silence=mid_sil,
            seek_step=10,
        )

        # Concatenate all non-silent segments
        wave = AudioSegment.silent(duration=0)
        for seg in non_silent_segs:
            wave += seg

    # Remove silence longer than 0.1 seconds in the begining and ending of wave
    wave = remove_silence_edges(wave, lead_sil, trail_sil, silence_thresh)

    # Convert to PyTorch tensor
    return audiosegment_to_tensor(wave)


def remove_silence_safe(
    audio: torch.Tensor,
    sampling_rate: int,
    mid_sil: int = 300,
    lead_sil: int = 100,
    trail_sil: int = 300,
    silence_threshs: tuple = (-50.0, -60.0, -70.0),
    min_keep_s: float = 0.5,
):
    """Silence removal that can never consume the whole recording (issue #1188).

    :func:`remove_silence` detects speech against a fixed dBFS threshold. A
    quiet-but-real recording — laptop mic far from the speaker, low input
    gain — can sit entirely below that threshold and be removed wholesale,
    leaving an empty clip and a dead-end error downstream ("Reference audio is
    empty after silence removal").

    Retry ladder: try the standard threshold first, then progressively gentler
    ones; accept the first result that keeps at least ``min_keep_s`` of audio
    (or the full clip, when the input is already shorter than that). If every
    rung still consumes (nearly) everything, skip trimming and return the input
    unchanged — an untrimmed real recording always beats an empty one.

    Same signature/semantics as :func:`remove_silence` otherwise; drop-in.
    """
    total = audio.size(-1)
    if total == 0:
        return audio
    floor = max(1, min(int(min_keep_s * sampling_rate), total))
    for thresh in silence_threshs:
        trimmed = remove_silence(
            audio, sampling_rate, mid_sil, lead_sil, trail_sil,
            silence_thresh=thresh,
        )
        if trimmed.size(-1) >= floor:
            if thresh != silence_threshs[0]:
                logger.info(
                    "Silence removal at %g dBFS would have removed nearly the "
                    "whole clip (quiet recording); recovered with the gentler "
                    "%g dBFS threshold.",
                    silence_threshs[0], thresh,
                )
            return trimmed
    logger.warning(
        "Silence removal would have removed (nearly) the whole clip at every "
        "threshold %s — the recording is very quiet. Skipping silence "
        "removal and using the audio as-is.",
        silence_threshs,
    )
    return audio


def validate_clone_reference(ref_wav: torch.Tensor, ref_rms: float) -> None:
    """Raise an actionable error when a voice-clone reference clip has no
    usable audio at all (issue #1188).

    Quiet-but-real clips are handled upstream by :func:`remove_silence_safe`
    (gentler thresholds, then no trimming), so the only clips that reach a
    hard failure are genuinely empty ones: zero samples, pure digital
    silence, or non-finite garbage (NaN/inf samples make the RMS non-finite).
    The message carries :data:`CLONE_REF_UNUSABLE_MARKER` so the UI can show
    localized guidance, and tells API callers the concrete fix directly.
    """
    if ref_wav.size(-1) == 0 or not math.isfinite(ref_rms) or ref_rms <= 0.0:
        raise ValueError(
            f"{CLONE_REF_UNUSABLE_MARKER} Reference audio has no usable "
            "sound — the clip is empty or completely silent, so there is no "
            "voice to clone. Record again closer to the microphone (check "
            "that the correct input device is selected and its level moves "
            "while you speak), or choose a different reference clip."
        )


def remove_silence_edges(
    audio: AudioSegment,
    lead_sil: int = 100,
    trail_sil: int = 300,
    silence_threshold: float = -50,
):
    """
    Remove edge silences longer than `keep_silence` ms.

    Parameters:
        audio: an AudioSegment object.
        keep_silence: kept silence in the edge.
        only_edge: If true, only remove edge silences.
        silence_threshold: the threshold of silence.

    Returns:
        An AudioSegment object
    """
    # Remove heading silence
    start_idx = detect_leading_silence(audio, silence_threshold=silence_threshold)
    start_idx = max(0, start_idx - lead_sil)
    audio = audio[start_idx:]

    # Remove trailing silence
    audio = audio.reverse()
    start_idx = detect_leading_silence(audio, silence_threshold=silence_threshold)
    start_idx = max(0, start_idx - trail_sil)
    audio = audio[start_idx:]
    audio = audio.reverse()

    return audio


def audiosegment_to_tensor(aseg):
    """
    Convert a pydub.AudioSegment to PyTorch audio tensor
    """
    audio_data = np.array(aseg.get_array_of_samples())

    # Convert to float32 and normalize to [-1, 1] range
    audio_data = audio_data.astype(np.float32) / aseg.max_possible_amplitude

    # Handle channels
    if aseg.channels == 1:
        # Mono channel: add channel dimension (T) -> (1, T)
        tensor_data = torch.from_numpy(audio_data).unsqueeze(0)
    else:
        # Multi-channel: reshape to (C, T)
        tensor_data = torch.from_numpy(audio_data.reshape(-1, aseg.channels).T)

    return tensor_data


def tensor_to_audiosegment(tensor, sample_rate):
    """
    Convert a PyTorch audio tensor to pydub.AudioSegment

    Parameters:
        tensor: Tensor with shape (C, T), where C is the number of channels
            and T is the time steps
        sample_rate: Audio sample rate
    """
    # Convert tensor to numpy array
    assert isinstance(tensor, torch.Tensor)
    audio_np = tensor.cpu().numpy()

    # Convert to int16 type (common format for pydub)
    # Assumes tensor values are in [-1, 1] range as floating point
    audio_np = (audio_np * 32768.0).clip(-32768, 32767).astype(np.int16)

    # Convert to byte stream
    # For multi-channel audio, pydub requires interleaved format
    # (e.g., left-right-left-right)
    if audio_np.shape[0] > 1:
        # Convert to interleaved format
        audio_np = audio_np.transpose(1, 0).flatten()
    audio_bytes = audio_np.tobytes()

    # Create AudioSegment
    audio_segment = AudioSegment(
        data=audio_bytes,
        sample_width=2,
        frame_rate=sample_rate,
        channels=tensor.shape[0],
    )

    return audio_segment


def fade_and_pad_audio(
    audio: torch.Tensor,
    pad_duration: float = 0.1,
    fade_duration: float = 0.1,
    sample_rate: int = 24000,
) -> torch.Tensor:
    """
    Applies a smooth fade-in and fade-out to the audio, and then pads both sides
    with pure silence to prevent abrupt starts and ends (clicks/pops).

    Args:
        audio: PyTorch tensor of shape (C, T) containing audio data.
        pad_duration: Duration of pure silence to add to each end (in seconds).
        fade_duration: Duration of the fade-in/out curve (in seconds).
        sample_rate: Audio sampling rate.

    Returns:
        Processed sequence tensor with shape (C, T_new)
    """
    if audio.shape[-1] == 0:
        return audio

    fade_samples = int(fade_duration * sample_rate)
    pad_samples = int(pad_duration * sample_rate)

    processed = audio.clone()

    if fade_samples > 0:
        k = min(fade_samples, processed.shape[-1] // 2)

        if k > 0:
            fade_in = torch.linspace(
                0, 1, k, device=processed.device, dtype=processed.dtype
            )[None, :]
            processed[..., :k] = processed[..., :k] * fade_in

            fade_out = torch.linspace(
                1, 0, k, device=processed.device, dtype=processed.dtype
            )[None, :]
            processed[..., -k:] = processed[..., -k:] * fade_out

    if pad_samples > 0:
        silence = torch.zeros(
            (processed.shape[0], pad_samples),
            dtype=processed.dtype,
            device=processed.device,
        )
        processed = torch.cat([silence, processed, silence], dim=-1)

    return processed


def trim_long_audio(
    audio: torch.Tensor,
    sampling_rate: int,
    max_duration: float = 15.0,
    min_duration: float = 3.0,
    trim_threshold: float = 20.0,
) -> torch.Tensor:
    """Trim audio to <= max_duration by splitting at the largest silence gap.

    Only trims when the audio exceeds *trim_threshold* seconds.

    Args:
        audio: Audio tensor of shape (C, T).
        sampling_rate: Audio sampling rate.
        max_duration: Maximum duration in seconds.
        min_duration: Minimum duration in seconds.
        trim_threshold: Only trim if audio is longer than this (seconds).

    Returns:
        Trimmed audio tensor.
    """
    duration = audio.size(-1) / sampling_rate
    if duration <= trim_threshold:
        return audio

    seg = tensor_to_audiosegment(audio, sampling_rate)
    nonsilent = detect_nonsilent(
        seg, min_silence_len=100, silence_thresh=-40, seek_step=10
    )
    if not nonsilent:
        return audio

    max_ms = int(max_duration * 1000)
    min_ms = int(min_duration * 1000)

    # Walk through speech regions; at each gap pick the latest split <= max_duration
    best_split = 0
    for start, end in nonsilent:
        if start > best_split and start <= max_ms:
            best_split = start
        if end > max_ms:
            break

    if best_split < min_ms:
        best_split = min(max_ms, len(seg))

    trimmed = seg[:best_split]
    return audiosegment_to_tensor(trimmed)


def cross_fade_chunks(
    chunks: list[torch.Tensor],
    sample_rate: int,
    silence_duration: float = 0.3,
) -> torch.Tensor:
    """Concatenate audio chunks with a short silence gap and fade at boundaries.

    Each boundary is structured as: fade-out tail → silence buffer → fade-in head.
    This avoids click artifacts from direct concatenation or overlapping mismatch.

    Args:
        chunks: List of audio tensors, each (C, T).
        sample_rate: Audio sample rate.
        silence_duration: Total silence gap duration in seconds.

    Returns:
        Merged audio tensor (C, T_total).
    """
    if len(chunks) == 1:
        return chunks[0]

    total_n = int(silence_duration * sample_rate)
    fade_n = total_n // 3
    silence_n = fade_n  # middle silent gap
    merged = chunks[0].clone()

    for chunk in chunks[1:]:
        dev, dt = merged.device, merged.dtype
        parts = [merged]

        # Fade out tail of current merged audio
        fout_n = min(fade_n, merged.size(-1))
        if fout_n > 0:
            w_out = torch.linspace(1, 0, fout_n, device=dev, dtype=dt)[None, :]
            parts[-1][..., -fout_n:] = parts[-1][..., -fout_n:] * w_out

        # Silent buffer between chunks
        parts.append(torch.zeros(chunks[0].shape[0], silence_n, device=dev, dtype=dt))

        # Fade in head of next chunk
        fade_in = chunk.clone()
        fin_n = min(fade_n, fade_in.size(-1))
        if fin_n > 0:
            w_in = torch.linspace(0, 1, fin_n, device=dev, dtype=dt)[None, :]
            fade_in[..., :fin_n] = fade_in[..., :fin_n] * w_in

        parts.append(fade_in)
        merged = torch.cat(parts, dim=-1)

    return merged
