"""Explicit local/paired project handoff; each import creates a new project and voices."""
from __future__ import annotations
import copy
import json
import logging
import math
from pathlib import Path
import shutil
import time
import threading
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request
from api.dependencies import require_admin
from fastapi.responses import FileResponse
from pydantic import BaseModel
from core.config import OUTPUTS_DIR, VOICES_DIR, DUB_DIR
from core.db import db_conn
from core import event_bus
from core.path_security import resolve_within, UnsafePath
from core.safe_archive import ArchiveError
from services import project_bundle as bundles

router = APIRouter(prefix='/project-bundles', tags=['Project handoff'], dependencies=[Depends(require_admin)])
logger = logging.getLogger(__name__)
EXPORT_RETENTION_SECONDS = 24 * 60 * 60
MAX_RETAINED_EXPORTS = 20
MAX_RETAINED_EXPORT_BYTES = 2 * bundles.MAX_TOTAL
_transfer_lock = threading.RLock()
_active_transfers: dict[Path, int] = {}


class _BundleResponse(FileResponse):
    """Keep a retained export alive until its last in-flight download finishes."""
    def __init__(self, path: Path, **kwargs):
        super().__init__(path, **kwargs)
        self.directory = path.parent
        with _transfer_lock:
            _active_transfers[self.directory] = _active_transfers.get(self.directory, 0) + 1

    async def __call__(self, scope, receive, send):
        try:
            await super().__call__(scope, receive, send)
        finally:
            with _transfer_lock:
                count = _active_transfers[self.directory] - 1
                if count:
                    _active_transfers[self.directory] = count
                else:
                    del _active_transfers[self.directory]
                _prune_exports()


def _valid_index(value):
    if not isinstance(value,dict):
        return False
    created = value.get('createdAt')
    return isinstance(created,(int,float)) and not isinstance(created,bool) and math.isfinite(created)


def _prune_exports():
    """Only completed outgoing copies expire; imported project assets are permanent."""
    with _transfer_lock:
        exports = []
        for directory in _root().iterdir():
            if not directory.is_dir() or directory.is_symlink():
                continue
            try:
                if (directory / 'index.json').is_symlink() or (directory / 'project.vsproject').is_symlink():
                    continue
                index = json.loads((directory / 'index.json').read_text())
                if not _valid_index(index) or index.get('incoming') is not False:
                    continue
                created = float(index['createdAt'])
                size = (directory / 'project.vsproject').stat().st_size
                exports.append((created, directory, size))
            except (ValueError, TypeError, KeyError, OSError):
                # In-progress or interrupted writes are not eligible for retention cleanup.
                continue
        retained_bytes = 0
        retained_count = 0
        for created, directory, size in sorted(exports,reverse=True):
            keep = (time.time() - created <= EXPORT_RETENTION_SECONDS
                    and retained_count < MAX_RETAINED_EXPORTS
                    and retained_bytes + size <= MAX_RETAINED_EXPORT_BYTES)
            if keep:
                retained_bytes += size
                retained_count += 1
            elif directory not in _active_transfers:
                shutil.rmtree(directory, ignore_errors=True)


def _root() -> Path:
    root = (Path(OUTPUTS_DIR)/'project_handoff').resolve(); root.mkdir(parents=True, exist_ok=True); return root


def _directory(identifier: str) -> Path:
    try:
        if str(uuid.UUID(identifier)) != identifier:
            raise ValueError()
    except ValueError as exc:
        raise HTTPException(400, 'Invalid project bundle id') from exc
    try:
        return resolve_within(_root(), str(uuid.UUID(identifier)))
    except UnsafePath as exc:
        raise HTTPException(400, 'Invalid project bundle path') from exc


def _bundle_file(identifier: str, filename: str) -> Path:
    try:
        return resolve_within(_directory(identifier), filename)
    except UnsafePath as exc:
        raise HTTPException(400, 'Invalid project bundle path') from exc


def _asset(value, roots) -> Path:
    for root in roots:
        try:
            path = resolve_within(root, value)
            if path.is_file() and not path.is_symlink(): return path
        except (UnsafePath, TypeError):
            # A relative asset may belong to another explicitly allowed root.
            continue
    raise ArchiveError(400, 'Project references a missing or unsafe local asset')


