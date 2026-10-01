"""Shared long-form render core (Stories + Audiobook convergence).

Pure builders for the chapterized mux: FFMETADATA (global tags + chapters),
concat list, loudness filter, cover validation, and the ffmpeg render argv.
All unit-testable without ffmpeg/torch/GPU.
"""
from __future__ import annotations

import pytest

from services.longform_render import (
    LOUDNESS_PRESETS,
    MeasuredLoudness,
    build_concat_list,
    build_ffmetadata,
    build_loudnorm_apply_filter,
    build_loudnorm_filter,
    build_loudnorm_measure_cmd,
    build_loudnorm_measure_filter,
    build_render_cmd,
    chapter_cache_key,
    parse_loudnorm_measure,
    prune_cache_dir,
    validate_cover_image,
)

# A verified ffmpeg loudnorm measure-JSON fixture (n8.1.1 shape).
_MEASURE_JSON = """[Parsed_loudnorm_0 @ 0x55]
{
    "input_i" : "-21.75",
    "input_tp" : "-18.06",
    "input_lra" : "0.00",
    "input_thresh" : "-31.75",
    "output_i" : "-19.02",
    "output_tp" : "-3.01",
    "normalization_type" : "dynamic",
    "target_offset" : "0.05"
}
[out#0/null @ 0x66] video:0kB audio:1kB
size=N/A time=00:00:10
"""
_MEASURED = MeasuredLoudness(input_i=-21.75, input_tp=-18.06, input_lra=0.0,
                             input_thresh=-31.75, target_offset=0.05)


# ── loudness ────────────────────────────────────────────────────────────────

def test_loudnorm_acx_filter():
    f = build_loudnorm_filter("acx")
    assert f == "loudnorm=I=-19.0:TP=-3.0:LRA=11.0"


def test_loudnorm_podcast_filter():
    assert build_loudnorm_filter("podcast") == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0"


def test_loudnorm_case_insensitive():
    assert build_loudnorm_filter("ACX") == build_loudnorm_filter("acx")


@pytest.mark.parametrize("val", [None, "", "off", "none", "bogus"])
def test_loudnorm_off_or_unknown_is_none(val):
    assert build_loudnorm_filter(val) is None


def test_loudness_presets_within_acx_window():
    # ACX wants integrated near -19 LUFS and a -3 dB peak ceiling.
    acx = LOUDNESS_PRESETS["acx"]
    assert -23.0 <= acx.i <= -18.0
    assert acx.tp == -3.0


# ── FFMETADATA ──────────────────────────────────────────────────────────────

def test_ffmetadata_chapters_only_matches_legacy_shape():
    doc = build_ffmetadata([("One", 1000), ("Two", 500)])
    assert doc.startswith(";FFMETADATA1\n")
    assert "[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=One" in doc
    assert "START=1000\nEND=1500\ntitle=Two" in doc
    # No global tags when none supplied.
    assert "artist=" not in doc


def test_ffmetadata_global_tags_mapped_and_ordered():
    doc = build_ffmetadata(
        [("Ch", 1000)],
        global_meta={
            "title": "My Book", "author": "Ada", "narrator": "Grace",
            "year": "2026", "genre": "Sci-Fi", "description": "A tale",
        },
    )
    # field → tag mapping (author→artist, narrator→composer, year→date,
    # description→comment) and stable order (title before artist).
    head = doc.split("[CHAPTER]")[0]
    assert head.index("title=My Book") < head.index("artist=Ada")
    assert "composer=Grace" in head
    assert "date=2026" in head
    assert "genre=Sci-Fi" in head
    assert "comment=A tale" in head


def test_ffmetadata_skips_empty_global_values():
    doc = build_ffmetadata([("Ch", 1)], global_meta={"title": "T", "author": "  ", "genre": None})
    head = doc.split("[CHAPTER]")[0]
    assert "title=T" in head
    assert "artist=" not in head   # whitespace-only dropped
    assert "genre=" not in head    # None dropped


