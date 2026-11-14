"""The project-bundle importer's ffmpeg call restricts the input to local protocols.

A bundle's ``source`` asset is copied straight out of an untrusted archive, so a
file named ``source.mp4`` can be an HLS playlist: without
``-protocol_whitelist file,pipe`` ffmpeg follows every URL inside it and the
import becomes a request to whatever host the playlist names. The importer's
conversion lane calls ``local_inputs_only`` with no argv, which returns ``[]``
and expands to nothing — the guard silently does not run on this lane, unlike
every other ffmpeg call site (``run_ffmpeg`` fails closed the same way).
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

HANDOFF_DIRS = ('OUTPUTS_DIR', 'VOICES_DIR', 'DUB_DIR')
PLAYLIST = b'#EXTM3U\n#EXT-X-VERSION:3\n#EXTINF:2.0,\nhttp://169.254.169.254/latest/meta-data/\n#EXT-X-ENDLIST\n'


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    from core import db
    from api.routers import project_bundles as routes
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'library.db')
    db.init_db()
    for name in HANDOFF_DIRS:
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(routes, name, directory)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, client=('127.0.0.1', 50000)), routes


def audio_only_dub_archive(tmp_path):
    """A dub bundle whose ``.mp4`` source asset is really a playlist."""
    from services import project_bundle as bundles
    source = tmp_path / 'source.mp4'
    source.write_bytes(PLAYLIST)
    document = {'duration': 1, 'source': 'assets/source.mp4', 'audioOnly': True,
                'targets': [{'code': 'fr', 'name': 'French'}],
                'segments': [{'id': 'cue', 'start': 0, 'end': 1, 'text': 'Source'}]}
    output = tmp_path / 'playlist.vsproject'
    bundles.write_bundle({'kind': 'dub', 'title': 'Imported media', 'document': document,
                          'voices': []}, {'assets/source.mp4': source}, output)
    return output.read_bytes()


def test_imported_dub_source_conversion_whitelists_local_protocols(handoff, tmp_path, monkeypatch):
    client, _ = handoff
    monkeypatch.setenv('FFMPEG_PATH', str(tmp_path / 'renamed-binary'))
    monkeypatch.setattr('services.ffmpeg_utils.find_ffmpeg', lambda: str(tmp_path / 'renamed-binary'))
    seen = []
    def capture(argv, **kwargs):
        seen.append(list(argv))
        open(argv[-1], 'wb').close()
        return type('Completed', (), {'returncode': 0})()
    monkeypatch.setattr('subprocess.run', capture)

    response = client.post('/project-bundles/import?allow_voices=true', content=audio_only_dub_archive(tmp_path))

    assert response.status_code == 200, response.text
    assert len(seen) == 1, seen
    cmd = seen[0]
    # ffmpeg's -protocol_whitelist is an input option: it only applies to the
    # input that follows it, so it sits immediately before -i.
    index = cmd.index('-i')
    assert cmd[index - 2:index] == ['-protocol_whitelist', 'file,pipe'], cmd
    assert cmd[index + 1].endswith('source.mp4'), cmd
    assert '-vn' in cmd and cmd[cmd.index('-ac') + 1] == '1' and cmd[cmd.index('-ar') + 1] == '24000'