def _asset_field(key: str) -> bool:
    return 'path' in key.lower() or key.lower() in {'ref_audio','filename','file','output'}


def _collect_paths(value, files: dict, roots, prefix='assets/desktop', depth=0):
    """Replace recognized filesystem fields with asset references; arbitrary strings never open files."""
    if depth > 64: raise ArchiveError(400,'Project document is nested too deeply')
    if isinstance(value, dict):
        out = {}
        for key, child in value.items():
            if key not in bundles.clean_document({key:None}): continue
            if key in {'native','models'}: out[key] = bundles.clean_document(child); continue
            if isinstance(child, str) and child and _asset_field(key) and not child.startswith('asset:'):
                try:
                    path = _asset(child, roots)
                except ArchiveError:
                    # Plain display filenames are not filesystem capabilities.
                    if '/' not in child and '\\' not in child: out[key] = child
                    else: out[key] = None
                    continue
                existing = next((name for name,p in files.items() if p == path), None)
                name = existing or f'{prefix}/{uuid.uuid4().hex}{path.suffix.lower() or ".media"}'
                files[name] = path; out[key] = 'asset:'+name
            else: out[key] = _collect_paths(child,files,roots,prefix,depth+1)
        return out
    if isinstance(value, list):
        if len(value) > 20000: raise ArchiveError(413,'Project array is too large')
        return [_collect_paths(child,files,roots,prefix,depth+1) for child in value]
    return value


def _restore_paths(value, files, *, asset=False, install_asset=None):
    if isinstance(value,str) and asset and value.startswith('asset:'):
        if value[6:] not in files: raise ArchiveError(400,'Project asset reference is missing')
        return str(install_asset(value[6:]) if install_asset else files[value[6:]])
    if isinstance(value,dict):
        result = {}
        descriptor = {'path','source','sha256','size'}.issubset(value)
        for key,child in value.items():
            if key in {'native','models'}: result[key] = child; continue
            if key == 'path' and descriptor:
                result[key] = bundles.archive_path(child); continue
            capability = _asset_field(key) or descriptor and key == 'source'
            if capability and isinstance(child,str) and child and not child.startswith('asset:'):
                # Only manifest references can install a filesystem capability.
                result[key] = '' if key == 'output' else None
            else: result[key] = _restore_paths(child,files,asset=capability,install_asset=install_asset)
        return result
    if isinstance(value,list): return [_restore_paths(v,files,install_asset=install_asset) for v in value]
    return value


def _voice_ids(value):
    found = set()
    def visit(node, key=''):
        if isinstance(node,dict):
            for k,v in node.items():
                if k in {'native','models'}: continue
                if k in {'defaultVoice','profileId','voice','voiceID','voice_id','profile_id'} and isinstance(v,str) and v and not v.startswith('preset:'): found.add(v)
                elif k in {'voiceCast','cast'} and isinstance(v,dict): found.update(x for x in v.values() if isinstance(x,str) and x and not x.startswith('preset:'))
                visit(v,k)
        elif isinstance(node,list):
            for child in node: visit(child,key)
    visit(value); return found


def _rewrite_voices(value, mapping, *, strict=True):
    if isinstance(value,dict):
        result = {}
        for key,child in value.items():
            if key in {'native','models'}: result[key] = child; continue
            if key in {'defaultVoice','profileId','voice','voiceID','voice_id','profile_id'} and isinstance(child,str):
                if strict and child and not child.startswith('preset:') and child not in mapping: raise ArchiveError(400,'A project voice reference is not bundled')
                result[key] = mapping.get(child)
            elif key in {'voiceCast','cast'} and isinstance(child,dict):
                if strict and any(isinstance(v,str) and v and not v.startswith('preset:') and v not in mapping for v in child.values()): raise ArchiveError(400,'A project cast voice is not bundled')
                result[key] = {k:mapping[v] for k,v in child.items() if isinstance(v,str) and v in mapping}
            else: result[key] = _rewrite_voices(child,mapping,strict=strict)
        return result
    if isinstance(value,list): return [_rewrite_voices(child,mapping,strict=strict) for child in value]
    return value


