"""Quiet-reference silence-removal ladder + actionable empty-clip error (#1188).

`remove_silence` detects speech against a fixed dBFS threshold (historically a
hardcoded -50). A quiet-but-real recording — laptop mic far from the speaker,
low input gain — sits entirely below that threshold and was removed wholesale,
so `create_voice_clone_prompt` dead-ended with the user-facing 400
"Reference audio is empty after silence removal. Try setting
preprocess_prompt=False." (which names an API kwarg the desktop UI doesn't
even expose).

The fix (fail-before/pass-after — `remove_silence_safe` and
`validate_clone_reference` did not exist before it):
  * `remove_silence_safe` retries with progressively gentler thresholds
    (-50 → -60 → -70 dBFS) and, when every rung would still consume the clip,
    skips trimming entirely — a quiet real recording clones instead of 400ing.
  * `validate_clone_reference` raises the one remaining hard failure — a clip
    with genuinely no audio (empty / digital silence / NaN) — with the
    machine-readable ``[clone_ref_unusable]`` marker the frontend maps to a
    localized, actionable message (electron/src/shared/utils/errorToast.jsx →
    tts_errors.ref_audio_unusable).

All clone producers share this path (`create_voice_clone_prompt` is the single
chokepoint: /generate inline refs, the tts_backend prompt cache, and dubbing's
per-speaker/per-segment voice profiles), so covering it here covers the class.

Pure synthetic tensors — no model load, no file I/O.
"""
import math

import pytest
import torch

from omnivoice.utils.audio import (
    CLONE_REF_UNUSABLE_MARKER,
    remove_silence,
    remove_silence_safe,
    validate_clone_reference,
)

SR = 24000


def _padded_tone(amplitude, *, speech_s=2.0, pad_s=0.7, sr=SR):
    """``pad_s`` digital silence + ``speech_s`` of a 220 Hz sine at
    ``amplitude`` + ``pad_s`` silence, shape (1, T). A pure sine keeps every
    10 ms analysis window at the same dBFS, so threshold behavior is exact:
    window level = 20*log10(amplitude / sqrt(2))."""
    t = torch.arange(int(speech_s * sr), dtype=torch.float32) / sr
    tone = amplitude * torch.sin(2 * math.pi * 220.0 * t)
    pad = torch.zeros(int(pad_s * sr), dtype=torch.float32)
    return torch.cat([pad, tone, pad]).unsqueeze(0)


# Window levels: NORMAL ≈ -13.5 dBFS (well above -50), QUIET ≈ -53.5 dBFS
# (below the historical -50, above the -60 rung), ULTRA ≈ -73.5 dBFS (below
# every rung, including -70).
NORMAL_AMP = 0.3
QUIET_AMP = 0.003
ULTRA_QUIET_AMP = 0.0003


# ── root cause, pinned ──────────────────────────────────────────────────────


def test_remove_silence_at_default_threshold_consumes_quiet_clip():
    """The bug mechanism: at the historical -50 dBFS threshold a quiet-but-real
    recording is 100% 'silence' and the whole clip is removed. This is what
    used to surface as the dead-end 400 in issue #1188."""
    clip = _padded_tone(QUIET_AMP)
    out = remove_silence(clip, SR, mid_sil=200, lead_sil=100, trail_sil=200)
    assert out.size(-1) == 0


# ── the retry ladder ────────────────────────────────────────────────────────


def test_safe_trims_normal_clip_like_before():
    """Healthy path unchanged: normal-level audio is trimmed on the first rung
    (edge padding removed, speech kept) — no behavior regression."""
    clip = _padded_tone(NORMAL_AMP)
    out = remove_silence_safe(clip, SR, mid_sil=200, lead_sil=100, trail_sil=200)
    assert 0 < out.size(-1) < clip.size(-1)  # trimmed, not consumed
    assert out.size(-1) >= int(1.5 * SR)     # the speech itself survived


def test_safe_recovers_quiet_clip_via_gentler_threshold():
    """A quiet-but-real clip that the -50 rung consumes is recovered by the
    -60 rung — trimmed, non-empty, speech intact. Failed (function absent,
    caller 400ed) before the fix."""
    clip = _padded_tone(QUIET_AMP)
    out = remove_silence_safe(clip, SR, mid_sil=200, lead_sil=100, trail_sil=200)
    assert out.size(-1) >= int(1.5 * SR)     # kept the speech
    assert out.size(-1) < clip.size(-1)      # still actually trimmed the pads


def test_safe_skips_trimming_when_every_rung_fails():
    """Below every threshold rung, trimming is skipped entirely: the input
    comes back unchanged — an untrimmed real recording beats an empty one."""
    clip = _padded_tone(ULTRA_QUIET_AMP)
    out = remove_silence_safe(clip, SR, mid_sil=200, lead_sil=100, trail_sil=200)
    assert torch.equal(out, clip)


def test_safe_never_returns_empty_for_silent_input():
    """Even pure digital silence comes back unchanged (the hard failure is
    raised by validate_clone_reference, with guidance — never by trimming)."""
    silence = torch.zeros(1, 2 * SR)
    out = remove_silence_safe(silence, SR, mid_sil=200, lead_sil=100, trail_sil=200)
    assert torch.equal(out, silence)
    empty = torch.zeros(1, 0)
    assert remove_silence_safe(empty, SR).size(-1) == 0  # degenerate: no crash


# ── the final, actionable error ─────────────────────────────────────────────


def _rms(wav: torch.Tensor) -> float:
    return torch.sqrt(torch.mean(torch.square(wav))).item()


def test_validate_rejects_digital_silence_with_actionable_marker():
    silence = torch.zeros(1, 2 * SR)
    with pytest.raises(ValueError) as exc:
        validate_clone_reference(silence, _rms(silence))
    msg = str(exc.value)
    # The marker is the frontend's localization hook (errorToast.jsx) — it and
    # the concrete user fix must both survive any rewording.
    assert CLONE_REF_UNUSABLE_MARKER in msg
    assert "[clone_ref_unusable]" in msg
    assert "microphone" in msg
    # The obsolete dead-end advice must be gone: trimming now degrades
    # automatically, so preprocess_prompt=False is never the user's fix.
    assert "preprocess_prompt" not in msg


def test_validate_rejects_empty_and_nonfinite_clips():
    empty = torch.zeros(1, 0)
    with pytest.raises(ValueError, match=r"\[clone_ref_unusable\]"):
        validate_clone_reference(empty, float("nan"))  # rms of empty is nan
    nan_clip = torch.full((1, SR), float("nan"))
    with pytest.raises(ValueError, match=r"\[clone_ref_unusable\]"):
        validate_clone_reference(nan_clip, _rms(nan_clip))


def test_validate_accepts_quiet_but_real_clip():
    """A quiet real recording is NOT a hard failure — it must proceed (and be
    RMS-boosted + gently trimmed) rather than 400. Raised before the fix."""
    validate_clone_reference(_padded_tone(QUIET_AMP), _rms(_padded_tone(QUIET_AMP)))
    validate_clone_reference(_padded_tone(NORMAL_AMP), _rms(_padded_tone(NORMAL_AMP)))
