"""Dub track language codes name files under the job directory
(``dubbed_{lang}.wav``, ``seg_{lang}_{id}.wav``). Every route that accepts
one must reject values that are not plain codes before any path is built,
while ordinary codes (``es``, ``zh-CN``, ``pt_BR``, the ``und`` default)
keep working.
"""
from __future__ import annotations

import asyncio
import os

os.environ.setdefault("OMNIVOICE_DISABLE_FILE_LOG", "1")

import pytest
from fastapi import HTTPException

from schemas.requests import DubRequest, DubSegment

_BAD_CODES = [
    "../../outside",
    "..",
    "es/../../x",
    "es\\..\\..\\x",
    "/abs",
    "C:evil",
    " es",
    "es\x00",
    "a" * 33,
]
_GOOD_CODES = ["es", "zh-CN", "pt_BR", "yue", "und", ""]


class _ReachedEngine(Exception):
    """Raised once a request gets past input validation."""


@pytest.fixture
def generate_route(monkeypatch):
    from api.routers import dub_generate as mod

    monkeypatch.setattr(mod, "_get_job", lambda job_id: {"segments": [], "dubbed_tracks": {}})

    async def _stop():
        raise _ReachedEngine()

    monkeypatch.setattr(mod, "_resolve_dub_execution", _stop)
    return mod.dub_generate


def _request(code: str) -> DubRequest:
    return DubRequest(
        segments=[DubSegment(start=0.0, end=1.0, text="hola")],
        language_code=code,
    )


@pytest.mark.parametrize("code", _BAD_CODES)
def test_generate_rejects_unsafe_language_code(generate_route, code):
    with pytest.raises(HTTPException) as exc:
        asyncio.run(generate_route("job1", _request(code)))
    assert exc.value.status_code == 400


@pytest.mark.parametrize("code", _GOOD_CODES)
def test_generate_accepts_plain_language_codes(generate_route, code):
    with pytest.raises(_ReachedEngine):
        asyncio.run(generate_route("job1", _request(code)))


def test_export_routes_share_the_same_language_check():
    from api.routers import dub_core, dub_export

    assert dub_export._safe_lang_or_400 is dub_core._safe_lang_or_400
    for code in _GOOD_CODES[:-1]:
        assert dub_core._safe_lang_or_400(code) == code
    assert dub_core._safe_lang_or_400(None) is None
    for code in _BAD_CODES:
        with pytest.raises(HTTPException) as exc:
            dub_core._safe_lang_or_400(code)
        assert exc.value.status_code == 400