def _retain_assets(memory,files):
    if not isinstance(memory,dict): return
    assets = memory.get('assets',[])
    if not isinstance(assets,list) or len(assets) > bundles.MAX_FILES: raise ArchiveError(400,'Invalid retained project assets')
    for asset in assets:
        if not isinstance(asset,dict): raise ArchiveError(400,'Invalid retained project asset')
        name = bundles.archive_path(asset.get('path'))
        if name in files: raise ArchiveError(400,'Duplicate retained project asset')
        files[name] = _asset(asset.get('source'),[OUTPUTS_DIR])


def _export_voices(document, files):
    # Keep marketplace v1 metadata, rather than define a second voice recipe shape.
    from api.routers.marketplace import _bundle_metadata
    voices = []
    for identifier in sorted(_voice_ids(document)):
        with db_conn() as conn: row = conn.execute('SELECT * FROM voice_profiles WHERE id=?',(identifier,)).fetchone()
        if not row: raise ArchiveError(400,'A project voice is missing; choose an available voice before exporting')
        profile = dict(row); source = _asset(profile.get('ref_audio_path'),[VOICES_DIR])
        reference = f'voices/{uuid.uuid4().hex}/ref_audio{source.suffix.lower()}'
        files[reference] = source
        voice = {'id':identifier,'metadata':_bundle_metadata(profile),'reference':reference}
        if profile.get('locked_audio_path'):
            locked = _asset(profile['locked_audio_path'],[VOICES_DIR]); name = str(Path(reference).parent/'locked_audio')+locked.suffix.lower(); files[name] = locked; voice['locked'] = name
        voices.append(voice)
    return voices


def _automatic_voice(value):
    return isinstance(value,str) and value.startswith(('auto:', 'auto-seg:'))


def _strip_automatic_bindings(value):
    """Source clips are portable assets, never persistent voice-profile IDs."""
    if isinstance(value,dict):
        result = {}
        for key, child in value.items():
            if key in {'profile_id','profileId','voiceID','voice_id','voice','defaultVoice'} and _automatic_voice(child):
                result[key] = None
            elif key in {'voiceCast','cast'} and isinstance(child,dict):
                result[key] = {role:voice for role,voice in child.items() if not _automatic_voice(voice)}
            else:
                result[key] = _strip_automatic_bindings(child)
        return result
    if isinstance(value,list):
        return [_strip_automatic_bindings(child) for child in value]
    return value


def _source_reference(job, segment, *, consistent=False):
    # Reuse the renderer's source-speaker selection, including cross-speaker
    # overrides and consistent-voice mode, instead of exporting a guessed clip.
    from api.routers.dub_generate import _find_speaker_clone, _speaker_key_for_segment, _ref_within_limit, resolve_consistent_ref
    from services.speaker_clone import auto_profile_id
    binding = segment.get('profile_id')
    segment_id = str(segment['id'])
    if isinstance(binding,str) and binding.startswith('auto-seg:'):
        selected = binding[len('auto-seg:'):]
        if consistent and selected == segment_id:
            speaker_key = _speaker_key_for_segment(job, selected)
            reference = resolve_consistent_ref(job,speaker_key) if speaker_key else None
            if reference:
                return reference
        return (job.get('segment_clones') or {}).get(selected)
    if isinstance(binding,str) and binding.startswith('auto:'):
        speaker_key = binding[len('auto:'):]
        if consistent:
            return resolve_consistent_ref(job,speaker_key)
        current = _speaker_key_for_segment(job,segment_id)
        reference = (job.get('segment_clones') or {}).get(segment_id) if current is None or current == speaker_key else None
        if _ref_within_limit(reference):
            return reference
        reference = _find_speaker_clone(job.get('speaker_clones') or {},speaker_key)
        return reference if _ref_within_limit(reference) else resolve_consistent_ref(job,speaker_key)
    if not binding:
        return (job.get('segment_clones') or {}).get(segment_id) or _find_speaker_clone(job.get('speaker_clones') or {},auto_profile_id(segment.get('speaker_id') or 'Speaker 1')[len('auto:'):])
    return None


class BundleExport(BaseModel):
    kind: str
    project_id: str | None = None
    project: dict | None = None


