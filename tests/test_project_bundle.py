from __future__ import annotations
import copy
import hashlib
import io
import json
from pathlib import Path
import stat
import struct
import zipfile
import pytest
from core.safe_archive import ArchiveError
from services import project_bundle as b

FIXTURES = Path(__file__).resolve().parents[1]/'mobile/contract/project-fixtures'


def test_shared_desktop_book_roundtrip_keeps_settings_and_phone_edits_win(tmp_path):
    manifest, files = b.read_bundle(FIXTURES/'desktop-book.vsproject',tmp_path/'one')
    original = manifest['document']['desktop']['draft']
    manifest['document']['script'] = '# First chapter\nEdited on the phone.'
    manifest['document']['speed'] = .75
    manifest['document']['pronunciations']['phone'] = 'fone'
    b.write_bundle(manifest,files,tmp_path/'phone.vsproject')
    imported,_ = b.read_bundle(tmp_path/'phone.vsproject',tmp_path/'two')
    result = b.desktop_manuscript(imported)
    assert result['draft']['script'].endswith('Edited on the phone.')
    assert result['draft']['globalSpeed'] == .75
    assert result['draft']['metadata'] == original['metadata']
    assert result['draft']['overrides'] == original['overrides']
    assert result['draft']['customFutureSetting'] == original['customFutureSetting']
    assert result['draft']['loudness'] == 'podcast'
    assert isinstance(result['draft']['lines'],list) and isinstance(result['draft']['cast'],list)


def test_story_voice_changes_apply_even_when_manuscript_text_is_unchanged():
    project={'name':'Story','mode':'stories','draft':{'script':'','globalSpeed':1,'cast':[{'id':'alice','name':'Alice','profileId':'old'}],'lines':[{'id':'line-1','character':'alice','text':'One','profileId':None,'speed':1.1},{'id':'line-2','character':'alice','text':'Two','profileId':'override','customLineSetting':'keep'}],'lexicon':[]}}
    doc=b.manuscript_document(project);doc['cast']['alice']='new';doc['cast']['line-1']='changed-override'
    result=b.desktop_manuscript({'kind':'stories','title':'Story','document':doc})
    assert result['draft']['cast'][0]['profileId']=='new'
    assert result['draft']['lines'][1]['profileId']=='changed-override'
    doc['script']=doc['script'].replace('One','Edited')
    result=b.desktop_manuscript({'kind':'stories','title':'Story','document':doc})
    assert result['draft']['lines'][1]['id']=='line-2'
    assert result['draft']['lines'][1]['customLineSetting']=='keep'
    assert result['draft']['lines'][1]['character']=='alice'


def test_shared_dub_roundtrip_preserves_source_refs_timing_translations_and_gains(tmp_path):
    manifest,files=b.read_bundle(FIXTURES/'desktop-dub.vsproject',tmp_path/'one')
    b.write_bundle(manifest,files,tmp_path/'copy.vsproject')
    result,restored=b.read_bundle(tmp_path/'copy.vsproject',tmp_path/'two')
    assert result['document']==manifest['document']
    assert all(restored[name].read_bytes()==source.read_bytes() for name,source in files.items())
    assert result['document']['segments'][0]['id']=='segment-stable'


