"""Editable project transfers retain target languages before their audio is rendered."""
import json
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient


@pytest.fixture
def handoff(tmp_path, monkeypatch):
    from api.routers import project_bundles as routes
    from core import db
    monkeypatch.setattr(db, 'DB_PATH', tmp_path / 'library.db')
    db.init_db()
    for name in ('OUTPUTS_DIR', 'VOICES_DIR', 'DUB_DIR'):
        directory = tmp_path / name
        directory.mkdir()
        monkeypatch.setattr(routes, name, directory)
    app = FastAPI()
    app.include_router(routes.router)
    return TestClient(app, client=("127.0.0.1", 50000)), routes, db


@pytest.mark.parametrize('tracks,selected,translations,text,job_language,expected_codes,expected_translations', [
    (['fr'], 'fr', {'fr':'Bonjour','de':'Hallo'}, 'Bonjour', 'fr', ['fr','de'], {'fr':'Bonjour','de':'Hallo'}),
    ([], 'fr', {'fr':'Bonjour','de':'Hallo'}, 'Bonjour', None, ['fr','de'], {'fr':'Bonjour','de':'Hallo'}),
    (['original','de','de'], 'fr', {'fr':'Bonjour','es':'Hola'}, 'Bonjour', 'de', ['fr','de','es'], {'fr':'Bonjour','es':'Hola'}),
    ([], None, {'es':'Hola','fr':'Bonjour'}, 'Hello', None, ['es','fr'], {'es':'Hola','fr':'Bonjour'}),
    (['es'], 'de', {'es':'Hola'}, 'Hola', 'es', ['de','es'], {'es':'Hola'}),
    (['fr'], 'fr', {'fr':'Edited translation'}, 'Stale text', 'fr', ['fr'], {'fr':'Edited translation'}),
    ([], 'fr', None, 'Bonjour', 'fr', ['fr'], {'fr':'Bonjour'}),
    ([], 'fr', None, 'Hello', None, ['fr'], {}),
])
def test_export_import_preserves_saved_translations_and_selected_target(
    handoff, tmp_path, monkeypatch, tracks, selected, translations, text,
    job_language, expected_codes, expected_translations,
):
    from services import dub_pipeline, project_bundle as bundles
    client, routes, db = handoff
    source = routes.DUB_DIR / 'source.wav'
    with wave.open(str(source), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(24000)
        output.writeframes(b'\0\0' * 24000)
    segment = {'id':'cue','start':0,'end':1,'text_original':'Hello','text':text}
    if translations is not None:
        segment['translations'] = translations
    state = {'dubJobId':'job','dubSegments':[segment],'dubTracks':tracks,'inputType':'audio'}
    if selected:
        state.update(dubLangCode=selected, dubLang='Selected language')
    job = {'video_path':str(source),'audio_path':str(source),'segments':[segment]}
    if job_language:
        job['language_code'] = job_language
    monkeypatch.setattr(dub_pipeline, '_dub_jobs', {'job':job})
    with db.db_conn() as conn:
        conn.execute('INSERT INTO studio_projects (id,name,audio_path,duration,state_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?)',
                     ('project','Translation draft',str(source),1,json.dumps(state),0,0))
    response = client.post('/project-bundles/export', json={'kind':'dub','project_id':'project'})
    assert response.status_code == 200, response.text
    archive = tmp_path / 'draft.vsproject'
    archive.write_bytes(response.content)
    manifest, _ = bundles.read_bundle(archive, tmp_path / 'unpacked')
    document = manifest['document']
    assert [target['code'] for target in document['targets']] == expected_codes
    assert document['segments'][0]['translations'] == expected_translations
    if selected:
        assert document['targets'][0] == {'code':selected,'name':'Selected language'}
    restored = client.post('/project-bundles/import?allow_voices=true', content=response.content)
    assert restored.status_code == 200, restored.text
    with db.db_conn() as conn:
        row = conn.execute('SELECT state_json FROM studio_projects WHERE id=?', (restored.json()['projectId'],)).fetchone()
        imported = json.loads(row[0])
    assert imported['dubLangCode'] == expected_codes[0]
    assert imported['dubSegments'][0]['translations'] == expected_translations
    assert set(imported['dubTracks']) == set(expected_codes)
