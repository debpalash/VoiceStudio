"""Read-only, model-free advisory checks; never a perceptual quality score."""
from pathlib import Path
from typing import Literal

import numpy as np
import soundfile as sf
from pydantic import BaseModel


class AudioWarning(BaseModel):
    kind: Literal['empty', 'silence', 'quiet', 'loud', 'clipping', 'invalid']
    start: float
    end: float


class AudioQuality(BaseModel):
    duration: float
    analyzed_seconds: float
    truncated: bool
    warnings: list[AudioWarning]


def analyze_audio(path: str | Path, *, max_seconds: float = 7200) -> AudioQuality:
    """Scan 100ms windows, bounded to two hours and 100 warnings.

    RMS uses the loudest channel, not a phase-cancelling downmix. Relative
    volume checks exclude silence and use the median active-window level.
    One-second silence and 12dB sustained deviations are advisory only.
    """
    levels, clips, invalid = [], [], []
    with sf.SoundFile(path) as source:
        duration = len(source) / source.samplerate
        if source.channels > 64 or source.samplerate > 768000:
            raise ValueError('Unsupported audio dimensions')
        window = max(1, round(source.samplerate * .1))
        limit = min(len(source), int(max(0, min(max_seconds, 7200)) * source.samplerate))
        read = 0
        while read < limit:
            data = source.read(min(window, limit - read), dtype='float32', always_2d=True)
            if not len(data):
                break
            read += len(data)
            finite = np.isfinite(data)
            invalid.append(not bool(finite.all()))
            data = np.where(finite, data, 0).astype(np.float64)
            clips.append(bool(np.count_nonzero(np.abs(data) >= .999) >= 3))
            rms = float(np.sqrt(np.max(np.mean(data * data, axis=0))))
            levels.append(20 * np.log10(max(rms, 1e-12)))
        analyzed = read / source.samplerate
        step = window / source.samplerate

    warnings = []
    truncated = analyzed < duration
    if not levels:
        return AudioQuality(duration=duration, analyzed_seconds=analyzed,
                            truncated=truncated, warnings=[AudioWarning(kind='empty', start=0, end=0)])
    values = np.asarray(levels)
    active = values[values > -50]
    baseline = float(np.median(active)) if len(active) else -50
    checks = [
        ('invalid', np.asarray(invalid), 0),
        ('clipping', np.asarray(clips), 0),
        ('silence', values <= -50, 0 if not len(active) else 1),
        ('quiet', (values > -50) & (values < baseline - 12), .5),
        ('loud', values > baseline + 12, .5),
    ]
    for kind, mask, minimum in checks:
        edges = np.diff(np.concatenate(([False], mask, [False])).astype(int))
        for start, end in zip(np.flatnonzero(edges == 1), np.flatnonzero(edges == -1)):
            begin, finish = start * step, min(end * step, analyzed)
            if finish - begin + 1e-8 < minimum:
                continue
            warnings.append(AudioWarning(kind=kind, start=round(begin, 3), end=round(finish, 3)))
    # Sort the full set first so the cap keeps the earliest warnings on the
    # timeline rather than whichever check happened to run first.
    warnings.sort(key=lambda item: (item.start, item.kind))
    if len(warnings) > 100:
        truncated = True
        del warnings[100:]
    return AudioQuality(duration=duration, analyzed_seconds=analyzed,
                        truncated=truncated, warnings=warnings)
