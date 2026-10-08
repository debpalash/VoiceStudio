"""Titles become filenames; Windows rejects `<>:"|?*` with [Errno 22] (#2376 class)."""
import pathlib
import re

import pytest

BACKEND = pathlib.Path(__file__).resolve().parents[1] / "backend"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("How to X: a guide?_en.mp4", "How to X_ a guide__en.mp4"),
        ('a<b>c|d"e*f.srt', "a_b_c_d_e_f.srt"),
        ("clip. .", "clip"),
        ("CON.wav", "_CON.wav"),
        ("lpt1", "_lpt1"),
        ("???", "file"),
        ("", "file"),
        ("tab\there\x00.wav", "tab_here_.wav"),
    ],
)
def test_portable_filename_repairs_windows_hostile_names(raw, expected):
    from core.path_security import portable_filename

    assert portable_filename(raw) == expected


def test_reserved_device_name_exposed_by_truncation_is_still_repaired():
    from core.path_security import portable_filename

    name = portable_filename("CON" + " " * 197 + "x.wav")
    assert name.split(".")[0].upper() != "CON" and name.endswith(".wav")
    assert len(name.encode("utf-8")) <= 200


def test_portable_filename_truncates_by_bytes_and_keeps_extension():
    from core.path_security import portable_filename

    name = portable_filename("日本語" * 60 + ".mp4")
    assert len(name.encode("utf-8")) <= 200
    assert name.endswith(".mp4") and "�" not in name


def test_download_names_derived_from_titles_are_never_raw():
    """A job's filename is the video title; every route that turns it into a
    download name must go through portable_filename (or an alnum allowlist)."""
    offenders = []
    for rel in ("api/routers/dub_export.py", "api/routers/batch.py"):
        text = (BACKEND / rel).read_text(encoding="utf-8")
        for match in re.finditer(r'^\s*(?:dl_)?filename\s*=\s*f"[^"\n]*\{[^}]*(?:base_name|filename)[^}]*\}[^\n]*$|^\s*dl_name\s*=\s*f"[^\n]*\{base_name\}[^\n]*$', text, re.M):
            offenders.append(f"{rel}: {match.group(0).strip()}")
    assert not offenders, offenders
