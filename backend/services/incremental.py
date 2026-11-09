"""
Incremental re-dub — Phase 4.1 (ROADMAP.md), the other defensible bet.

Given the current dub state + a prior state, compute the minimum set of
segments whose audio needs regenerating. A segment needs re-gen when any of
its *generation inputs* changed:

    text · target_lang · profile_id · instruct · speed · direction

Deterministic content hash per segment is the key. We store the hash on the
segment after every successful generation; when re-dubbing, we hash again and
only queue segments whose hash differs from the stored one.

This isn't full crossfade-at-the-edges yet — that comes with Phase 4.5. For
now the caller re-runs `/dub/generate` with a filtered segments list.
"""
from __future__ import annotations

import hashlib
import json


_GEN_INPUT_FIELDS = ("text", "target_lang", "profile_id", "instruct", "speed", "direction", "effect_preset")

# Pydantic fills `effect_preset` with this default when the client omits it,
# while the client-side recompute (/tools/incremental) sends nothing. Both
# representations must hash identically or every segment looks "stale" after
# every generate and incremental re-dub degrades to a full re-dub (#281).
_DEFAULT_EFFECT_PRESET = "broadcast"


def _canon_value(field: str, value):
    """Normalise one generation-input value so that the server-side view
    (pydantic-parsed `DubSegment`, defaults filled in) and the client-side
    view (raw segment dict, unset keys omitted) produce the same hash.

    - missing / None / "" all mean "default" for string fields
    - `effect_preset` default is "broadcast" (pydantic fills it server-side)
    - numbers are coerced to float so `speed: 1` (JS) == `speed: 1.0` (pydantic)
    """
    if field == "effect_preset":
        return value or _DEFAULT_EFFECT_PRESET
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return float(value)
    return value


def segment_fingerprint(
    seg: dict, track_lang: str | None = None, voice_match: str | None = None
) -> str:
    """Deterministic hash of the inputs that actually affect TTS output.

    Any change to `_GEN_INPUT_FIELDS` flips the hash and the segment becomes
    a re-gen candidate. Changes to position / selection state / lip-sync
    badge don't trigger regen, which is what we want.

    Currently includes: text, target_lang, profile_id, instruct, speed,
    direction, effect_preset. Values are canonicalised (see `_canon_value`)
    so a fingerprint computed from the generate request (server defaults
    filled in) matches one recomputed later from the client's raw segment
    state — the root cause of #281's "1 edit re-dubs all N lines".

    ``track_lang`` (P1.3) is the TRACK's language code (`req.language_code`,
    e.g. "es"). It is part of the fingerprint because the same segment text
    renders different audio per language — without it, a bn hash could
    vouch for an es WAV on a multi-track job. It is only mixed in when
    provided, so hashes computed by legacy callers (and hashes stored by
    previous builds, which never carried a language) keep their old values;
    a legacy hash therefore never matches a lang-scoped fingerprint and the
    segment reads as stale — the safe direction (one clean regen, never a
    wrong-language splice).

    ``voice_match`` is the job-level voice-identity mode (DubRequest.voice_match).
    "consistent" resolves `auto:`/default `auto-seg:` bindings to a different
    reference than "per_line" does, so audio rendered under one mode must not
    vouch for the other — flipping the toggle has to mark segments stale, or
    "Regen changed" would splice mixed-identity voices (#281 class). Same
    back-compat trick as ``track_lang``: only mixed in when NON-DEFAULT, so
    every hash stored by previous builds (and by per_line runs) keeps its
    value and per_line stays byte-identical to the pre-toggle behaviour.
    """
    payload = {k: _canon_value(k, seg.get(k)) for k in _GEN_INPUT_FIELDS}
    if track_lang:
        payload["track_lang"] = str(track_lang)
    if voice_match and voice_match != "per_line":
        payload["voice_match"] = str(voice_match)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


