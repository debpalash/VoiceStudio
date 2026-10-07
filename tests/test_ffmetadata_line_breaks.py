"""Description paragraphs separated by CRLF/CR must survive the real mux (#2528)."""
import json
import shutil
import subprocess
import wave

import pytest

from services.longform_render import (
    build_concat_list,
    build_ffmetadata,
    build_render_cmd,
)

_FFMPEG = shutil.which("ffmpeg")
_FFPROBE = shutil.which("ffprobe")


@pytest.mark.parametrize("sep", ["\r\n", "\r", "\n"])
def test_every_line_break_style_escapes_to_one_logical_line(sep):
    doc = build_ffmetadata([("A", 100)], {"description": f"One.{sep}Two."})
    lines = doc.split("\n")
    assert "\r" not in doc
    comment = next(i for i, ln in enumerate(lines) if ln.startswith("comment="))
    assert lines[comment] == "comment=One.\\"
    assert lines[comment + 1] == "Two."


def test_chapter_titles_are_normalized_too():
    assert "\r" not in build_ffmetadata([("A\r\nB", 100)])


@pytest.mark.skipif(not (_FFMPEG and _FFPROBE), reason="needs ffmpeg/ffprobe")
@pytest.mark.parametrize("fmt", ["m4b", "mp3"])
@pytest.mark.parametrize("sep", ["\r\n", "\r", "\n"])
def test_description_paragraphs_survive_the_mux(tmp_path, fmt, sep):
    wav = tmp_path / "c.wav"
    with wave.open(str(wav), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(24000)
        w.writeframes(b"\x00\x00" * 24000)
    concat = tmp_path / "list.txt"
    concat.write_text(build_concat_list([str(wav)]))
    meta = tmp_path / "meta.txt"
    meta.write_text(
        build_ffmetadata([("Ch", 1000)], {"description": f"Opening paragraph.{sep}Closing paragraph."}),
        encoding="utf-8", newline="",
    )
    out = tmp_path / f"out.{fmt}"
    subprocess.run(build_render_cmd(_FFMPEG, str(concat), str(meta), str(out), fmt=fmt), check=True)
    probe = subprocess.run(
        [_FFPROBE, "-v", "error", "-show_format", "-of", "json", str(out)],
        check=True, capture_output=True, text=True,
    )
    comment = json.loads(probe.stdout)["format"]["tags"]["comment"]
    assert "Opening paragraph." in comment and "Closing paragraph." in comment


def test_mux_inputs_are_written_with_bare_lf():
    """Text-mode writes turn LF into CRLF on Windows, which ends the escaped
    multi-paragraph tag early there only; the router must use the LF writer."""
    import inspect

    from api.routers import audiobook

    src = inspect.getsource(audiobook)
    assert "write_lf_text(meta_path" in src and "write_lf_text(concat_path" in src
    assert 'open(meta_path, "w"' not in src and 'open(concat_path, "w"' not in src


def test_write_lf_text_is_byte_exact(tmp_path):
    from services.longform_render import write_lf_text

    p = tmp_path / "m"
    write_lf_text(str(p), "a\\\nbé\n")
    assert p.read_bytes() == "a\\\nbé\n".encode()
