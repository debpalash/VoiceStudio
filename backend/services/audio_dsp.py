"""
Audio DSP pipeline — broadcast-grade mastering + configurable effects chain.

`apply_mastering()` is the shared pre-stage that runs before the user's
effect preset: highpass + gentle compression only (see `MASTERING_CHAIN`).
Reverb is deliberately NOT part of it — it is preset-declared only (e.g.
cinematic, warm); a hidden reverb here used to bake echo into every non-raw
synthesis, which field reports flagged. `apply_effects_chain()` lets callers
build custom pipelines from a list of named effects.

Filters, compression, gate and reverb use Spotify's `pedalboard` library; when
it isn't installed those stages are skipped (audio returned unmodified). The
leveler and the peak limiter are pure numpy (#3412), so they always run.
"""
import logging
import torch

logger = logging.getLogger("omnivoice.dsp")

# ── Effect presets ──────────────────────────────────────────────────────

EFFECT_PRESETS = {
    "broadcast": {
        "label": "Broadcast",
        "icon": "📻",
        "description": "Radio/podcast standard — even volume, warm and clear.",
        # Backs the Clone "Even out volume" toggle. A slow leveler evens out
        # passages; the old 3:1 / 5 ms / 80 ms compressor here flattened
        # peaks and lifted decay tails (#3412). The limiter only catches overs.
        "chain": [
            {"type": "highpass", "cutoff_hz": 80},
            {"type": "leveler", "target_db": -20.0, "window_ms": 400, "amount": 0.7, "max_gain_db": 8.0, "smooth_ms": 200},
            {"type": "eq", "low_gain_db": 1.5, "mid_gain_db": 0, "high_gain_db": 2.0},
            {"type": "limiter", "threshold_db": -1.0},
        ],
    },
    "cinematic": {
        "label": "Cinematic",
        "icon": "🎬",
        "description": "Film-quality — spacious reverb, gentle compression.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 60},
            {"type": "compressor", "threshold_db": -15, "ratio": 1.8, "attack_ms": 10, "release_ms": 150},
            {"type": "reverb", "room_size": 0.35, "wet_level": 0.15, "dry_level": 0.85},
            {"type": "limiter", "threshold_db": -1.5},
        ],
    },
    "podcast": {
        "label": "Podcast",
        "icon": "🎙️",
        "description": "Close-mic, intimate — heavy compression, no reverb.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 100},
            {"type": "noise_gate", "threshold_db": -40, "release_ms": 200},
            {"type": "compressor", "threshold_db": -20, "ratio": 4.0, "attack_ms": 2, "release_ms": 60},
            {"type": "eq", "low_gain_db": -1.0, "mid_gain_db": 2.0, "high_gain_db": 1.5},
            {"type": "limiter", "threshold_db": -0.5},
        ],
    },
    "raw": {
        "label": "Raw",
        "icon": "🔇",
        "description": "No processing — model output as-is.",
        "chain": [],
    },
    "warm": {
        "label": "Warm",
        "icon": "☀️",
        "description": "Boosted low-mids, subtle saturation, cozy feel.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 60},
            {"type": "eq", "low_gain_db": 3.0, "mid_gain_db": 1.0, "high_gain_db": -1.0},
            {"type": "compressor", "threshold_db": -16, "ratio": 2.0, "attack_ms": 8, "release_ms": 120},
            {"type": "reverb", "room_size": 0.15, "wet_level": 0.06, "dry_level": 0.94},
        ],
    },
    "bright": {
        "label": "Bright",
        "icon": "✨",
        "description": "Crisp high-end, presence boost, airy feel.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 80},
            {"type": "eq", "low_gain_db": -1.0, "mid_gain_db": 0, "high_gain_db": 4.0},
            {"type": "compressor", "threshold_db": -14, "ratio": 2.5, "attack_ms": 3, "release_ms": 80},
            {"type": "limiter", "threshold_db": -1.0},
        ],
    },
}


def list_effect_presets() -> list[dict]:
    """Return presets for the frontend UI picker."""
    return [
        {"id": k, "label": v["label"], "icon": v["icon"], "description": v["description"]}
        for k, v in EFFECT_PRESETS.items()
    ]