def test_ffmetadata_escapes_special_chars():
    doc = build_ffmetadata([("a=b;c#d", 100)], global_meta={"title": "x=y"})
    assert r"title=x\=y" in doc
    assert r"title=a\=b\;c\#d" in doc


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
def test_ffmetadata_normalizes_line_endings_before_escaping(newline):
    value = newline.join(["Opening =;#\\ paragraph.", "Closing paragraph."])
    expected = build_ffmetadata([("Opening =;#\\ paragraph.\nClosing paragraph.", 1000)],
                                {"description": "Opening =;#\\ paragraph.\nClosing paragraph."})
    assert build_ffmetadata([(value, 1000)], {"description": value}) == expected
    assert "\r" not in expected


@pytest.mark.parametrize("newline", ["\n", "\r\n", "\r"])
@pytest.mark.parametrize("fmt", ["m4b", "mp3"])
def test_rendered_metadata_retains_description_paragraphs(tmp_path, newline, fmt):
    """Exercise the shipped chapter-WAV → mux path without synthesis."""
    import json
    import subprocess
    import wave

    from services.ffmpeg_utils import find_ffmpeg, find_ffprobe

    ffmpeg, ffprobe = find_ffmpeg(), find_ffprobe()
    if not ffmpeg or not ffprobe:
        pytest.skip("ffmpeg and ffprobe required for metadata mux round trip")
    audio = tmp_path / "chapter.wav"
    with wave.open(str(audio), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\0\0" * 16000)
    concat = tmp_path / "chapters.txt"
    concat.write_text(build_concat_list([str(audio)]), encoding="utf-8")
    metadata = tmp_path / "chapters.ffmeta"
    metadata.write_text(build_ffmetadata([("Chapter one", 1000)], {
        "title": "My =;#\\ book",
        "description": newline.join(["Opening =;#\\ paragraph.", "Closing paragraph."]),
    }), encoding="utf-8", newline="")
    output = tmp_path / f"book.{fmt}"
    subprocess.run(build_render_cmd(ffmpeg, str(concat), str(metadata), str(output), fmt=fmt),
                   check=True, capture_output=True, timeout=30)
    probe = subprocess.run([ffprobe, "-v", "error", "-show_entries", "format_tags=title,comment",
                            "-of", "json", str(output)],
                           check=True, capture_output=True, timeout=30)
    tags = json.loads(probe.stdout)["format"]["tags"]
    assert tags["title"] == "My =;#\\ book"
    assert tags["comment"] == "Opening =;#\\ paragraph.\nClosing paragraph."


# ── concat list ─────────────────────────────────────────────────────────────

def test_concat_list_quotes_and_escapes():
    out = build_concat_list(["/a/one.wav", "/weird/it's here.wav"])
    assert "file '/a/one.wav'" in out
    assert "file '/weird/it'\\''s here.wav'" in out


# ── cover validation ────────────────────────────────────────────────────────

def test_cover_valid(tmp_path):
    p = tmp_path / "cover.jpg"
    p.write_bytes(b"\xff\xd8\xff" + b"x" * 100)
    assert validate_cover_image(str(p)) is True


def test_cover_rejects_missing_and_bad_type(tmp_path):
    assert validate_cover_image(None) is False
    assert validate_cover_image(str(tmp_path / "nope.jpg")) is False
    txt = tmp_path / "c.txt"
    txt.write_bytes(b"hi")
    assert validate_cover_image(str(txt)) is False


def test_cover_rejects_oversize(tmp_path):
    big = tmp_path / "big.png"
    big.write_bytes(b"\x89PNG" + b"0" * (8 * 1024 * 1024 + 1))
    assert validate_cover_image(str(big)) is False


def test_cover_rejects_empty(tmp_path):
    empty = tmp_path / "empty.jpg"
    empty.write_bytes(b"")
    assert validate_cover_image(str(empty)) is False


# ── render command ──────────────────────────────────────────────────────────

def test_render_cmd_m4b_default():
    cmd = build_render_cmd("ffmpeg", "concat.txt", "ch.ffmeta", "out.m4b")
    assert cmd[0] == "ffmpeg"
    assert "-f" in cmd and "concat" in cmd
    assert cmd[-3:] == ["-f", "mp4", "out.m4b"]
    assert "-c:a" in cmd and "aac" in cmd
    assert "+faststart" in cmd
    assert "-map_metadata" in cmd
    # no cover, no loudnorm by default
    assert "attached_pic" not in cmd
    assert "-af" not in cmd


def test_render_cmd_mp3_format():
    cmd = build_render_cmd("ffmpeg", "c.txt", "m.ffmeta", "out.mp3", fmt="mp3")
    assert "libmp3lame" in cmd
    assert cmd[-3:] == ["-f", "mp3", "out.mp3"]
    assert "+faststart" not in cmd


def test_render_cmd_bitrate_validation():
    ok = build_render_cmd("ffmpeg", "c", "m", "o", bitrate="192k")
    assert "192k" in ok
    bad = build_render_cmd("ffmpeg", "c", "m", "o", bitrate="; rm -rf /")
    assert "128k" in bad  # rejected → default
    assert "; rm -rf /" not in bad


def test_render_cmd_loudnorm_adds_af():
    cmd = build_render_cmd("ffmpeg", "c", "m", "o", loudness="acx")
    assert "-af" in cmd
    assert any(a.startswith("loudnorm=") for a in cmd)


def test_render_cmd_with_cover(tmp_path):
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff" + b"x" * 50)
    cmd = build_render_cmd("ffmpeg", "c", "m", "o.m4b", cover_path=str(cover))
    # cover becomes input 2, mapped as attached_pic, copied
    assert str(cover) in cmd
    assert "-map" in cmd and "2:v" in cmd
    assert "attached_pic" in cmd
    assert "-c:v" in cmd and "copy" in cmd


def test_render_cmd_drops_invalid_cover(tmp_path):
    cmd = build_render_cmd("ffmpeg", "c", "m", "o.m4b", cover_path=str(tmp_path / "missing.jpg"))
    assert "attached_pic" not in cmd  # silently dropped, render still proceeds


def test_render_cmd_mp3_never_embeds_cover(tmp_path):
    # A valid cover must NOT be wired into an MP3 (the attached_pic + -c:v copy
    # combo produces a corrupt mp3); m4b is the cover-bearing format.
    cover = tmp_path / "cover.jpg"
    cover.write_bytes(b"\xff\xd8\xff" + b"x" * 50)
    cmd = build_render_cmd("ffmpeg", "c", "m", "o.mp3", fmt="mp3", cover_path=str(cover))
    assert "libmp3lame" in cmd
    assert "attached_pic" not in cmd
    assert str(cover) not in cmd
    assert "-c:v" not in cmd


# ── chapter cache key (resume) ──────────────────────────────────────────────

_SPANS = [(None, "Once upon a time.", 350), ("narrator", "The end.", 0)]


def test_cache_key_deterministic():
    a = chapter_cache_key(_SPANS, sample_rate=24000, engine_id="omnivoice")
    b = chapter_cache_key(list(_SPANS), sample_rate=24000, engine_id="omnivoice")
    assert a == b and len(a) == 20


@pytest.mark.parametrize("mutate", [
    lambda: chapter_cache_key([(None, "Different.", 350), ("narrator", "The end.", 0)],
                              sample_rate=24000, engine_id="omnivoice"),                       # text
    lambda: chapter_cache_key([("x", "Once upon a time.", 350), ("narrator", "The end.", 0)],
                              sample_rate=24000, engine_id="omnivoice"),                       # voice
    lambda: chapter_cache_key([(None, "Once upon a time.", 500), ("narrator", "The end.", 0)],
                              sample_rate=24000, engine_id="omnivoice"),                       # pause
    lambda: chapter_cache_key(list(reversed(_SPANS)), sample_rate=24000, engine_id="omnivoice"),  # order
    lambda: chapter_cache_key(_SPANS, sample_rate=44100, engine_id="omnivoice"),              # sr
    lambda: chapter_cache_key(_SPANS, sample_rate=24000, engine_id="kokoro"),                 # engine
    lambda: chapter_cache_key(_SPANS, sample_rate=24000, engine_id="omnivoice",
                              voice_sig={"narrator": "ref.wav|warm|7"}),                       # voice sig
    lambda: chapter_cache_key([(None, "Once upon a time.", 350, 0.8), ("narrator", "The end.", 0)],
                              sample_rate=24000, engine_id="omnivoice"),                       # speed
])
def test_cache_key_changes_on_any_input(mutate):
    base = chapter_cache_key(_SPANS, sample_rate=24000, engine_id="omnivoice")
    assert mutate() != base


def test_cache_key_voice_sig_order_irrelevant():
    a = chapter_cache_key(_SPANS, sample_rate=24000, engine_id="omnivoice",
                          voice_sig={"a": "1", "b": "2"})
    b = chapter_cache_key(_SPANS, sample_rate=24000, engine_id="omnivoice",
                          voice_sig={"b": "2", "a": "1"})
    assert a == b


# ── cache eviction ──────────────────────────────────────────────────────────

def _seed(tmp_path, name, size, age_s):
    import os, time
    p = tmp_path / name
    p.write_bytes(b"\0" * size)
    t = time.time() - age_s
    os.utime(p, (t, t))
    return p


def test_prune_cache_under_cap_is_noop(tmp_path):
    _seed(tmp_path, "a.wav", 100, 10)
    remaining, removed = prune_cache_dir(str(tmp_path), max_bytes=1000)
    assert removed == 0 and remaining == 100


def test_prune_cache_evicts_oldest_first(tmp_path):
    old = _seed(tmp_path, "old.wav", 600, age_s=100)   # oldest
    new = _seed(tmp_path, "new.wav", 600, age_s=1)      # newest
    remaining, removed = prune_cache_dir(str(tmp_path), max_bytes=1000)
    assert removed == 1
    assert not old.exists()      # oldest evicted
    assert new.exists()          # newest kept
    assert remaining <= 1000


def test_prune_cache_missing_dir_is_safe(tmp_path):
    assert prune_cache_dir(str(tmp_path / "nope")) == (0, 0)


# ── #28 two-pass loudnorm: measure filter ───────────────────────────────────

def test_measure_filter_goldens():
    assert build_loudnorm_measure_filter("acx") == "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:print_format=json"
    assert build_loudnorm_measure_filter("podcast") == "loudnorm=I=-16.0:TP=-1.5:LRA=11.0:print_format=json"
    assert build_loudnorm_measure_filter("ACX") == build_loudnorm_measure_filter("acx")


@pytest.mark.parametrize("val", [None, "", "off", "none", "bogus", " acx "])
def test_measure_filter_off_unknown_whitespace_is_none(val):
    # mirrors single-pass: no strip, so " acx " is unknown → None
    assert build_loudnorm_measure_filter(val) is None


# ── parse_loudnorm_measure ──────────────────────────────────────────────────

def test_parse_measure_success_picks_last_block_and_ignores_extra_keys():
    m = parse_loudnorm_measure(_MEASURE_JSON)
    assert m == _MEASURED


def test_parse_measure_last_block_wins_over_config_dump():
    text = '{"input_i":"1"}\nconfig\n' + _MEASURE_JSON
    assert parse_loudnorm_measure(text) == _MEASURED


@pytest.mark.parametrize("bad", [
    None, "", "   ", "no braces here", '{ "input_i": "-1"',          # no/unbalanced
    '{ "input_i": "-1", }', '{ input_i: -1 }',                       # malformed json
    '{ "input_i":"-1","input_tp":"-3","input_lra":"0","input_thresh":"-30" }',  # missing target_offset
    '[1,2,3]', '"scalar"',                                           # not an object
])
def test_parse_measure_failure_matrix_returns_none(bad):
    assert parse_loudnorm_measure(bad) is None


@pytest.mark.parametrize("badval", ['"n/a"', '""', '"-inf"', '"inf"', '"nan"'])
def test_parse_measure_rejects_nonnumeric_and_nonfinite(badval):
    text = ('{ "input_i":%s,"input_tp":"-3","input_lra":"0",'
            '"input_thresh":"-30","target_offset":"0.0" }') % badval
    assert parse_loudnorm_measure(text) is None


# ── apply filter ────────────────────────────────────────────────────────────

def test_apply_filter_golden():
    f = build_loudnorm_apply_filter("acx", _MEASURED)
    assert f == (
        "loudnorm=I=-19.0:TP=-3.0:LRA=11.0:measured_I=-21.75:measured_TP=-18.06"
        ":measured_LRA=0.0:measured_thresh=-31.75:offset=0.05:linear=true:print_format=summary"
    )


@pytest.mark.parametrize("preset,measured", [
    ("off", _MEASURED), (None, _MEASURED), ("bogus", _MEASURED), ("acx", None),
])
def test_apply_filter_none_cases(preset, measured):
    assert build_loudnorm_apply_filter(preset, measured) is None


# ── measure cmd argv ────────────────────────────────────────────────────────

def test_measure_cmd_exact_argv():
    assert build_loudnorm_measure_cmd("ffmpeg", "c.txt", "FILT") == [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "info",
        "-f", "concat", "-safe", "0", "-i", "c.txt",
        "-af", "FILT", "-f", "null", "-",
    ]


# ── build_render_cmd measured branch ────────────────────────────────────────

def test_render_cmd_two_pass_apply_when_measured():
    cmd = build_render_cmd("ffmpeg", "c.txt", "m.ff", "o.m4b", loudness="acx", measured=_MEASURED)
    af = cmd[cmd.index("-af") + 1]
    assert "measured_I=-21.75" in af and "linear=true" in af


def test_render_cmd_single_pass_when_measured_none():
    cmd = build_render_cmd("ffmpeg", "c.txt", "m.ff", "o.m4b", loudness="acx", measured=None)
    af = cmd[cmd.index("-af") + 1]
    assert af == "loudnorm=I=-19.0:TP=-3.0:LRA=11.0"   # single-pass, no measured_*


def test_render_cmd_off_emits_no_af_even_with_stray_measured():
    cmd = build_render_cmd("ffmpeg", "c.txt", "m.ff", "o.m4b", loudness="off", measured=_MEASURED)
    assert "-af" not in cmd

    cmd2 = build_render_cmd("ffmpeg", "c.txt", "m.ff", "o.m4b")  # default: no loudness/measured
    assert "-af" not in cmd2


@pytest.mark.parametrize("fmt", ["m4b", "mp3"])
def test_public_renderer_retains_metadata_with_windows_newline_defaults(tmp_path, monkeypatch, fmt):
    """Native full renderer, with Windows text-mode translation on any host."""
    import asyncio
    import builtins
    import json
    import subprocess
    torch = pytest.importorskip("torch")
    from api.routers import audiobook
    from core.db import init_db
    from services.audiobook import AudiobookPlan, Chapter, Span
    from services.ffmpeg_utils import find_ffmpeg, find_ffprobe

    if not find_ffmpeg() or not find_ffprobe():
        pytest.skip("ffmpeg and ffprobe required for metadata mux round trip")
    init_db()
    outputs = tmp_path / "outputs"
    outputs.mkdir()
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(outputs))

    def synth_factory(*args, **kwargs):
        return {"mode": "generic", "engine_id": "stub", "sample_rate": 24000,
                "resolve": lambda _: {"ref_audio": None, "ref_text": None,
                                       "instruct": None, "seed": None},
                "synth": lambda *args, **kwargs: torch.zeros(2400)}
    monkeypatch.setattr(audiobook, "_build_synth", synth_factory)  # Model boundary only.
    native_open = builtins.open

    def windows_text_open(file, mode="r", *args, **kwargs):
        if "w" in mode and "b" not in mode:
            kwargs.setdefault("newline", "\r\n")
        return native_open(file, mode, *args, **kwargs)
    monkeypatch.setattr(audiobook, "open", windows_text_open, raising=False)

    async def render():
        plan = AudiobookPlan(chapters=[Chapter(title="One", spans=[Span(voice_id=None, text="Hello.")])])
        return [json.loads(frame[len("data:"):].strip())
                async for frame in audiobook._render_longform_sse(
                    plan, default_voice=None, fmt=fmt,
                    metadata={"description": "Opening paragraph.\r\nClosing paragraph."})]

    events = asyncio.run(render())
    assert events[-1]["type"] == "done", events
    output = outputs / events[-1]["output"]
    probe = subprocess.run([find_ffprobe(), "-v", "error", "-show_entries", "format_tags=comment",
                            "-of", "json", str(output)],
                           check=True, capture_output=True, timeout=30)
    assert json.loads(probe.stdout)["format"]["tags"]["comment"] == "Opening paragraph.\nClosing paragraph."