# ── Smart Fit (dub-length fitting v2) fingerprints ─────────────────────────
#
# Fitting parameters stay OUT of segment_fingerprint on purpose: changing a
# fit knob (caps, gap guard, strategy) must trigger a RE-MIX of the already
# rendered natural-rate WAVs (generate with regen_only=[]), never a re-TTS.
# A separate per-track fingerprint tracks the fit configuration; a dubbed
# track is stale iff its fit_fp differs OR any segment hash differs.

_FIT_PARAM_FIELDS = (
    "timing_strategy",
    "max_audio_only_rate",
    "audio_rate_cap",
    "video_slow_cap",
    "gap_guard_s",
    "allow_video_retime",
)

# Server-side defaults (must mirror services.fit_planner.FitParams). Filled
# in for omitted keys so a fingerprint computed from a fully-populated
# server view matches one recomputed from a sparse client payload — the
# same #281 regression class segment_fingerprint already guards against.
_FIT_PARAM_DEFAULTS = {
    "timing_strategy": "smart_fit",
    "max_audio_only_rate": 1.2,
    "audio_rate_cap": 1.5,
    "video_slow_cap": 2.0,
    "gap_guard_s": 0.05,
    "allow_video_retime": True,
}


_MIN_AUDIO_RATE_DEFAULT = 0.85  # mirrors FitParams.min_audio_rate


def fit_fingerprint(params: dict) -> str:
    """Deterministic hash of the fit configuration for one dub track.

    Canonicalised with the same `_canon_value` rules as segment
    fingerprints (int vs float, None/"" vs omitted), plus default-filling
    so `{}` and `{"audio_rate_cap": 1.5}` hash identically.
    """
    params = params or {}
    payload = {}
    for k in _FIT_PARAM_FIELDS:
        v = params.get(k)
        if v is None or v == "":
            v = _FIT_PARAM_DEFAULTS[k]
        payload[k] = _canon_value(k, v)
    # Added after tracks were already fingerprinted: hashed only when set to a
    # non-default value, so existing tracks are not flagged stale by an upgrade.
    min_rate = params.get("min_audio_rate")
    if min_rate not in (None, "") and float(min_rate) != _MIN_AUDIO_RATE_DEFAULT:
        payload["min_audio_rate"] = _canon_value("min_audio_rate", min_rate)
    blob = json.dumps(payload, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(blob.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]


def plan_incremental(
    segments: list[dict],
    *,
    stored_hashes: dict[str, str] | None = None,
    track_lang: str | None = None,
    voice_match: str | None = None,
) -> dict:
    """Return `{stale, fresh, total, fingerprints}` where:

    • `stale`       : list of segment ids whose generation inputs changed
      since the last successful generate (i.e. need re-dub).
    • `fresh`       : ids whose stored hash still matches current inputs —
      safe to reuse the prior audio.
    • `fingerprints`: {id: sha1} for every segment, for caller to persist
      after a successful regen.

    `stored_hashes` may come from the caller's own bookkeeping (e.g. the
    `dub_history.job_data["seg_hashes"]` we'll start writing in Phase 4.5).
    When missing, every segment is considered stale (first run).

    `track_lang` (P1.3) scopes the plan to ONE dub track: pass the track's
    language code together with THAT language's stored hashes
    (`job_data["seg_hashes_by_lang"][lang]`) so staleness is judged against
    the active track, never against whatever language was generated last.
    Must match the language the generate run hashed with, or every segment
    reads stale (#281 parity class).

    `voice_match` must likewise match the mode the generate run hashed with
    (send the store's current voice-match mode); omitted/`"per_line"` hashes
    identically to legacy calls.
    """
    stored = stored_hashes or {}
    stale: list[str] = []
    fresh: list[str] = []
    fingerprints: dict[str, str] = {}
    for seg in segments:
        sid = str(seg.get("id", ""))
        if not sid:
            continue
        fp = segment_fingerprint(seg, track_lang=track_lang, voice_match=voice_match)
        fingerprints[sid] = fp
        prev = stored.get(sid)
        if prev == fp:
            fresh.append(sid)
        else:
            stale.append(sid)
    return {
        "stale": stale,
        "fresh": fresh,
        "total": len(segments),
        "fingerprints": fingerprints,
    }
