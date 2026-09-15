"""
Audio DSP pipeline — broadcast-grade mastering + configurable effects chain.

`apply_mastering()` is the shared pre-stage that runs before the user's
effect preset: highpass + gentle compression only (see `MASTERING_CHAIN`).
Reverb is deliberately NOT part of it — it is preset-declared only (e.g.
cinematic, warm); a hidden reverb here used to bake echo into every non-raw
synthesis, which field reports flagged. `apply_effects_chain()` lets callers
build custom pipelines from a list of named effects.

All effects use Spotify's `pedalboard` library. When pedalboard isn't
installed, every function degrades gracefully (returns audio unmodified).
"""
import logging
import torch

logger = logging.getLogger("omnivoice.dsp")

# ── Effect presets ──────────────────────────────────────────────────────

EFFECT_PRESETS = {
    "broadcast": {
        "label": "Broadcast",
        "icon": "📻",
        "description": "Radio/podcast standard — warm, compressed, clear.",
        "chain": [
            {"type": "highpass", "cutoff_hz": 80},
            {"type": "compressor", "threshold_db": -18, "ratio": 3.0, "attack_ms": 5, "release_ms": 80},
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
        limiter     — threshold_db
    """
    if not chain:
        return audio_tensor

    try:
        from pedalboard import (
            Pedalboard,
            Compressor,
            Reverb,
            HighpassFilter,
            LowpassFilter,
            NoiseGate,
            Limiter,
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
            elif t == "limiter":
                plugins.append(Limiter(threshold_db=fx.get("threshold_db", -1.0)))
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