@pytest.mark.parametrize('name',['../outside','/absolute','C:/outside','assets\\outside','assets/CON.wav','assets/one/../outside','assets/file./bad'])
def test_archive_paths_are_portable_and_never_extracted(tmp_path,name):
    source=tmp_path/'bad.zip'
    with zipfile.ZipFile(source,'w') as z:z.writestr(name,b'bad')
    with pytest.raises(ArchiveError):b.read_bundle(source,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_symlinks_duplicates_compressed_bombs_and_corrupt_headers_are_refused(tmp_path):
    for kind in ['symlink','duplicate','deflate','header']:
        source=tmp_path/(kind+'.zip')
        with zipfile.ZipFile(source,'w',compression=zipfile.ZIP_DEFLATED if kind=='deflate' else zipfile.ZIP_STORED) as z:
            entry=zipfile.ZipInfo('manifest.json');entry.create_system=3
            if kind=='symlink':entry.external_attr=(stat.S_IFLNK|0o777)<<16
            entry.compress_type=zipfile.ZIP_DEFLATED if kind=='deflate' else zipfile.ZIP_STORED
            z.writestr(entry,(FIXTURES/'desktop-book.json').read_bytes())
            if kind=='duplicate':z.writestr('MANIFEST.JSON',b'{}')
        if kind=='header':
            data=bytearray(source.read_bytes());data[8]=8;source.write_bytes(data)
        with pytest.raises(ArchiveError):b.read_bundle(source,tmp_path/(kind+'-out'))


def test_fingerprints_extra_assets_and_future_versions_fail_without_publication(tmp_path):
    base=json.loads((FIXTURES/'desktop-book.json').read_text(encoding="utf-8"))
    for index,change in enumerate([lambda m:m.update(version=2),lambda m:m.update(format='other'),lambda m:m.update(voices=[{'id':'x','metadata':{},'reference':'missing.wav'}]),lambda m:m['document'].update(desktop={'draft':{'lines':'bad'}})]):
        manifest=copy.deepcopy(base);change(manifest)
        with pytest.raises(ArchiveError):b.validate_manifest(manifest)
    data=b'content';manifest=copy.deepcopy(base);manifest['assets']=[{'path':'assets/data.txt','size':len(data),'sha256':'0'*64}]
    source=tmp_path/'corrupt.vsproject'
    with zipfile.ZipFile(source,'w') as z:z.writestr('manifest.json',json.dumps(manifest));z.writestr('assets/data.txt',data)
    with pytest.raises(ArchiveError):b.read_bundle(source,tmp_path/'out')
    assert not (tmp_path/'out').exists()


def test_ownership_credentials_consent_and_absolute_paths_never_gain_privileges():
    value={'credentials':{'token':'secret'},'apiKey':'secret','verified_own_voice':True,'consent_text':'yes','consent_audio_path':'/secret','consent_recorded_at':123,'native':{'request':{'consentAt':999}},'video_path':'/etc/passwd','text':'User manuscript stays exact'}
    cleaned=b.clean_document(value)
    assert cleaned=={'native':{'request':{}},'video_path':None,'text':'User manuscript stays exact'}


@pytest.mark.parametrize('change',[lambda d:d.update(targets=[]),lambda d:d.update(targets=[{'code':'../x','name':'Bad'}]),lambda d:d['segments'][0].update(translations={'fr':{}}),lambda d:d['segments'][0].update(reference='assets/missing.pcm'),lambda d:d['segments'][0].update(gain=float('nan'))])
def test_malformed_timeline_fields_are_rejected_before_import(change,tmp_path):
    manifest,_=b.read_bundle(FIXTURES/'desktop-dub.vsproject',tmp_path/'valid')
    change(manifest['document'])
    with pytest.raises(ArchiveError):b.validate_manifest(manifest)


def test_repeated_phone_paragraphs_keep_voice_but_get_unique_desktop_line_ids():
    project = {'name':'Story','mode':'stories','draft':{
        'cast':[{'id':'alice','name':'Alice','profileId':'character-voice'}],
        'lines':[{'id':'original-line','character':'alice','text':'Original','profileId':'line-voice','customLineSetting':'keep'}],
        'lexicon':[]}}
    document = b.manuscript_document(project)
    document['script'] = '[voice:line-0] First paragraph\nSecond paragraph\n[voice:line-0] Third paragraph'
    document['cast']['line-0'] = 'phone-voice'
    result = b.desktop_manuscript({'kind':'stories','title':'Story','document':document})
    lines = result['draft']['lines']
    assert [line['text'] for line in lines] == ['First paragraph','Second paragraph','Third paragraph']
    assert lines[0]['id'] == 'original-line'
    assert len({line['id'] for line in lines}) == 3
    assert all(line['profileId'] == 'phone-voice' and line['character'] == 'alice' for line in lines)
    assert all(line['customLineSetting'] == 'keep' for line in lines)


@pytest.mark.parametrize('change', [
    lambda document: document.update(pronunciations=[]),
    lambda document: document.update(pronunciations={'word':{}}),
    lambda document: document.update(roles={'role':'invalid'}),
    lambda document: document.update(roles={'role':{'lineId':[]}}),
    lambda document: document['desktop']['draft'].update(lines=[{'id':[], 'text':'Bad'}]),
])
def test_malformed_editable_manuscript_collections_are_rejected(change):
    manifest = json.loads((FIXTURES/'desktop-book.json').read_text(encoding="utf-8"))
    change(manifest['document'])
    with pytest.raises(ArchiveError): b.validate_manifest(manifest)


@pytest.mark.parametrize('draft', [
    {'lines':[{'character':[], 'text':'Bad'}]},
    {'cast':'invalid'},
    {'handoff':{'document':'invalid'}},
])
def test_export_rejects_invalid_desktop_draft_before_resolving_bindings(draft):
    with pytest.raises(ArchiveError): b.manuscript_document({'mode':'stories','draft':draft})


@pytest.mark.parametrize('collection', ['cast','lines'])
def test_duplicate_desktop_identities_are_rejected_before_unchanged_snapshot_import(collection):
    manifest = json.loads((FIXTURES/'desktop-book.json').read_text(encoding="utf-8"))
    manifest['document']['desktop']['draft'][collection] = [{'id':'same'},{'id':'same'}]
    with pytest.raises(ArchiveError): b.validate_manifest(manifest)
    with pytest.raises(ArchiveError): b.manuscript_document(manifest['document']['desktop'])
