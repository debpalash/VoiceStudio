"""#119 — audio-only dubbing export.

The audio-only branch of /dub/download builds a simple ffmpeg command that
muxes the dubbed track (optionally mixed with the separated background) into an
audio container — no video input, no video codec/stream-map/subtitle pass.
"""
from __future__ import annotations

from api.routers.dub_export import _build_audio_export_cmd


def _flat(cmd):
    return " ".join(cmd)


def test_wav_track_only_no_video():
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", None, "/j/out.wav", "wav")
    s = _flat(cmd)
    # the dubbed track is the only input; never the source media / a video map
    assert cmd.count("-i") == 1
    assert "/j/dubbed_de.wav" in s
    assert "-map" not in s or "0:v" not in s  # no video stream mapping
    assert "-c:v" not in s                    # no video codec
    assert "pcm_s16le" in s                   # wav → PCM
    assert cmd[-1] == "/j/out.wav"


def test_m4a_uses_aac():
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", None, "/j/out.m4a", "m4a")
    s = _flat(cmd)
    assert "aac" in s
    assert "-c:v" not in s


def test_mp3_uses_lame():
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", None, "/j/out.mp3", "mp3")
    assert "libmp3lame" in _flat(cmd)


def test_background_mix_adds_amix_and_second_input():
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", "/j/no_vocals.wav", "/j/out.m4a", "m4a")
    s = _flat(cmd)
    assert cmd.count("-i") == 2              # track + background
    assert "/j/no_vocals.wav" in s
    assert "amix" in s                       # mixed, not just concatenated
    assert "-filter_complex" in s


def test_unknown_format_falls_back_to_aac_m4a():
    # Defensive: an unexpected format string must not produce a broken command.
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", None, "/j/out.bin", "weird")
    assert "aac" in _flat(cmd)


def test_background_mix_preserves_bed_level_and_bandwidth(monkeypatch):
    """The two fidelity bugs that made dub music 'not sound like the original':

    1. amix normalizes each input — the old weight strings left the music bed
       at ~57% of its original level (measured). On modern ffmpeg the chain
       must disable normalization outright (normalize=0 + per-input gains);
       amix's normalization is DYNAMIC, so the naive fix (a constant post-mix
       compensation) over-boosts the bed after the voice stream ends.
    2. amix negotiates one common rate; against a 24 kHz voice track the
       44.1 kHz bed was silently downsampled — everything above 12 kHz gone.
       Both inputs must be resampled UP to the mix rate before amix.
    """
    import services.ffmpeg_utils as fu

    # Patch the globals dict the CALL CHAIN actually reads. Several suites
    # purge/reload sys.modules["services.*"] and "api.routers.*", so under
    # random test order a freshly-imported module object and the function this
    # file bound at collection time can disagree — patching either module by
    # name then misses (the test_clone_prompt_wiring stale-alias class).
    # Following __globals__ from the function under test cannot miss.
    _bed_mix = _build_audio_export_cmd.__globals__["bed_mix_filter"]
    monkeypatch.setitem(_bed_mix.__globals__, "_AMIX_NORMALIZE", True)
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", "/j/no_vocals.wav", "/j/out.m4a", "m4a")
    s = _flat(cmd)
    assert f"aresample={fu.BED_MIX_SAMPLE_RATE}" in s, "bed bandwidth collapses to the 24kHz voice rate"
    assert "normalize=0" in s, "amix normalization not disabled — bed level depends on stream lifetimes"
    assert "volume=1[" in s and f"volume={fu.VOICE_GAIN:g}" in s
    assert fu.BED_GAIN >= 0.85, "bed gain drifted away from 'almost like the original'"
    assert "alimiter" in s  # full-scale mixing needs the peak guard


def test_background_mix_legacy_ffmpeg_fallback(monkeypatch):
    """ffmpeg <5 has no amix `normalize` option — the graph must fall back to
    the compensated form (weights + post-multiply) instead of failing whole
    exports on a rejected option."""
    import services.ffmpeg_utils as fu

    _bed_mix = _build_audio_export_cmd.__globals__["bed_mix_filter"]
    monkeypatch.setitem(_bed_mix.__globals__, "_AMIX_NORMALIZE", False)
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", "/j/no_vocals.wav", "/j/out.m4a", "m4a")
    s = _flat(cmd)
    assert "normalize=0" not in s
    assert f"volume={fu.BED_GAIN + fu.VOICE_GAIN:g}" in s  # the compensation multiply
    assert "alimiter" in s


def test_bed_mix_filter_uniq_labels_do_not_collide():
    """Several chains can share one filtergraph (multi-track mux) — internal
    labels must be disambiguated or ffmpeg rejects the graph."""
    from services.ffmpeg_utils import bed_mix_filter

    a = bed_mix_filter("0:a", "1:a", out="aout0", uniq="0")
    b = bed_mix_filter("0:a", "2:a", out="aout1", uniq="1")
    import re
    labels_a = set(re.findall(r"\[(bm[bv]\d*)\]", a))
    labels_b = set(re.findall(r"\[(bm[bv]\d*)\]", b))
    assert labels_a and labels_b and not (labels_a & labels_b)


def test_background_mix_preserves_stereo_width():
    """The synthesized voice is MONO; amix negotiates one layout for all
    inputs, so without an explicit stereo aformat on both legs the stereo
    music bed collapsed to mono (measured: L/R correlation 1.000 vs the
    original's 0.754 — the whole stereo image gone)."""
    cmd = _build_audio_export_cmd("ffmpeg", "/j/dubbed_de.wav", "/j/no_vocals.wav", "/j/out.m4a", "m4a")
    s = _flat(cmd)
    assert s.count("aformat=channel_layouts=stereo") == 2, (
        "both amix legs must be stereo or the bed loses its width"
    )


def test_segments_text_endpoint_serves_i18n_map():
    """Export preview tabs hydrate missing per-segment translations from
    segments_i18n — the authoritative per-language store (#1148 review)."""
    import asyncio
    from api.routers import dub_export as de
    from unittest.mock import patch

    job = {"segments_i18n": {"de": {"1": "die Zeile"}, "bn": {"1": "লাইন"}}}
    with patch.object(de, "_get_job", lambda jid: job):
        out = asyncio.run(de.dub_segments_text("j1", lang="de"))
        assert out == {"texts": {"1": "die Zeile"}}
        out = asyncio.run(de.dub_segments_text("j1", lang="fr"))
        assert out == {"texts": {}}  # never-generated track → empty, not error

    with patch.object(de, "_get_job", lambda jid: {"segments": []}):
        out = asyncio.run(de.dub_segments_text("j1", lang="de"))
        assert out == {"texts": {}}  # legacy job predating segments_i18n