@router.post('/export')
def export_project_bundle(body: BundleExport):
    identifier = str(uuid.uuid4()); directory = _directory(identifier); directory.mkdir()
    files = {}
    try:
        if body.kind in {'stories','audiobook'}:
            if not body.project: raise ArchiveError(400,'A saved longform draft is required')
            draft = body.project.get('draft')
            _retain_assets(draft.get('handoff') if isinstance(draft,dict) else None,files)
            raw = _collect_paths(copy.deepcopy(body.project),files,[OUTPUTS_DIR])
            document = bundles.manuscript_document(raw); title = raw.get('name') or raw.get('draft',{}).get('title') or 'Project'
        elif body.kind == 'dub':
            with db_conn() as conn: row = conn.execute('SELECT * FROM studio_projects WHERE id=?',(body.project_id,)).fetchone()
            if not row: raise HTTPException(404,'Project not found')
            project = dict(row); state = json.loads(project.get('state_json') or '{}')
            memory = state.pop('handoff',None); _retain_assets(memory,files)
            from services import dub_pipeline
            job = dub_pipeline.get_job(str(state.get('dubJobId') or '')) or {}
            source = _asset(job.get('video_path') or project.get('video_path') or project.get('audio_path'),[DUB_DIR,OUTPUTS_DIR])
            source_name = next((name for name,path in files.items() if path == source),None) or 'assets/source'+source.suffix.lower(); files[source_name] = source
            segments = []
            for index,seg in enumerate(state.get('dubSegments') or job.get('segments') or []):
                item = dict(seg); item['id'] = str(item.get('id',index)); item['text'] = item.get('text_original') or item.get('text',''); item.setdefault('translations',{})
                for field in ('speaker','voiceID','reference','referenceText'): item.pop(field,None)
                if seg.get('speaker_id'): item['speaker'] = str(seg['speaker_id'])
                if _automatic_voice(seg.get('profile_id')):
                    item.pop('profile_id',None)
                elif seg.get('profile_id'):
                    item['voiceID'] = seg['profile_id']
                if 'gain' in seg: item['muted'] = seg['gain'] == 0
                language = state.get('dubLangCode')
                # Modern drafts keep authoritative translations by language. A
                # switch to an untranslated target can leave the outgoing text
                # visible, so only seed legacy rows without that map.
                if (language and 'translations' not in seg and seg.get('text_original')
                        and seg.get('text') and seg['text'] != seg['text_original']):
                    item['translations'] = {language:seg['text']}
                reference = _source_reference(job,{**seg,'id':item['id']},consistent=state.get('voiceMatch') == 'consistent')
                if _automatic_voice(seg.get('profile_id')) and not (isinstance(reference,dict) and reference.get('ref_audio')):
                    raise ArchiveError(400,'A source voice reference is missing; prepare the source voice before exporting')
                if isinstance(reference,dict) and reference.get('ref_audio'):
                    path = _asset(reference['ref_audio'],[DUB_DIR,OUTPUTS_DIR,VOICES_DIR])
                    name = next((name for name,existing in files.items() if existing == path),None) or 'assets/references/'+uuid.uuid4().hex+path.suffix.lower()
                    files[name] = path; item['reference'] = name; item['referenceText'] = reference.get('ref_text') or item['text']
                segments.append(item)
            # Imports open the first target. Preserve the current selection,
            # rendered tracks and translated drafts that have no audio yet.
            current = state.get('dubLangCode')
            target_codes = [current] if current and current != 'original' else []
            candidates = [code for code in state.get('dubTracks',[]) if code != 'original']
            for segment in segments:
                if isinstance(segment.get('translations'),dict):
                    candidates.extend(segment['translations'])
            for code in candidates:
                if code not in target_codes:
                    target_codes.append(code)
            targets = [{'code':code,'name':(state.get('dubLang') or code) if code == current else code}
                       for code in target_codes]
            document = copy.deepcopy(memory.get('document',{})) if isinstance(memory,dict) else {}
            document.update({'duration':project.get('duration') or job.get('duration') or max([0]+[s.get('end',0) for s in segments]),'source':source_name,'segments':segments,'targets':targets,'audioOnly':state.get('inputType') == 'audio','desktop':{'name':project['name'],'state':_collect_paths(_strip_automatic_bindings(state),files,[DUB_DIR,OUTPUTS_DIR,VOICES_DIR]),'job':_collect_paths(_strip_automatic_bindings(job),files,[DUB_DIR,OUTPUTS_DIR,VOICES_DIR])}})
            title = project['name']
        else: raise ArchiveError(400,'Unsupported project type')
        manifest = {'format':bundles.FORMAT,'version':1,'kind':body.kind,'title':title,'document':document,'voices':_export_voices(document,files),'assets':[],'createdAt':time.time()}
        manifest = bundles.write_bundle(manifest,files,directory/'project.vsproject')
        with _transfer_lock:
            (directory/'index.json').write_text(json.dumps({'id':identifier,'title':manifest['title'],'kind':body.kind,'createdAt':time.time(),'incoming':False}))
            response = _BundleResponse(directory/'project.vsproject',media_type='application/zip',filename=title+'.vsproject')
            _prune_exports()
        return response
    except ArchiveError as exc:
        shutil.rmtree(directory,ignore_errors=True); raise HTTPException(exc.status,detail={'code':'project_archive_invalid','message':exc.detail}) from exc
    except BaseException:
        shutil.rmtree(directory,ignore_errors=True); raise