def get_effect_chain(preset_id: str) -> list[dict]:
    """Return the effect chain for a preset. Falls back to empty chain."""
    p = EFFECT_PRESETS.get(preset_id)
    return p["chain"] if p else []


# ── Core DSP functions ──────────────────────────────────────────────────

#: Shared pre-preset mastering stage: highpass + gentle compression ONLY.
#: Reverb must never live here — a hidden Reverb in this chain baked echo
#: into every non-raw synthesis regardless of the chosen preset (field
#: reports of echoey voices; the podcast preset even promises "no reverb").
#: Reverb is preset-declared only (see EFFECT_PRESETS: cinematic, warm).
MASTERING_CHAIN = [
    {"type": "highpass", "cutoff_hz": 60},
    {"type": "compressor", "threshold_db": -15, "ratio": 1.5, "attack_ms": 2.0, "release_ms": 100},
]


def apply_mastering(audio_tensor, sample_rate=24000):
    """Applies the broadcast pre-stage (highpass + gentle compression) to the clone voice.

    Reverb is intentionally absent — only user-chosen effect presets declare
    it. Degrades gracefully: pedalboard missing or any DSP error returns the
    input unmodified.
    """
    try:
        return apply_effects_chain(audio_tensor, sample_rate, MASTERING_CHAIN)
    except Exception as e:
        logger.warning("Mastering DSP Error: %s", e)
        return audio_tensor


def normalize_audio(audio_tensor, target_dBFS=-2.0):
    """Peak-normalizes the audio to a standard broadcasting level (-2 dB) to fix F5TTS volume fluctuations.

    Never amplifies a near-silent signal. A failed/empty render sits at the
    noise floor; blindly scaling its peak up to -2 dBFS applies thousands of
    times of gain and turns silence into full-scale hiss — the "blank noise"
    some generated voices exhibited. Below a -50 dBFS silence floor we leave the
    audio untouched so it stays inaudible (and downstream guards can treat it as
    a dead render) instead of shipping amplified noise. Real speech — even a
    whisper — peaks well above this floor, so normal output is unaffected.
    """
    if audio_tensor.numel() == 0:
        return audio_tensor
    max_val = torch.abs(audio_tensor).max()
    # -50 dBFS ≈ 0.00316 linear. Anything at/below this is silence / noise floor.
    silence_floor = 10 ** (-50.0 / 20.0)
    if max_val > silence_floor:
        target_amp = 10 ** (target_dBFS / 20.0)
        audio_tensor = audio_tensor * (target_amp / max_val)
    return audio_tensor


def trim_trailing_silence(
    audio_tensor: torch.Tensor,
    sample_rate: int,
    keep_tail_s: float = 0.3,
) -> torch.Tensor:
    """Trim trailing near-silence from a generated clip, keeping a short
    natural tail of ``keep_tail_s`` seconds after the last voiced sample.

    Amplitude-based SILENCE trim only — no content analysis of any kind.
    Uses the same -50 dBFS silence floor as :func:`normalize_audio`: the last
    sample above that floor marks the end of speech, and everything more than
    ``keep_tail_s`` past it is dropped.

    Guaranteed no-op cases (input returned as-is, same object):
      • the trailing quiet span is already ≤ ``keep_tail_s`` (clean output);
      • the entire clip sits below the floor (dead render — downstream
        dead-render guards own that case, we must not shrink their evidence);
      • empty input.

    Accepts ``(n,)`` or ``(channels, n)`` tensors; the returned tensor keeps
    the input's shape convention.
    """
    if audio_tensor.numel() == 0:
        return audio_tensor
    # -50 dBFS ≈ 0.00316 linear — matches normalize_audio's silence floor.
    floor = 10 ** (-50.0 / 20.0)
    envelope = torch.abs(audio_tensor)
    if envelope.ndim > 1:
        envelope = envelope.amax(dim=tuple(range(envelope.ndim - 1)))
    voiced = torch.nonzero(envelope > floor)
    if voiced.numel() == 0:
        return audio_tensor
    last_voiced = int(voiced[-1].item())
    end = last_voiced + 1 + int(keep_tail_s * sample_rate)
    if end >= audio_tensor.shape[-1]:
        return audio_tensor
    return audio_tensor[..., :end]



