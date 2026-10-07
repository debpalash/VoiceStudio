"""Audiobook render stops on client disconnect (#1216).

"The Create audiobook button has no stop option." The frontend fix aborts the
fetch on Stop; this pins the BACKEND half: ``_render_longform_sse`` polls
``is_disconnected()`` at each chapter boundary and, when the client is gone,
stops scheduling further chapters instead of rendering the whole book into a
stream nobody reads — while leaving the finished chapters cached and the resume
manifest in place so a later Create/resume finishes the rest cheaply.

No models / GPU: the synth boundary is stubbed (a CPU tone), exactly like the
longform e2e. ffmpeg is never reached on the stop path (we break before
assembling), so it's stubbed truthy only to clear the early gate.
"""
from __future__ import annotations

import asyncio
import json

import pytest
import torch

from services.ffmpeg_utils import find_ffmpeg


def _resolve(_voice_id):
    return {"ref_audio": None, "ref_text": None, "instruct": None, "seed": None}


def _stub_build_synth():
    def _factory(default_voice=None, language=None, opts=None, voice_map=None, lease=None):
        def synth(text, voice_id, speed=None):
            return torch.zeros(2400)  # 0.1s @ 24k, 1-D float32
        return {"mode": "generic", "resolve": _resolve, "engine_id": "stub",
                "synth": synth, "sample_rate": 24000}
    return _factory


def _plan(*chapters):
    from services.audiobook import AudiobookPlan, Chapter, Span
    return AudiobookPlan(chapters=[
        Chapter(title=title, spans=[Span(voice_id=None, text=body)])
        for title, body in chapters
    ])


def _drive(plan, monkeypatch, outputs_dir, *, is_disconnected=None, **kw):
    from api.routers import audiobook
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(outputs_dir))
    # Clear the early ffmpeg gate; the stop path never reaches run_ffmpeg.
    monkeypatch.setattr("services.ffmpeg_utils.find_ffmpeg", lambda: "/usr/bin/true")

    async def _run():
        out = []
        async for frame in audiobook._render_longform_sse(
            plan, default_voice=None, is_disconnected=is_disconnected, **kw
        ):
            out.append(json.loads(frame[len("data:"):].strip()))
        return out

    return asyncio.run(_run())


def _disconnect_after(n_ok):
    """Async is_disconnected that returns False for the first ``n_ok`` polls
    (letting those chapters render), then True (client gone)."""
    state = {"i": 0}

    async def check():
        i = state["i"]
        state["i"] += 1
        return i >= n_ok

    return check


def test_disconnect_stops_early_and_preserves_resume(tmp_path, monkeypatch):
    out = tmp_path / "outputs"
    out.mkdir()
    events = _drive(
        _plan(("One", "a"), ("Two", "b"), ("Three", "c")),
        monkeypatch, out, is_disconnected=_disconnect_after(1),
    )
    types = [e["type"] for e in events]
    # Only the first chapter rendered; the render stopped before scheduling more.
    assert types.count("chapter") == 1
    assert "assembling" not in types and "done" not in types
    assert types[-1] == "stopped"
    assert events[-1]["rendered"] == 1 and events[-1]["total"] == 3

    # The resume manifest is preserved (NOT cleared), so the finished chapter is
    # offered for resume — Create-again picks up the rest from the cache.
    from services import longform_resume
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(out))
    assert any(e["job_type"] == "audiobook" for e in longform_resume.scan_resumable())


@pytest.mark.skipif(find_ffmpeg() is None, reason="ffmpeg required for the full-render control")
def test_no_disconnect_renders_all_chapters(tmp_path, monkeypatch):
    # Control: a connected client (is_disconnected always False) is never
    # stopped — the normal completion path is untouched.
    out = tmp_path / "outputs"
    out.mkdir()

    async def _connected():
        return False

    from api.routers import audiobook
    monkeypatch.setattr(audiobook, "_build_synth", _stub_build_synth())
    monkeypatch.setattr("core.config.OUTPUTS_DIR", str(out))

    async def _run():
        return [json.loads(f[len("data:"):].strip())
                async for f in audiobook._render_longform_sse(
                    _plan(("One", "a"), ("Two", "b"), ("Three", "c")),
                    default_voice=None, fmt="m4b", is_disconnected=_connected)]

    events = asyncio.run(_run())
    types = [e["type"] for e in events]
    assert "stopped" not in types
    assert types.count("chapter") == 3
    assert types[-1] == "done"
