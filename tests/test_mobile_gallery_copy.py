"""Phone voice copies explicitly request a bounded, stored legacy voice bundle."""
import asyncio
import io
import zipfile
from contextlib import contextmanager

import pytest
from fastapi import HTTPException


@pytest.fixture
def export(tmp_path, monkeypatch):
    from api.routers import marketplace as routes
    audio = tmp_path / 'reference.wav'
    audio.write_bytes(b'RIFF reference fixture')
    profile = {'id': 'phone-copy', 'name': 'Shared voice', 'ref_text': 'Reference transcript', 'ref_audio_path': audio.name}

    class Connection:
        def execute(self, *args):
            return self

        def fetchone(self):
            return profile

    @contextmanager
    def connection():
        yield Connection()

    monkeypatch.setattr(routes, 'db_conn', connection)
    monkeypatch.setattr(routes, 'VOICES_DIR', str(tmp_path))
    return routes, audio


def contents(response):
    async def read():
        return b''.join([part async for part in response.body_iterator])
    return asyncio.run(read())


def test_phone_copy_opt_in_preserves_default_archive_format(export):
    routes, audio = export
    for stored in (False, True):
        response = routes.export_profile('phone-copy', stored=stored)
        with zipfile.ZipFile(io.BytesIO(contents(response))) as archive:
            assert archive.read('ref_audio.wav') == audio.read_bytes()
            assert archive.getinfo('metadata.json').compress_type == (zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
            assert archive.getinfo('ref_audio.wav').compress_type == (zipfile.ZIP_STORED if stored else zipfile.ZIP_DEFLATED)
    with zipfile.ZipFile(io.BytesIO(contents(routes.export_profile('phone-copy')))) as archive:
        assert archive.getinfo('ref_audio.wav').compress_type == zipfile.ZIP_DEFLATED


def test_phone_copy_refuses_large_reference_before_building_archive(export):
    routes, audio = export
    with audio.open('wb') as output:
        output.truncate(32 * 1024 * 1024 + 1)
    with pytest.raises(HTTPException) as failure:
        routes.export_profile('phone-copy', stored=True)
    assert failure.value.status_code == 413


def test_phone_copy_bounds_the_complete_archive(export):
    routes, audio = export
    with audio.open('wb') as output:
        output.truncate(32 * 1024 * 1024)
    with routes.db_conn() as conn:
        conn.fetchone()['locked_audio_path'] = audio.name
    with pytest.raises(HTTPException) as failure:
        routes.export_profile('phone-copy', stored=True)
    assert failure.value.status_code == 413
