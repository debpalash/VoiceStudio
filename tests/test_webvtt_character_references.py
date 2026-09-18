"""WebVTT cue text escapes `&`, `<` and `>` as character references.

A caption track saves "Q&A" as ``Q&amp;A`` and a ">>" speaker-change marker
as ``&gt;&gt;``. Both places that read WebVTT for dubbing — the caption track
downloaded with a video and a pasted or picked ``.vtt`` translation — must
hand the dub the words, not the escapes, or the voice says "amp".
"""
from __future__ import annotations

import os

os.environ.setdefault("OMNIVOICE_MODEL", "test")

VTT = """WEBVTT

00:00:01.000 --> 00:00:03.000
&gt;&gt; Welcome to the Q&amp;A.

00:00:03.500 --> 00:00:05.000
Is 3 &lt; 4? Yes&nbsp;&#8212; always.
"""


def test_downloaded_caption_track_decodes_character_references(tmp_path):
    from services.dub_pipeline import parse_vtt_segments

    track = tmp_path / "original.en.vtt"
    track.write_text(VTT, encoding="utf-8")

    assert [cue["text"] for cue in parse_vtt_segments(str(track))] == [
        ">> Welcome to the Q&A.",
        "Is 3 < 4? Yes — always.",
    ]


def test_pasted_webvtt_translation_decodes_character_references():
    from api.routers import dub_core
    from schemas.requests import ParseSubtitleTextRequest

    result = dub_core.dub_parse_subtitle_text(ParseSubtitleTextRequest(text=VTT))

    assert [cue["text"] for cue in result["segments"]] == [
        ">> Welcome to the Q&A.",
        "Is 3 < 4? Yes — always.",
    ]