def _install_voices(manifest, files, connection, cleanup):
    mapping = {}
    for voice in manifest['voices']:
        metadata = voice['metadata']; identifier = uuid.uuid4().hex[:12]; source = files[voice['reference']]
        name = identifier+source.suffix.lower(); target = Path(VOICES_DIR)/name; shutil.copyfile(source,target); cleanup.append(target)
        locked_name = ''
        if voice.get('locked') in files:
            locked = files[voice['locked']]; locked_name = identifier+'_locked'+locked.suffix.lower(); target = Path(VOICES_DIR)/locked_name; shutil.copyfile(locked,target); cleanup.append(target)
        connection.execute('''INSERT INTO voice_profiles (id,name,ref_audio_path,ref_text,instruct,language,seed,personality,is_locked,locked_audio_path,created_at,kind,vd_states) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (identifier,str(metadata.get('profile_name') or manifest['title'])[:200],name,str(metadata.get('ref_text') or ''),str(metadata.get('instruct') or ''),str(metadata.get('language') or 'Auto'),metadata.get('seed') if isinstance(metadata.get('seed'),int) else None,str(metadata.get('personality') or ''),int(bool(locked_name)),locked_name,time.time(),'design' if metadata.get('kind') == 'design' else 'clone',metadata.get('vd_states') if isinstance(metadata.get('vd_states'),str) else None))
        mapping[voice['id']] = identifier
        if voice['id'].startswith('phone:'): mapping[voice['id'][6:]] = identifier
    return mapping


def _pcm_wave(source: Path,target: Path,*,max_frames: int) -> Path:
    import numpy as np
    import soundfile as sf
    size = source.stat().st_size
    if size % 4 or not 0 < size <= max_frames*4:
        raise ArchiveError(400,'Invalid PCM reference')
    with sf.SoundFile(target,'w',samplerate=24000,channels=1,subtype='PCM_16') as output, source.open('rb') as stream:
        while block := stream.read(8192*4):
            samples = np.frombuffer(block,dtype='<f4')
            if not np.isfinite(samples).all(): raise ArchiveError(400,'Invalid PCM samples')
            output.write(samples)
    return target


def _import(directory: Path,source: Path,allow_voices: bool):
    manifest, files = bundles.read_bundle(source,directory/'content')
    if (manifest['voices'] or manifest['kind']=='dub') and not allow_voices: raise ArchiveError(403,'Explicit permission to use the bundled voices is required')
    cleanup = []
    cleanup_directories = []
    try:
        with db_conn() as conn:
            mapping = _install_voices(manifest,files,conn,cleanup)
            manifest = copy.deepcopy(manifest)
            # Canonical fields are authoritative. A phone may replace every voice,
            # leaving unused IDs in its opaque desktop snapshot; discard only those.
            desktop = manifest['document'].pop('desktop', None)
            manifest['document'] = _rewrite_voices(manifest['document'],mapping)
            if desktop is not None:
                manifest['document']['desktop'] = _rewrite_voices(desktop,mapping,strict=False)
            if manifest['kind'] != 'dub':
                project = _restore_paths(bundles.desktop_manuscript(manifest),files)
                output = project.get('draft',{}).get('output')
                if output:
                    # Electron builds /audio/<output>, whose root is OUTPUTS_DIR.
                    project['draft']['output'] = resolve_within(OUTPUTS_DIR,output).relative_to(Path(OUTPUTS_DIR).resolve()).as_posix()
                # Covers must enter the existing audiobook cover directory.
                cover = project.get('draft',{}).get('cover')
                if isinstance(cover,dict) and cover.get('path'):
                    src = Path(cover['path']); dest = Path(OUTPUTS_DIR)/'audiobook_covers'/(uuid.uuid4().hex+src.suffix.lower()); dest.parent.mkdir(exist_ok=True); shutil.copyfile(src,dest); cleanup.append(dest); cover['path'] = str(dest)
                result = {'kind':manifest['kind'],'title':manifest['title'],'project':project}
            else:
                doc = manifest['document']; identifier = str(uuid.uuid4())[:8]
                # The preview/render/export APIs require job-owned media, not
                # paths inside the retained handoff archive under OUTPUTS_DIR.
                job_directory = resolve_within(DUB_DIR,identifier)
                job_directory.mkdir()
                cleanup_directories.append(job_directory)
                installed = {}
                def install_asset(name):
                    if name not in installed:
                        if name not in files:
                            raise ArchiveError(400,'Project asset reference is missing')
                        target = resolve_within(job_directory,'imported/'+bundles.archive_path(name))
                        target.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(files[name],target)
                        installed[name] = target
                    return installed[name]
                desktop = _restore_paths(doc.get('desktop') or {},files,install_asset=install_asset); state = desktop.get('state') or {}
                job = desktop.get('job') or {}
                target = (doc.get('targets') or [{'code':'en','name':'English'}])[0]
                segments = []
                references = {}
                for index, value in enumerate(doc['segments']):
                    segment = dict(value)
                    segment.update(id=str(value.get('id',index)),text_original=value['text'],text=(value.get('translations') or {}).get(target['code']) or value['text'])
                    if value.get('speaker'): segment['speaker_id'] = value['speaker']
                    if value.get('voiceID'): segment['profile_id'] = value['voiceID']
                    if value.get('muted'): segment['gain'] = 0
                    if value.get('reference'):
                        reference = install_asset(value['reference'])
                        if reference.suffix == '.pcm':
                            reference = _pcm_wave(reference,job_directory/('reference-'+uuid.uuid4().hex+'.wav'),max_frames=720000)
                        # Imported clips are explicit voice choices, not the renderer's
                        # auto-seg:<own-id> defaults that consistent mode can replace.
                        reference_id = 'handoff-'+uuid.uuid4().hex
                        references[reference_id] = {'ref_audio':str(reference),'ref_text':value.get('referenceText') or value['text']}
                        if not value.get('voiceID'):
                            segment['profile_id'] = 'auto-seg:'+reference_id
                    segments.append(segment)
                if references: job['segment_clones'] = references
                source_asset = str(install_asset(doc['source'])); audio = job.get('audio_path')
                if not audio:
                    # Pure WAV conversion, not synthesis; retain source provenance.
                    if doc.get('original') in files:
                        audio = str(_pcm_wave(install_asset(doc['original']),job_directory/'original.wav',max_frames=345600000))
                    elif Path(source_asset).suffix.lower() in {'.wav','.flac','.ogg'}: audio = source_asset
                    else:
                        from services.ffmpeg_utils import find_ffmpeg, local_inputs_only
                        import subprocess
                        audio = str(job_directory/'original.wav')
                        subprocess.run([find_ffmpeg(),'-nostdin','-v','error',*local_inputs_only(),'-i',source_asset,'-vn','-ac','1','-ar','24000',audio],check=True,timeout=300)
                target = (doc.get('targets') or [{'code':'en','name':'English'}])[0]
                state['handoff'] = {'document':{k:v for k,v in doc.items() if k != 'desktop'},'assets':[{**asset,'source':str(files[asset['path']])} for asset in manifest['assets']]}
                if doc.get('native'):
                    # Foreign render caches remain portable assets; they are not current host output.
                    job['dubbed_tracks'] = {}; state['segHashesByLang'] = {}
                job['segments_i18n'] = {target['code']:{segment['id']:segment.get('translations',{}).get(target['code']) or segment['text_original'] for segment in segments} for target in doc.get('targets',[])}
                state.update(dubJobId=identifier,dubFilename=source_asset,dubSegments=segments,dubLang=target['name'],dubLangCode=target['code'],dubTracks=[t['code'] for t in doc.get('targets',[])],dubStep='editing',inputType='audio' if doc.get('audioOnly') else 'video')
                job.update(video_path=source_asset,audio_path=audio,vocals_path=audio,duration=doc['duration'],filename=manifest['title'],segments=segments,dubbed_tracks=job.get('dubbed_tracks') or {},input_type=state['inputType'])
                conn.execute('INSERT INTO studio_projects (id,name,video_path,audio_path,duration,state_json,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?)',(identifier,manifest['title'],source_asset,audio,doc['duration'],json.dumps(state),time.time(),time.time()))
                conn.execute('INSERT INTO dub_history (id,filename,duration,segments_count,language,language_code,tracks,job_data,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                    (identifier,manifest['title'],doc['duration'],len(segments),job.get('language',''),job.get('language_code',''),json.dumps(list(job['dubbed_tracks'])),json.dumps(job),time.time()))
                result = {'kind':'dub','title':manifest['title'],'projectId':identifier}
            # All failure-prone writes happen before committing either projects or voices.
            (directory/'result.json').write_text(json.dumps(result))
            index = {'id':directory.name,'title':manifest['title'],'kind':manifest['kind'],'createdAt':time.time(),'incoming':True}
            (directory/'index.json').write_text(json.dumps(index))
        return manifest,result
    except BaseException:
        for path in cleanup: path.unlink(missing_ok=True)
        for path in cleanup_directories: shutil.rmtree(path,ignore_errors=True)
        raise


@router.post('/import')
async def import_project_bundle(request: Request,allow_voices: bool = False):
    import asyncio
    identifier = str(uuid.uuid4()); directory = _directory(identifier); directory.mkdir(); source = directory/'project.vsproject'
    committed = False
    try:
        with source.open('xb') as output:
            count = 0
            async for block in request.stream():
                count += len(block)
                if count > bundles.MAX_TOTAL+8*1024*1024: raise ArchiveError(413,'Project archive is too large')
                output.write(block)
        work = asyncio.create_task(asyncio.to_thread(_import,directory,source,allow_voices))
        try:
            manifest,result = await asyncio.shield(work)
        except asyncio.CancelledError:
            # The file/DB transaction cannot be canceled midway through a thread.
            # Let it finish before cleanup; successful imports remain in the inbox.
            while not work.done():
                try: await asyncio.shield(work)
                except asyncio.CancelledError: continue
            work.result()
            committed = True
            raise
        committed = True
        index = json.loads((directory/'index.json').read_text())
        event_bus.emit('projects',{'action':'imported','id':result.get('projectId',identifier)})
        if manifest['voices']: event_bus.emit('profiles',{'action':'imported'})
        return {**index,**result}
    except ArchiveError as exc:
        if not committed: shutil.rmtree(directory,ignore_errors=True)
        raise HTTPException(exc.status,detail={'code':'project_archive_invalid','message':exc.detail}) from exc
    except BaseException:
        if not committed: shutil.rmtree(directory,ignore_errors=True)
        raise


@router.get('')
def list_project_bundles():
    _prune_exports()
    items = []
    for directory in _root().iterdir():
        try:
            path = _bundle_file(directory.name,'index.json')
            item = json.loads(path.read_text())
            if _valid_index(item): items.append(item)
        except (HTTPException,ValueError,OSError):
            logger.debug('Skipping unreadable project handoff index', exc_info=True)
    return sorted(items,key=lambda item:item.get('createdAt',0),reverse=True)[:200]


@router.get('/{identifier}/document')
def project_bundle_document(identifier: str):
    path = _bundle_file(identifier, 'result.json')
    if not path.is_file(): raise HTTPException(404,'Imported project not found')
    return json.loads(path.read_text())


@router.get('/{identifier}')
def download_project_bundle(identifier: str):
    with _transfer_lock:
        path = _bundle_file(identifier, 'project.vsproject')
        if not path.is_file(): raise HTTPException(404,'Project archive not found')
        return _BundleResponse(path,media_type='application/zip',filename=identifier+'.vsproject')