def trim_speech_padding(audio_tensor: torch.Tensor, sample_rate: int) -> torch.Tensor:
    """Remove generated edge silence before timing, retaining 50 ms of context.

    Never compress silence into the spoken slot or delete internal pauses.
    Silent/invalid outputs remain intact for the generation integrity guard.
    """
    if audio_tensor.numel() == 0 or sample_rate <= 0:
        return audio_tensor
    envelope = audio_tensor.abs()
    if envelope.ndim > 1:
        envelope = envelope.amax(dim=tuple(range(envelope.ndim - 1)))
    voiced = torch.nonzero(envelope > 10 ** (-50 / 20))
    if voiced.numel() == 0:
        return audio_tensor
    margin = int(sample_rate * 0.05)
    start = max(0, int(voiced[0].item()) - margin)
    end = min(audio_tensor.shape[-1], int(voiced[-1].item()) + 1 + margin)
    return audio_tensor[..., start:end]

#: Samples per block for the numpy stages. Long takes are processed in blocks
#: so temporary memory stays bounded (a 30-minute take at 24 kHz is 43 M
#: samples; whole-take float64 temporaries needed several GB).
_CHUNK_SAMPLES = 1 << 18


def _box_mean(values, width: int):
    """Centered moving average over ``width`` frames, shrinking at the edges."""
    import numpy as np

    n = values.shape[0]
    csum = np.concatenate(([0.0], np.cumsum(values)))
    lo = np.clip(np.arange(n) - width // 2, 0, n)
    hi = np.clip(np.arange(n) + width - width // 2, 0, n)
    return (csum[hi] - csum[lo]) / np.maximum(hi - lo, 1)


def level_speech(
    audio_tensor: torch.Tensor,
    sample_rate: int,
    *,
    target_db: float | None = None,
    window_ms: float = 400.0,
    amount: float = 0.7,
    max_gain_db: float = 8.0,
    smooth_ms: float = 200.0,
) -> torch.Tensor:
    """Even out phrase-to-phrase loudness without touching syllable dynamics.

    A slow leveler, not a compressor (#3412). Speech is told apart from pauses
    and tails with a short 50 ms gate, then each speech frame's loudness is
    the mean of the *speech* frames within ``window_ms``, so a phrase ending
    into silence is not measured as quieter. Each passage moves ``amount`` of
    the way toward the take's speech average, by at most ``max_gain_db``.
    Pauses, breaths and decay tails hold the gain of the speech before them,
    so they are never boosted toward speech level (the padded, reverberant
    sound of the old compressor). The gain is smoothed over ``smooth_ms`` in
    both directions, so it cannot follow single syllables: peaks keep their
    shape and the gain never "breathes" between words.

    Level-independent (only relative levels matter, plus the optional fixed
    ``target_db`` for the speech average), pure numpy and processed in blocks
    of ``_CHUNK_SAMPLES``. A silent clip is returned unchanged.
    """
    if audio_tensor.numel() == 0:
        return audio_tensor
    import numpy as np

    source = audio_tensor.detach().cpu()
    n = source.shape[-1]
    flat = source.reshape(-1, n)
    hop = max(1, int(sample_rate * 0.01))
    step = max(1, _CHUNK_SAMPLES // hop) * hop
    frames = -(-n // hop)
    frame_power = np.empty(frames)
    for start in range(0, n, step):
        power = (flat[:, start:start + step].double().numpy() ** 2).mean(axis=0)
        count = -(-power.shape[0] // hop)
        padded = np.zeros(count * hop)
        padded[:power.shape[0]] = power
        first = start // hop
        frame_power[first:first + count] = padded.reshape(count, hop).mean(axis=1)

    # Speech gate like EBU R128 on a short 50 ms window, which keeps tails and
    # pauses out of it: a floor 40 dB under the loudest moment (never below
    # -70 dBFS), then a relative gate 25 dB under the speech average. Both are
    # relative, so the result does not depend on input level. A clip that
    # never rises above the -50 dBFS silence floor shared with
    # normalize_audio is left alone.
    short_power = _box_mean(frame_power, 5)
    short_db = 10.0 * np.log10(short_power + 1e-20)
    if not short_db.max() > -50.0:
        return audio_tensor
    floor_db = max(-70.0, short_db.max() - 40.0)
    average_db = 10.0 * np.log10(short_power[short_db > floor_db].mean())
    speech = short_db > max(floor_db, average_db - 25.0)
    speech_db = 10.0 * np.log10(frame_power[speech].mean() + 1e-20)

    win = max(1, round(window_ms / 10.0))
    speech_power = _box_mean(np.where(speech, frame_power, 0.0), win)
    speech_share = _box_mean(speech.astype(float), win)
    level_db = 10.0 * np.log10(speech_power / np.maximum(speech_share, 1e-12) + 1e-20)

    gain_db = np.clip(amount * (speech_db - level_db), -max_gain_db, max_gain_db)
    if target_db is not None:
        gain_db += target_db - speech_db
    # Hold the last speech gain across pauses (leading pauses take the first).
    idx = np.where(speech, np.arange(frames), -1)
    idx = np.maximum.accumulate(idx)
    idx[idx < 0] = int(np.argmax(speech))
    gain_db = gain_db[idx]

    alpha = np.exp(-1.0 / max(1.0, smooth_ms / 10.0))
    for order in (slice(None), slice(None, None, -1)):  # zero-phase smoothing
        g = gain_db[order].copy()
        for i in range(1, frames):
            g[i] = alpha * g[i - 1] + (1.0 - alpha) * g[i]
        gain_db[order] = g

    centers = np.arange(frames) * hop + hop / 2.0
    out = torch.empty_like(flat)
    for start in range(0, n, step):
        stop = min(n, start + step)
        gain = 10.0 ** (np.interp(np.arange(start, stop), centers, gain_db) / 20.0)
        out[:, start:stop] = torch.from_numpy(flat[:, start:stop].double().numpy() * gain)
    return out.reshape(source.shape).to(audio_tensor.device)


def _window_min(values, before: int, after: int):
    """Minimum of ``values[i - before : i + after + 1]`` for every i, in O(n).

    van Herk/Gil-Werman: block-wise prefix and suffix minima, so it stays
    linear without SciPy.
    """
    import numpy as np

    width = before + after + 1
    n = values.shape[0]
    tail = (-(n + width - 1)) % width
    padded = np.pad(values, (before, after + tail), constant_values=np.inf)
    blocks = padded.reshape(-1, width)
    prefix = np.minimum.accumulate(blocks, axis=1).ravel()
    suffix = np.minimum.accumulate(blocks[:, ::-1], axis=1)[:, ::-1].ravel()
    start = np.arange(n)
    return np.minimum(suffix[start], prefix[start + width - 1])


def peak_limit(
    audio_tensor: torch.Tensor,
    sample_rate: int,
    *,
    threshold_db: float = -1.0,
    lookahead_ms: float = 5.0,
    release_ms: float = 40.0,
) -> torch.Tensor:
    """Look-ahead peak limiter: a ceiling, nothing else (#3412).

    pedalboard's ``Limiter`` (JUCE) is not a ceiling: it puts a fixed 4:1,
    2 ms compressor at -10 dBFS in front of the brickwall and adds makeup
    gain, so every speech peak between -10 and 0 dBFS was flattened. This one
    leaves audio below ``threshold_db`` bit-identical and only dips the gain
    around samples that would cross it: the dip starts at most
    ``lookahead_ms`` before an over and holds ``release_ms`` after it, then
    ramps back. The gain is a moving average (radius r) of a minimum taken
    over r ahead and r + hold behind, so every averaged value is at or below
    the gain each over needs and the output never exceeds the ceiling.
    Processed in overlapping blocks of ``_CHUNK_SAMPLES``; the result equals
    a single pass.
    """
    if audio_tensor.numel() == 0:
        return audio_tensor
    import numpy as np

    source = audio_tensor.detach().cpu()
    n = source.shape[-1]
    flat = source.reshape(-1, n)
    ceiling = 10.0 ** (threshold_db / 20.0)
    step = max(1, _CHUNK_SAMPLES)
    if not any(flat[:, s:s + step].abs().max().item() > ceiling for s in range(0, n, step)):
        return audio_tensor

    ramp = max(1, int(sample_rate * lookahead_ms / 2000.0))
    hold = max(0, int(sample_rate * release_ms / 1000.0))
    before, after = 2 * ramp + hold, 2 * ramp  # samples one output depends on
    out = flat.clone()
    for start in range(0, n, step):
        stop = min(n, start + step)
        lo, hi = max(0, start - before), min(n, stop + after)
        block = flat[:, lo:hi].double().numpy()
        gain = np.minimum(1.0, ceiling / np.maximum(np.abs(block).max(axis=0), 1e-12))
        if gain.min() >= 1.0:
            continue
        held = _window_min(gain, ramp + hold, ramp)
        csum = np.concatenate(([0.0], np.cumsum(np.pad(held, ramp, mode="edge"))))
        smooth = (csum[2 * ramp + 1:] - csum[:-(2 * ramp + 1)]) / (2 * ramp + 1)
        keep = slice(start - lo, stop - lo)
        applied = np.minimum(smooth, gain)[keep]  # min() only absorbs float rounding
        out[:, start:stop] = torch.from_numpy(block[:, keep] * applied)
    return out.reshape(source.shape).to(audio_tensor.device)

_NUMPY_STAGES = {
    "leveler": (level_speech, ("target_db", "window_ms", "amount", "max_gain_db", "smooth_ms")),
    "limiter": (peak_limit, ("threshold_db", "lookahead_ms", "release_ms")),
}


def apply_effects_chain(audio_tensor, sample_rate: int, chain: list[dict]) -> torch.Tensor:
    """Apply a chain of named effects to an audio tensor.

    Each item in `chain` is a dict with a `type` key and effect-specific
    parameters. Unknown types are silently skipped.

    Supported types:
        highpass    — cutoff_hz (default 80)
        lowpass     — cutoff_hz (default 8000)
        compressor  — threshold_db, ratio, attack_ms, release_ms
        reverb      — room_size, wet_level, dry_level
        noise_gate  — threshold_db, release_ms
        eq          — low_gain_db, mid_gain_db, high_gain_db
        limiter     — threshold_db, lookahead_ms, release_ms (see peak_limit)
        leveler     — target_db, window_ms, amount, max_gain_db, smooth_ms
                      (see level_speech)
    """
    if not chain:
        return audio_tensor

    if any(str(fx.get("type", "")).lower() in _NUMPY_STAGES for fx in chain):
        # Leveler and limiter are pure numpy, so run the chain in order:
        # pedalboard segments between them go through this function again.
        pending: list[dict] = []
        for fx in chain:
            t = str(fx.get("type", "")).lower()
            if t not in _NUMPY_STAGES:
                pending.append(fx)
                continue
            audio_tensor = apply_effects_chain(audio_tensor, sample_rate, pending)
            pending = []
            stage, names = _NUMPY_STAGES[t]
            try:
                audio_tensor = stage(audio_tensor, sample_rate,
                                     **{k: fx[k] for k in names if k in fx})
            except Exception as e:  # noqa: BLE001 — a DSP stage must never fail a take
                logger.warning("%s failed: %s — skipped", t, e)
        return apply_effects_chain(audio_tensor, sample_rate, pending)

    try:
        from pedalboard import (
            Pedalboard,
            Compressor,
            Reverb,
            HighpassFilter,
            LowpassFilter,
            NoiseGate,
            LowShelfFilter,
            HighShelfFilter,
            PeakFilter,
        )
        import numpy as np
    except ImportError:
        logger.debug("pedalboard not installed — effects chain skipped")
        return audio_tensor

    plugins = []
    for fx in chain:
        t = fx.get("type", "").lower()
        try:
            if t == "highpass":
                plugins.append(HighpassFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 80)))
            elif t == "lowpass":
                plugins.append(LowpassFilter(cutoff_frequency_hz=fx.get("cutoff_hz", 8000)))
            elif t == "compressor":
                plugins.append(Compressor(
                    threshold_db=fx.get("threshold_db", -15),
                    ratio=fx.get("ratio", 2.0),
                    attack_ms=fx.get("attack_ms", 5),
                    release_ms=fx.get("release_ms", 100),
                ))
            elif t == "reverb":
                plugins.append(Reverb(
                    room_size=fx.get("room_size", 0.2),
                    wet_level=fx.get("wet_level", 0.1),
                    dry_level=fx.get("dry_level", 0.9),
                ))
            elif t == "noise_gate":
                plugins.append(NoiseGate(
                    threshold_db=fx.get("threshold_db", -40),
                    release_ms=fx.get("release_ms", 200),
                ))
            elif t == "eq":
                low = fx.get("low_gain_db", 0)
                mid = fx.get("mid_gain_db", 0)
                high = fx.get("high_gain_db", 0)
                if low:
                    plugins.append(LowShelfFilter(cutoff_frequency_hz=250, gain_db=low))
                if mid:
                    plugins.append(PeakFilter(cutoff_frequency_hz=1500, gain_db=mid, q=1.0))
                if high:
                    plugins.append(HighShelfFilter(cutoff_frequency_hz=4000, gain_db=high))
            else:
                logger.debug("Unknown effect type: %s — skipped", t)
        except Exception as e:
            logger.warning("Failed to create %s effect: %s", t, e)

    if not plugins:
        return audio_tensor

    board = Pedalboard(plugins)
    audio_np = audio_tensor.cpu().numpy()
    if audio_np.ndim == 1:
        audio_np = audio_np[None, :]
    try:
        effected = board(audio_np, sample_rate, reset=False)
        return torch.from_numpy(effected).to(audio_tensor.device)
    except Exception as e:
        logger.warning("Effects chain failed: %s — returning unmodified audio", e)
        return audio_tensor



# ── Pause fill (#3611) ──────────────────────────────────────────────────
#
# ``[pause]`` markers and audiobook line gaps used to be exact digital zeros.
# Cloned speech carries a quiet noise floor, so the background dropped to true
# silence at every gap and came back with the next line: on headphones that
# sounds gated, and audiobook platforms reject it. "Room tone" fills the gap
# with noise shaped like the take's own quietest frames instead. Digital
# silence stays the default.

PAUSE_FILL_PREF = "pause_fill"
PAUSE_FILL_ENV = "OMNIVOICE_PAUSE_FILL"
PAUSE_FILL_MODES = ("silence", "room_tone")
_TONE_FRAME = 1024
_TONE_FADE_S = 0.04
#: Frames at or below this are digital silence or a trimmed fade, not room tone.
_TONE_SILENT_DB = -90.0
#: A "floor" louder than this is speech, not room tone: fall back to silence.
_TONE_MAX_DB = -30.0
_TONE_SEED = 3611


def pause_fill_mode() -> str:
    """The configured fill for pauses: env > Settings > ``silence``."""
    try:
        from core import prefs

        value = prefs.resolve(PAUSE_FILL_PREF, env=PAUSE_FILL_ENV, default="silence")
    except Exception:  # noqa: BLE001 - an unreadable setting keeps the default
        return "silence"
    value = str(value or "").strip().lower()
    return value if value in PAUSE_FILL_MODES else "silence"


def _room_tone_profile(spans: list):
    """``(magnitude spectrum, rms)`` of the quietest non-silent frames, or None.

    The frames come from the synthesized speech itself, so the fill matches
    the floor the listener hears between words. None when the take has no
    usable quiet frames (too short, fully silent, or no quiet floor at all).
    """
    import numpy as np

    blocks = []
    for audio in spans:
        x = audio.detach().to(torch.float32).cpu().numpy()
        if x.ndim > 1:
            x = x.reshape(-1, x.shape[-1]).mean(axis=0)
        n = x.shape[0] // _TONE_FRAME
        if n:
            blocks.append(x[: n * _TONE_FRAME].reshape(n, _TONE_FRAME))
    if not blocks:
        return None
    frames = np.concatenate(blocks).astype(np.float64)
    rms = np.sqrt((frames ** 2).mean(axis=1))
    live = rms > 10 ** (_TONE_SILENT_DB / 20)
    if int(live.sum()) < 4:
        return None
    quiet = frames[live & (rms <= np.percentile(rms[live], 15))]
    if quiet.shape[0] < 2:
        return None
    level = float(np.sqrt((quiet ** 2).mean()))
    if level <= 0 or level > 10 ** (_TONE_MAX_DB / 20):
        return None
    window = np.hanning(_TONE_FRAME)
    magnitude = np.abs(np.fft.rfft(quiet * window, axis=1)).mean(axis=0)
    return magnitude, level


def _room_tone(profile, n: int, seed: int):
    """``n`` samples of seeded noise with the profile's spectrum and level."""
    import numpy as np

    magnitude, level = profile
    spectrum = np.fft.rfft(np.random.default_rng(seed).standard_normal(n))
    shape = np.interp(
        np.linspace(0.0, 1.0, spectrum.shape[0]),
        np.linspace(0.0, 1.0, magnitude.shape[0]),
        magnitude,
    )
    noise = np.fft.irfft(spectrum * shape, n)
    rms = float(np.sqrt((noise ** 2).mean())) if n else 0.0
    return noise * (level / rms) if rms > 0 else noise


def fill_gaps(items: list, sample_rate: int, mode: str | None = None) -> list:
    """Turn ``[("a", tensor) | ("s", n_samples), ...]`` into tensors to concatenate.

    ``silence`` inserts zeros shaped like the first audio item (the previous
    behaviour). ``room_tone`` inserts noise shaped like the take's quiet
    frames, faded in over the last 40 ms of the line before the gap and out
    over the first 40 ms of the line after it, because trimmed line edges
    already fade to zero. It falls back to silence when the take has no
    usable floor. The output is deterministic for the same input.
    """
    import numpy as np

    mode = pause_fill_mode() if mode is None else mode
    ref = next((t for kind, t in items if kind == "a"), None)
    if ref is None:
        return [torch.zeros(n, dtype=torch.float32) for kind, n in items if kind == "s"]
    # Adjacent gaps are one gap: one noise run, one pair of fades.
    merged: list = []
    for kind, val in items:
        if kind == "s" and merged and merged[-1][0] == "s":
            merged[-1] = ("s", merged[-1][1] + val)
        else:
            merged.append((kind, val))
    profile = None
    if mode == "room_tone":
        profile = _room_tone_profile([t for kind, t in merged if kind == "a"])
    parts = [val for _kind, val in merged]
    fade_max = int(sample_rate * _TONE_FADE_S)
    for i, (kind, n) in enumerate(merged):
        if kind != "s":
            continue
        shape = list(ref.shape[:-1]) + [n]
        if profile is None:
            parts[i] = torch.zeros(*shape, dtype=ref.dtype, device=ref.device)
            continue
        prev = parts[i - 1] if i > 0 and merged[i - 1][0] == "a" else None
        nxt = parts[i + 1] if i + 1 < len(merged) and merged[i + 1][0] == "a" else None
        lead = min(fade_max, prev.shape[-1]) if prev is not None else 0
        tail = min(fade_max, nxt.shape[-1]) if nxt is not None else 0
        noise = _room_tone(profile, lead + n + tail, _TONE_SEED + i)
        if prev is not None and lead:
            ramp = np.linspace(0.0, 1.0, lead, endpoint=False)
            prev = prev.clone()
            prev[..., -lead:] += torch.as_tensor(noise[:lead] * ramp, dtype=prev.dtype, device=prev.device)
            parts[i - 1] = prev
        gap = torch.as_tensor(noise[lead:lead + n], dtype=ref.dtype, device=ref.device)
        parts[i] = gap.expand(*shape).clone() if len(shape) > 1 else gap
        if nxt is not None and tail:
            ramp = np.linspace(1.0, 0.0, tail, endpoint=False)
            nxt = nxt.clone()
            nxt[..., :tail] += torch.as_tensor(noise[lead + n:] * ramp, dtype=nxt.dtype, device=nxt.device)
            parts[i + 1] = nxt
    return parts
