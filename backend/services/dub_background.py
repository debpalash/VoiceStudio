"""Dialogue-only replacement beds: original outside speech, separated bed inside."""
from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import tempfile
from pathlib import Path

from services.ffmpeg_utils import find_ffmpeg, run_ffmpeg
from services.video_retime import expand_retime_chunks

RATE = 48000
FADE_S = .01
_locks: dict[str, asyncio.Lock] = {}


def dialogue_intervals(segments: list[dict]) -> list[tuple[float, float]]:
    intervals = []
    for row in segments:
        a, b = float(row['start']), float(row['end'])
        if not math.isfinite(a) or not math.isfinite(b) or a < 0 or b <= a:
            raise ValueError('Invalid dialogue interval')
        intervals.append((a, b))
    merged: list[tuple[float, float]] = []
    for a, b in sorted(intervals):
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(b, merged[-1][1]))
        else:
            merged.append((a, b))
    return merged


def splice_background(original: str, separated: str, output: str, intervals: list[tuple[float, float]]) -> None:
    """Stream in bounded memory; crossfades lie INSIDE dialogue intervals."""
    import numpy as np
    import soundfile as sf

    with sf.SoundFile(original) as src, sf.SoundFile(separated) as bed:
        if src.samplerate != bed.samplerate or src.channels != bed.channels:
            raise ValueError('Background inputs must have matching sample format')
        if bed.frames < src.frames - int(.1 * src.samplerate):
            raise ValueError('Separated background is incomplete')
        with sf.SoundFile(output, 'w', samplerate=src.samplerate, channels=src.channels, subtype='FLOAT') as out:
            offset = 0
            active = 0
            while True:
                wave = src.read(65536, dtype='float32', always_2d=True)
                if not len(wave):
                    break
                background = bed.read(len(wave), dtype='float32', always_2d=True)
                if len(background) < len(wave):
                    background = np.pad(background, ((0, len(wave)-len(background)), (0, 0)))
                times = np.arange(offset, offset + len(wave)) / src.samplerate
                mask = np.zeros(len(wave), dtype='float32')
                while active < len(intervals) and intervals[active][1] < times[0]:
                    active += 1
                for a, b in intervals[active:]:
                    if a > times[-1]:
                        break
                    fade = min(FADE_S, (b-a)/2)
                    envelope = np.clip(np.minimum((times-a)/fade, (b-times)/fade), 0, 1)
                    mask = np.maximum(mask, envelope)
                out.write(wave * (1-mask[:, None]) + background * mask[:, None])
                offset += len(wave)


async def _checked(cmd: list[str]) -> None:
    rc, _, error = await run_ffmpeg(cmd, timeout=1800.0)
    if rc:
        raise RuntimeError('Could not preserve original background audio: ' + str(error)[-500:])


async def surgical_background(source: str, separated: str, cache_dir: str, segments: list[dict], plan: list[dict], duration: float) -> str:
    for chunk in plan:
        ratio = float(chunk["stretch_ratio"])
        if not math.isfinite(ratio) or ratio <= 0:
            raise ValueError("Invalid background retiming ratio")
    intervals = dialogue_intervals(segments)
    if not intervals:
        raise ValueError('Dialogue timing is required to preserve original background audio')
    identity = [(p, os.stat(p).st_size, os.stat(p).st_mtime_ns) for p in (source, separated)]
    key = hashlib.sha256(json.dumps([1, identity, intervals, plan, duration], sort_keys=True).encode()).hexdigest()[:24]
    target = str(Path(cache_dir) / f'surgical_{key}.wav')
    async with _locks.setdefault(target, asyncio.Lock()):
        if os.path.isfile(target):
            return target
        ffmpeg = find_ffmpeg()
        with tempfile.TemporaryDirectory(prefix='.surgical-', dir=cache_dir) as tmp:
            original, bed, spliced = [str(Path(tmp)/name) for name in ('source.wav', 'bed.wav', 'spliced.wav')]
            for inp, out in ((source, original), (separated, bed)):
                await _checked([ffmpeg, '-y', '-i', inp, '-map', '0:a:0', '-vn', '-ar', str(RATE), '-ac', '2', '-c:a', 'pcm_f32le', out])
            await asyncio.to_thread(splice_background, original, bed, spliced, intervals)
            if plan and any(abs(float(p['stretch_ratio'])-1) > 1e-6 for p in plan):
                chunks = expand_retime_chunks(plan, duration)
                # Bound filter buffering for long projects; trim each batch's
                # input before splitting it among the chunk filters.
                batches = []
                for batch_index in range(0, len(chunks), 16):
                    batch = chunks[batch_index:batch_index+16]
                    origin = batch[0][0]
                    filters = []
                    for i, (a, b, ratio) in enumerate(batch):
                        rate = 1 / ratio
                        tempos = []
                        while rate < .5:
                            tempos.append('atempo=0.5')
                            rate /= .5
                        while rate > 2:
                            tempos.append('atempo=2')
                            rate /= 2
                        tempos.append(f'atempo={rate:.9f}')
                        length = (b-a)*ratio
                        filters.append(f'[0:a]atrim=start={a-origin:.9f}:end={b-origin:.9f},asetpts=PTS-STARTPTS,' + ','.join(tempos) + f',apad,atrim=duration={length:.9f}[c{i}]')
                    filters.append(''.join(f'[c{i}]' for i in range(len(batch))) + f'concat=n={len(batch)}:v=0:a=1[out]')
                    script = Path(tmp)/'retime.txt'
                    script.write_text(';'.join(filters))
                    batch_name = f'batch{batch_index}.wav'
                    output = str(Path(tmp)/batch_name)
                    await _checked([ffmpeg, '-y', '-ss', str(origin), '-t', str(batch[-1][1]-origin), '-i', spliced, '-filter_complex_script', str(script), '-map', '[out]', '-c:a', 'pcm_f32le', output])
                    batches.append(batch_name)
                listing = Path(tmp)/'concat.txt'
                listing.write_text(''.join(f"file '{name}'\n" for name in batches))
                retimed = str(Path(tmp)/'retimed.wav')
                await _checked([ffmpeg, '-y', '-f', 'concat', '-safe', '1', '-i', str(listing), '-c:a', 'copy', retimed])
                spliced = retimed
            os.replace(spliced, target)
    return target
