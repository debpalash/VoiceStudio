"""Place complete speech inside fixed subtitle windows without shifting later cues."""
import math


def bounded_audio_layout(cues, durations, source_duration):
    if len(cues) != len(durations):
        raise ValueError("Every subtitle needs one audio duration")
    source_duration = float(source_duration)
    if not math.isfinite(source_duration) or source_duration < 0:
        raise ValueError("Invalid source duration")
    layout = []
    previous_end = 0.0
    for cue, duration in zip(cues, durations):
        start, end, duration = float(cue[0]), float(cue[1]), float(duration)
        if (not all(math.isfinite(v) for v in (start, end, duration))
                or start < 0 or end <= start or duration < 0
                or start < previous_end - 1e-6
                or (source_duration > 0 and end > source_duration + 1e-3)):
            raise ValueError("Invalid or overlapping subtitle window")
        rate = max(1.0, duration / (end - start))
        layout.append((start, min(end, start + duration / rate), rate))
        previous_end = end
    return layout, source_duration or previous_end


# Subtitle-only and consolidated narration share the same fixed-window contract.
subtitle_audio_layout = bounded_audio_layout
