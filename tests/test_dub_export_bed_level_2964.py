"""#2964: dub exports take a background bed level instead of a fixed 1.0.

Every dub export that mixes the separated background (video mux, preview mux,
audio-only download, /dub/download-audio, /dub/download-mp3) hard-coded
``bed_gain=1.0``, so a quiet source exported almost dry. Each now takes
``bed_level`` (default 1.0, the old behaviour), and caches keyed on the mix
keep their pre-#2964 names at the default level.
"""
from __future__ import annotations

import ast
import asyncio
import inspect
import os

import pytest


def _module():
    from api.routers import dub_export

    return dub_export


def test_every_bed_mix_call_site_uses_the_requested_level():
    tree = ast.parse(inspect.getsource(_module()))
    calls = [
        node for node in ast.walk(tree)
        if isinstance(node, ast.Call) and getattr(node.func, "id", None) == "bed_mix_filter"
    ]
    assert len(calls) >= 5
    for call in calls:
        gain = next((kw.value for kw in call.keywords if kw.arg == "bed_gain"), None)
        assert isinstance(gain, ast.Name) and gain.id == "bed_level", ast.unparse(call)


def test_every_background_route_accepts_bed_level():
    from fastapi.dependencies.utils import get_dependant

    mod = _module()
    routes = [r for r in mod.router.routes if "preserve_bg" in inspect.signature(r.endpoint).parameters]
    assert {r.path.split("/{")[0] for r in routes} >= {
        "/dub/download", "/dub/download-audio", "/dub/download-mp3", "/dub/preview-video",
    }
    for route in routes:
        params = {p.name: p for p in get_dependant(path=route.path, call=route.endpoint).query_params}
        assert "bed_level" in params, route.path
        info = params["bed_level"].field_info
        assert params["bed_level"].default == 1.0
        limits = {type(m).__name__: m for m in info.metadata}
        assert limits["Ge"].ge == 0.0 and limits["Le"].le == 4.0


@pytest.mark.parametrize("normalize", [True, False])
def test_audio_export_command_carries_the_level(monkeypatch, normalize):
    mod = _module()
    build = mod._build_audio_export_cmd
    monkeypatch.setitem(build.__globals__["bed_mix_filter"].__globals__, "_AMIX_NORMALIZE", normalize)
    default = " ".join(build("ffmpeg", "/j/de.wav", "/j/bg.wav", "/j/out.m4a", "m4a"))
    raised = " ".join(build("ffmpeg", "/j/de.wav", "/j/bg.wav", "/j/out.m4a", "m4a", 2.5))
    if normalize:
        assert "volume=1[" in default and "volume=2.5[" in raised
    else:
        assert "weights=1 " in default and "weights=2.5 " in raised


def test_default_level_keeps_existing_cache_names():
    mod = _module()
    assert mod._bed_level_tag(1.0) == ""
    assert mod._bed_level_tag(2.0) == "_bed2p0"
    assert mod._bed_level_tag(0.0) == "_bed0p0"


def test_distinct_levels_never_share_a_preview_name():
    """Rounding to percent made 0, 0.001, 0.002 and 0.004 one cached preview."""
    mod = _module()
    levels = [0.0, 0.001, 0.002, 0.004, 0.005, 1.1, 1.104, 4.0]
    tags = [mod._bed_level_tag(level) for level in levels]
    assert len(set(tags)) == len(levels)
    assert all(tag.replace("_", "").replace("-", "").isalnum() for tag in tags)


def test_mixed_audio_cache_is_per_level(tmp_path, monkeypatch):
    mod = _module()
    fn = mod._mixed_dub_audio
    g = fn.__globals__
    exports = tmp_path / "dub" / "job" / "exports"
    exports.mkdir(parents=True)
    bg, track = tmp_path / "dub" / "job" / "bg.wav", tmp_path / "dub" / "job" / "de.wav"
    bg.write_bytes(b"b")
    track.write_bytes(b"t")
    filters: list[str] = []

    async def fake_run(cmd, timeout=None):
        filters.append(cmd[cmd.index("-filter_complex") + 1])
        with open(cmd[-1], "wb") as fh:
            fh.write(b"RIFF")
        return 0, b"", b""

    monkeypatch.setitem(g, "DUB_DIR", str(tmp_path / "dub"))
    monkeypatch.setitem(g, "run_ffmpeg", fake_run)
    monkeypatch.setitem(g, "find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setitem(g["bed_mix_filter"].__globals__, "_AMIX_NORMALIZE", True)

    async def mix(*level):
        path = await fn(str(exports), "de", str(bg), str(track), *level)
        g["_release_mix"](path)
        return os.path.basename(path)

    default = asyncio.run(mix())
    assert asyncio.run(mix(1.0)) == default, "explicit default must reuse the pre-#2964 cache"
    raised = asyncio.run(mix(2.0))
    assert raised != default
    assert len(filters) == 2 and "volume=2[" in filters[1]
