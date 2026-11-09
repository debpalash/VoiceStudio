"""#1262: a non-Latin voice-profile name 500'd every download endpoint.

    500 Internal Server Error: 'latin-1' codec can't encode characters in
    position 22-25: ordinal not in range(256)

``attachment; filename="`` is exactly 22 characters long, so positions 22-25
were the first four characters of the reporter's own profile name. HTTP header
values are latin-1 by definition; every download endpoint interpolated the
filename straight into the header.

The sanitisers in front of those f-strings looked like they covered it — but
they all filtered with ``str.isalnum()``, which is ``True`` for *every*
alphabetic script. They stripped punctuation and let through exactly the
characters that break the header.

This was never one endpoint: the same ``isalnum()`` idiom was copy-pasted
across persona export, marketplace, stories, the OpenAI-compatible speech
route, and eight dub-export routes. All of them now go through one RFC 6266
builder, and a guard below keeps the next one from being written by hand.
"""
from __future__ import annotations

import pathlib
import re

import pytest

from core.http_headers import ascii_filename, content_disposition

REPO = pathlib.Path(__file__).resolve().parents[1]


# ── the header is always encodable ───────────────────────────────────────


@pytest.mark.parametrize(
    "name",
    [
        "我的声音.ovsvoice",            # Chinese — the reported shape
        "私の声.ovsvoice",              # Japanese
        "내 목소리.ovsvoice",            # Korean
        "Моя речь.ovsvoice",           # Cyrillic
        "φωνή.ovsvoice",               # Greek
        "קול.ovsvoice",                # Hebrew
        "🎙️ voice.ovsvoice",            # emoji
        "Sébastien’s voix.ovsvoice",   # accented Latin + smart quote
    ],
)
def test_the_header_survives_any_script(name):
    header = content_disposition(name)
    # The actual failure: Starlette encodes header values as latin-1.
    header.encode("latin-1")


def test_the_exact_reported_failure():
    """A four-character CJK name — the one that produced 'position 22-25'."""
    header = content_disposition("我的声音.ovsvoice")
    header.encode("latin-1")
    assert 'filename="' in header
    assert "filename*=UTF-8''" in header


def test_the_real_name_is_preserved_for_modern_clients():
    header = content_disposition("我的声音.ovsvoice")
    # RFC 5987 percent-encoded UTF-8 — browsers prefer this over `filename=`.
    assert "%E6%88%91%E7%9A%84%E5%A3%B0%E9%9F%B3" in header


def test_accented_latin_is_folded_not_deleted():
    assert ascii_filename("Sébastien.ovsvoice") == "Sebastien.ovsvoice"


def test_an_entirely_non_ascii_name_still_yields_a_usable_filename():
    """Stripping CJK leaves only ".ovsvoice", which is not a filename."""
    safe = ascii_filename("我的声音.ovsvoice")
    assert safe.endswith(".ovsvoice")
    stem = safe[: -len(".ovsvoice")]
    assert stem and stem.strip("_ "), f"no usable stem in {safe!r}"


def test_ascii_names_are_left_alone():
    assert ascii_filename("dubbed_output_en.mp4") == "dubbed_output_en.mp4"
    header = content_disposition("dubbed_output_en.mp4")
    assert 'filename="dubbed_output_en.mp4"' in header


@pytest.mark.parametrize("hostile", ['a"b.mp4', "a\\b.mp4", "a\r\nX-Evil: 1.mp4", "a/b/c.mp4"])
def test_quoting_and_header_injection_are_neutralised(hostile):
    """A dub filename comes from a video title, i.e. from the internet. A bare
    quote would end the quoted-string; a CRLF would split the header."""
    header = content_disposition(hostile)
    header.encode("latin-1")
    assert "\r" not in header and "\n" not in header
    # Exactly the two parameters we intend, no smuggled third.
    assert header.count("filename=") == 1
    assert header.count("filename*=") == 1


def test_inline_disposition_is_supported():
    """The OpenAI-compatible speech route streams inline, not as a download."""
    assert content_disposition("speech.mp3", disposition="inline").startswith("inline;")


# ── and no endpoint builds the header by hand again ──────────────────────


def test_no_router_interpolates_a_filename_into_the_header():
    """The recurrence guard. This bug shipped in ten places because the header
    was written by f-string ten times; the eleventh must not compile."""
    offenders = []
    pattern = re.compile(r'"Content-Disposition"\s*:\s*f[\'"]')
    for path in (REPO / "backend").rglob("*.py"):
        if "test" in path.parts:
            continue
        for i, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if pattern.search(line):
                offenders.append(f"{path.relative_to(REPO)}:{i}")
    assert offenders == [], (
        "build the value with core.http_headers.content_disposition() — an "
        "f-string here 500s on any non-latin-1 filename (#1262)"
    )


def test_every_download_endpoint_actually_uses_the_builder():
    """Complements the guard above: proves the call sites were converted, not
    merely reworded into something the regex misses."""
    routers = REPO / "backend" / "api" / "routers"
    users = {
        path.name
        for path in routers.rglob("*.py")
        if "content_disposition(" in path.read_text(encoding="utf-8")
    }
    for expected in (
        "personas.py",
        "marketplace.py",
        "stories.py",
        "dub_export.py",
        "openai_compat.py",
    ):
        assert expected in users, f"{expected} still builds the header itself"


def test_a_hostile_custom_fallback_cannot_reach_the_header(): 
    """Review finding (#1262): `fallback` landed in `filename=` verbatim
    whenever the real name folded away entirely, so a non-ASCII or CRLF
    fallback walked past every guard the real name goes through."""
    header = content_disposition("我的声音.ovsvoice", fallback='ev"il\r\nX-Evil: 1')
    header.encode("latin-1")
    assert "\r" not in header and "\n" not in header
    assert header.count("filename=") == 1
    assert header.count("filename*=") == 1

    # A fallback that is ENTIRELY non-ASCII must still leave a usable name.
    header = content_disposition("我的声音.ovsvoice", fallback="声音")
    header.encode("latin-1")
    assert 'filename=""' not in header


@pytest.mark.parametrize("control", [chr(i) for i in range(32)] + [chr(127)])
def test_download_filenames_remove_ascii_controls(control):
    header = content_disposition(f"voice{control}take.wav")
    assert all(ord(c) >= 32 and ord(c) != 127 for c in header)
    assert 'filename="voice_take.wav"' in header
    assert "filename*=UTF-8''voice_take.wav" in header


def test_custom_fallback_removes_nul_before_http_serialization():
    import h11

    header = content_disposition("🎙.wav", fallback="voice\x00take")
    response = h11.Response(status_code=200, headers=[("Content-Disposition", header)])
    assert h11.Connection(h11.SERVER).send(response)
