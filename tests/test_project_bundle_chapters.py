"""Stories chapter rows survive desktop/phone archive edits as unspoken headings."""
import copy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services import project_bundle as bundles


def story():
    return {'name':'Book','mode':'stories','draft':{
        'voice':'preset:0','cast':[{'id':'alice','name':'Alice','profileId':'preset:1'}],
        'lines':[
            {'id':'chapter-one','character':'narrator','text':'# First','profileId':None},
            {'id':'speech-one','character':'alice','text':'First words','profileId':None,'speed':.8},
            {'id':'chapter-two','character':'narrator','text':'# Second','profileId':None},
            {'id':'speech-two','character':'alice','text':'Second words','profileId':None},
        ]}}


def manuscript(document):
    return {'kind':'stories','title':'Book','document':document,'voices':[],'assets':[]}


@pytest.mark.parametrize('heading',['# Chapter 1','  #\tChapter 1  '])
def test_export_heading_is_unspoken_and_has_no_voice_role(heading):
    project = story()
    project['draft']['lines'] = [{'id':'chapter','character':'unused','text':heading,
                                  'profileId':'preset:2','speed':1.2}]
    document = bundles.manuscript_document(project)
    assert document['script'] == '# Chapter 1'
    assert document['cast'] == document['roles'] == {}


@pytest.mark.parametrize('text',['## Subtitle','#hashtag','#','### Subtitle'])
def test_export_does_not_promote_spoken_hash_text_to_chapters(text):
    project = story()
    project['draft']['lines'] = [{'id':'speech','character':'alice','text':text}]
    assert bundles.manuscript_document(project)['script'] == '[voice:alice] '+text


def test_phone_only_chapters_preserve_order_without_resetting_active_voice():
    document = {'script':'# First\n[voice:alice] First words\n# Second\nSecond words',
                'defaultVoice':'preset:0','cast':{'alice':'preset:1'}}
    draft = bundles.desktop_manuscript(manuscript(document))['draft']
    assert [row['text'] for row in draft['lines']] == ['# First','First words','# Second','Second words']
    assert [row['character'] for row in draft['lines']] == ['narrator','alice','narrator','alice']
    assert len({row['id'] for row in draft['lines']}) == 4
    for row in draft['lines'][::2]:
        assert row['profileId'] is None and row['speed'] is None


def test_phone_body_edit_retains_heading_ids_and_duplicate_titles():
    project = story()
    project['draft']['lines'][2]['text'] = '# First'
    original = copy.deepcopy(project)
    document = bundles.manuscript_document(project)
    document['script'] = '# First\n[voice:alice] Changed words\n# First\n[voice:alice] Second words'
    draft = bundles.desktop_manuscript(manuscript(document))['draft']
    assert [row['text'] for row in draft['lines']] == ['# First','Changed words','# First','Second words']
    assert [row['id'] for row in draft['lines'][::2]] == ['chapter-one','chapter-two']
    assert project == original


def test_unchanged_desktop_roundtrip_preserves_exact_heading_rows():
    project = story()
    project['draft']['lines'][0]['text'] = '\t#\tFirst  '
    result = bundles.desktop_manuscript(manuscript(bundles.manuscript_document(project)))
    assert result['draft']['lines'] == project['draft']['lines']


def test_story_archive_routes_roundtrip_phone_chapter_edits(tmp_path,monkeypatch):
    from api.routers import project_bundles as routes
    from core import db
    monkeypatch.setattr(db,'DB_PATH',tmp_path/'library.db')
    db.init_db()
    for name in ('OUTPUTS_DIR','VOICES_DIR','DUB_DIR'):
        directory = tmp_path/name; directory.mkdir()
        monkeypatch.setattr(routes,name,directory)
    app = FastAPI(); app.include_router(routes.router)
    client = TestClient(app, client=("127.0.0.1", 50000))
    exported = client.post('/project-bundles/export',json={'kind':'stories','project':story()})
    assert exported.status_code == 200,exported.text
    archive = tmp_path/'desktop.vsproject'
    archive.write_bytes(exported.content)
    manifest,files = bundles.read_bundle(archive,tmp_path/'desktop-read')
    assert manifest['document']['script'].splitlines()[::2] == ['# First','# Second']
    manifest['document']['script'] = '# Second\n[voice:alice] Edited words\n# New\nNew words\n# First\nLast words'
    phone = tmp_path/'phone.vsproject';bundles.write_bundle(manifest,files,phone)
    imported = client.post('/project-bundles/import',content=phone.read_bytes())
    assert imported.status_code == 200,imported.text
    project = imported.json()['project']
    rows = project['draft']['lines']
    assert [row['text'] for row in rows] == ['# Second','Edited words','# New','New words','# First','Last words']
    assert [rows[index]['id'] for index in (0,4)] == ['chapter-two','chapter-one']
    returned = client.post('/project-bundles/export',json={'kind':'stories','project':project})
    assert returned.status_code == 200,returned.text
    archive = tmp_path/'returned.vsproject'
    archive.write_bytes(returned.content)
    final,_ = bundles.read_bundle(archive,tmp_path/'returned-read')
    assert final['document']['script'].splitlines()[::2] == ['# Second','# New','# First']
    assert [row['character'] for row in rows[1::2]] == ['alice']*3
