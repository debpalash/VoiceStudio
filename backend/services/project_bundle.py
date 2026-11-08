"""Versioned editable project handoff. Reuses .omnivoice voice metadata and bounded ZIP IO.

The archive is a ZIP_STORED container with manifest.json plus SHA-256 listed
assets. Documents preserve Electron's existing project/draft/settings shapes;
canonical manuscript/timeline fields remain authoritative after phone edits.
No credentials, model weights, absolute filesystem paths or consent receipts
are installed from a bundle. Importers always allocate new object identities.
"""
from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re
import shutil
import math
import struct
import stat
import uuid
import zipfile

from core.safe_archive import ArchiveError
from core.path_security import safe_relative_path, safe_filename, UnsafePath

FORMAT = 'voicestudio.project'
VERSION = 1
MAX_TOTAL = 1024 * 1024 * 1024
MAX_MEMBER = 512 * 1024 * 1024
MAX_MANIFEST = 4 * 1024 * 1024
MAX_FILES = 20000
_ALLOWED_EXT = {'.json', '.pcm', '.wav', '.mp3', '.m4a', '.m4b', '.ogg', '.flac', '.aac', '.aiff', '.caf', '.mp4', '.mov', '.mkv', '.webm', '.media', '.jpg', '.jpeg', '.png', '.webp', '.srt', '.vtt', '.txt'}
_SECRET_KEYS = {'credentials', 'authorization', 'cookie', 'cookies', 'cookie_file', 'api_key', 'apikey', 'access_token', 'refresh_token', 'sessiontoken', 'verified', 'verified_owner', 'ownershipverified', 'consentat', 'consent_at', 'licencereceipts', 'licensereceipts', 'verified_own_voice', 'verifiedownvoice', 'consent_text', 'consent_audio_path', 'consent_recorded_at', 'consentrecordedat', 'consenttext', 'consentaudiopath'}


def archive_path(value: str) -> str:
    try:
        safe_relative_path(value)
        if len(value.encode()) > 240 or not re.fullmatch(r'[A-Za-z0-9_./-]+', value):
            raise UnsafePath('nonportable archive path')
        for part in value.split('/'):
            safe_filename(part)
    except (UnsafePath, TypeError) as exc:
        raise ArchiveError(400, 'unsafe project archive path') from exc
    return value


def digest(path: Path) -> str:
    result = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(262144), b''):
            result.update(block)
    return result.hexdigest()


def clean_document(value, depth=0):
    if depth > 64:
        raise ArchiveError(400, 'project document is nested too deeply')
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            if not isinstance(key, str) or len(key) > 250:
                raise ArchiveError(400, 'invalid project document key')
            if key.lower().replace('-', '_') in _SECRET_KEYS:
                continue
            # Paths inside packages are asset: references, never workstation paths.
            if isinstance(child, str) and ('path' in key.lower() or key.lower().endswith('file')) and (child.startswith(('/', '\\')) or re.match(r'^[A-Za-z]:', child)):
                child = None
            result[key] = clean_document(child, depth+1)
        return result
    if isinstance(value, list):
        if len(value) > 20000:
            raise ArchiveError(400, 'project array is too large')
        return [clean_document(child, depth+1) for child in value]
    if isinstance(value, str) and len(value) > 1_000_000:
        raise ArchiveError(413, 'project text is too large')
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ArchiveError(400, 'invalid project document value')



def _validate_desktop_draft(draft):
    if not isinstance(draft,dict) or any(key in draft and not isinstance(draft[key],list) for key in ('cast','lines','lexicon')) or any(key in draft and not isinstance(draft[key],dict) for key in ('metadata','overrides','voiceCast','handoff')):
        raise ArchiveError(400,'invalid desktop draft collections')
    for key,fields in (('cast',('id','name','profileId')),('lines',('id','character','profileId','text')),('lexicon',('word','pronunciation'))):
        for row in draft.get(key,[]):
            if not isinstance(row,dict) or any(row.get(field) is not None and not isinstance(row[field],str) for field in fields):
                raise ArchiveError(400,'invalid desktop draft row')
    for key in ('cast','lines'):
        identities = [row['id'] for row in draft.get(key,[]) if row.get('id') is not None]
        if any(not identity for identity in identities) or len(set(identities)) != len(identities):
            raise ArchiveError(400,'duplicate or empty desktop identity')
    if any(not isinstance(voice,str) for voice in draft.get('voiceCast',{}).values()) or draft.get('voice') is not None and not isinstance(draft['voice'],str):
        raise ArchiveError(400,'invalid desktop voice binding')
    memory = draft.get('handoff',{})
    if 'document' in memory and not isinstance(memory['document'],dict):
        raise ArchiveError(400,'invalid retained project document')


def validate_manifest(value: dict) -> dict:
    if not isinstance(value, dict) or value.get('format') != FORMAT or value.get('version') != VERSION:
        raise ArchiveError(400, 'unsupported project archive version')
    if value.get('kind') not in {'stories', 'audiobook', 'dub'} or not isinstance(value.get('title'), str) or not value['title'].strip() or len(value['title']) > 250:
        raise ArchiveError(400, 'invalid project identity')
    if not isinstance(value.get('document'), dict) or not isinstance(value.get('assets'), list) or not isinstance(value.get('voices'), list) or len(value['voices']) > 100:
        raise ArchiveError(400, 'invalid project manifest')
    paths = set(); total = 0
    for asset in value['assets']:
        if not isinstance(asset, dict) or not isinstance(asset.get('path'), str):
            raise ArchiveError(400, 'invalid project asset')
        path = archive_path(asset['path'])
        if not path.startswith(('assets/', 'voices/', 'native/')) or Path(path).suffix.lower() not in _ALLOWED_EXT or path.lower() in paths:
            raise ArchiveError(400, 'unsupported or duplicate project asset')
        size = asset.get('size')
        if not isinstance(size, int) or isinstance(size, bool) or not 0 <= size <= MAX_MEMBER or not re.fullmatch(r'[0-9a-f]{64}', str(asset.get('sha256', ''))):
            raise ArchiveError(400, 'invalid project asset fingerprint')
        total += size; paths.add(path.lower())
    if total > MAX_TOTAL or len(paths)+1 > MAX_FILES:
        raise ArchiveError(413, 'project archive is too large')
    voice_ids = set()
    for voice in value['voices']:
        if not isinstance(voice, dict) or not isinstance(voice.get('id'), str) or not 1 <= len(voice['id']) <= 200 or voice['id'] in voice_ids or not isinstance(voice.get('metadata'), dict) or voice.get('reference') not in {a['path'] for a in value['assets']}:
            raise ArchiveError(400, 'invalid project voice reference')
        voice_ids.add(voice['id'])
        for field in ('pcm','preview','locked'):
            if voice.get(field) is not None and voice[field] not in {a['path'] for a in value['assets']}: raise ArchiveError(400,'missing voice asset')
    # Strip capabilities/consent even from hand-edited archives, not just exporters.
    value = clean_document(value)
    document = value['document']
    def number(raw):
        return isinstance(raw,(int,float)) and not isinstance(raw,bool) and math.isfinite(raw)
    desktop = document.get('desktop')
    if desktop is not None and not isinstance(desktop,dict): raise ArchiveError(400,'invalid desktop project')
    if isinstance(desktop,dict) and 'draft' in desktop:
        _validate_desktop_draft(desktop['draft'])
    if value['kind'] != 'dub':
        if not isinstance(document.get('script'), str) or len(document['script']) > 1_000_000 or not isinstance(document.get('cast', {}), dict) or any(not isinstance(v,str) for v in document.get('cast',{}).values()) or not isinstance(document.get('defaultVoice','preset:0'),str):
            raise ArchiveError(400, 'invalid manuscript')
        pronunciations = document.get('pronunciations',{})
        roles = document.get('roles',{})
        if not isinstance(pronunciations,dict) or any(not isinstance(v,str) for v in pronunciations.values()):
            raise ArchiveError(400,'invalid pronunciation dictionary')
        if not isinstance(roles,dict) or any(not isinstance(origin,dict) or any(origin.get(field) is not None and not isinstance(origin[field],str) for field in ('characterId','name','lineId')) for origin in roles.values()):
            raise ArchiveError(400,'invalid manuscript role mapping')
        speed = document.get('speed', 1)
        if not number(speed) or not 0.5 <= speed <= 2:
            raise ArchiveError(400, 'invalid reading speed')
    else:
        segments = document.get('segments', [])
        duration = document.get('duration', 0)
        if not isinstance(segments, list) or len(segments) > 10000 or not number(duration) or not 0 <= duration <= 14400:
            raise ArchiveError(400, 'invalid timeline')
        targets = document.get('targets')
        if not isinstance(targets,list) or not 1 <= len(targets) <= 20: raise ArchiveError(400,'invalid dub targets')
        codes = set()
        for target in targets:
            if not isinstance(target,dict) or not isinstance(target.get('code'),str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',target['code']) or not isinstance(target.get('name'),str) or not 2 <= len(target['name']) <= 120 or target['code'] in codes: raise ArchiveError(400,'invalid dub target')
            codes.add(target['code'])
        identities = set()
        for segment in segments:
            if not isinstance(segment, dict) or not isinstance(segment.get('text'), str) or not segment['text'].strip() or not number(segment.get('start')) or not number(segment.get('end')) or not 0 <= segment['start'] < segment['end'] <= duration + .01:
                raise ArchiveError(400, 'invalid timeline segment')
            translations = segment.get('translations',{})
            if not isinstance(translations,dict) or any(not isinstance(k,str) or k not in codes or not isinstance(v,str) or len(v)>4000 for k,v in translations.items()): raise ArchiveError(400,'invalid translated text')
            if segment.get('reference') is not None and segment['reference'] not in {a['path'] for a in value['assets']}: raise ArchiveError(400,'missing segment reference')
            if segment.get('referenceText') is not None and (not isinstance(segment['referenceText'],str) or len(segment['referenceText'])>4000): raise ArchiveError(400,'invalid reference transcript')
            if segment.get('muted') is not None and not isinstance(segment['muted'],bool): raise ArchiveError(400,'invalid segment mute')
            if not number(segment.get('gain',1)) or not 0 <= segment.get('gain',1) <= 2: raise ArchiveError(400,'invalid segment gain')
            identity = segment.get('id')
            if identity is not None and (not isinstance(identity,str) or not identity or identity in identities): raise ArchiveError(400,'invalid segment identity')
            identities.add(identity)
        if document.get('source') not in {a['path'] for a in value['assets']}:
            raise ArchiveError(400, 'project source asset is missing')
    return value


def write_bundle(manifest: dict, files: dict[str, Path], output: Path) -> dict:
    if output.exists():
        raise ArchiveError(409, 'project archive already exists')
    result = copy.deepcopy(manifest); result.update(format=FORMAT, version=VERSION)
    result['assets'] = []
    for name, path in files.items():
        archive_path(name)
        if path.is_symlink() or not path.is_file():
            raise ArchiveError(400, 'project assets must be regular local files')
        result['assets'].append({'path': name, 'size': path.stat().st_size, 'sha256': digest(path)})
    result = validate_manifest(result)
    encoded = json.dumps(result, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode()
    if len(encoded) > MAX_MANIFEST or len(encoded)+sum(a['size'] for a in result['assets']) > MAX_TOTAL:
        raise ArchiveError(413, 'project manifest is too large')
    pending = output.with_name('.bundle-'+uuid.uuid4().hex)
    try:
        with zipfile.ZipFile(pending, 'w', compression=zipfile.ZIP_STORED) as archive:
            archive.writestr('manifest.json', encoded)
            for asset in result['assets']:
                path = files[asset['path']]
                archive.write(path, asset['path'])
                if path.stat().st_size != asset['size'] or digest(path) != asset['sha256']:
                    raise ArchiveError(409, 'project changed during export')
        pending.replace(output)
    finally:
        pending.unlink(missing_ok=True)
    return result


def read_bundle(source: Path, destination: Path) -> tuple[dict, dict[str, Path]]:
    if destination.exists() or source.stat().st_size > MAX_TOTAL + 8*1024*1024:
        raise ArchiveError(413, 'project archive is too large')
    completed = False
    try:
        with zipfile.ZipFile(source) as archive:
            infos = archive.infolist(); names = set(); total = 0
            if len(infos) > MAX_FILES:
                raise ArchiveError(413, 'too many project archive entries')
            for info in infos:
                name = archive_path(info.filename); mode = info.external_attr >> 16
                if name.lower() in names or info.is_dir() or mode & 0o170000 not in (0, stat.S_IFREG) or info.compress_type != zipfile.ZIP_STORED or info.flag_bits & ~0x800 or info.file_size > MAX_MEMBER or info.compress_size != info.file_size:
                    raise ArchiveError(400, 'unsupported project archive entry')
                with source.open('rb') as raw:
                    raw.seek(info.header_offset); local = raw.read(30)
                    if len(local) != 30: raise ArchiveError(400,'truncated ZIP header')
                    signature,needed,flags,method,tm,dt,crc,compressed,length,name_size,extra_size = struct.unpack('<IHHHHHIIIHH',local)
                    if signature != 0x04034b50 or flags != info.flag_bits or method != 0 or crc != info.CRC or compressed != info.file_size or length != info.file_size or raw.read(name_size) != name.encode() or info.header_offset+30+name_size+extra_size+length > archive.start_dir: raise ArchiveError(400,'inconsistent ZIP member header')
                names.add(name.lower()); total += info.file_size
            if total > MAX_TOTAL or 'manifest.json' not in names:
                raise ArchiveError(413, 'project archive is too large or missing its manifest')
            info = archive.getinfo('manifest.json')
            if info.file_size > MAX_MANIFEST:
                raise ArchiveError(413, 'project manifest is too large')
            try:
                def bad_constant(value):
                    raise ValueError(value)
                manifest = validate_manifest(json.loads(archive.read(info), parse_constant=bad_constant))
            except (ValueError, RecursionError, UnicodeDecodeError) as exc:
                raise ArchiveError(400, 'invalid project manifest JSON') from exc
            expected = {'manifest.json'} | {a['path'] for a in manifest['assets']}
            if {i.filename for i in infos} != expected:
                raise ArchiveError(400, 'project archive contains unlisted or missing assets')
            destination.mkdir(parents=True)
            files = {}
            for asset in manifest['assets']:
                info = archive.getinfo(asset['path'])
                if info.file_size != asset['size']:
                    raise ArchiveError(400, 'project asset size mismatch')
                target = destination.joinpath(*asset['path'].split('/')); target.parent.mkdir(parents=True, exist_ok=True)
                checksum = hashlib.sha256(); count = 0
                with archive.open(info) as reader, target.open('xb') as writer:
                    while block := reader.read(262144):
                        count += len(block)
                        if count > asset['size']:
                            raise ArchiveError(413, 'project asset exceeded its declared limit')
                        checksum.update(block); writer.write(block)
                if count != asset['size'] or checksum.hexdigest() != asset['sha256']:
                    raise ArchiveError(400, 'project asset checksum mismatch')
                files[asset['path']] = target
            # JSON and the zip manifest above are both UTF-8 by definition. A
            # bare write_text() encodes in the locale code page, so importing a
            # bundle whose title the local ANSI page cannot represent raised
            # UnicodeEncodeError — a ValueError here — and surfaced as a bogus
            # "invalid project archive" for an archive that validated fine.
            (destination/'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, allow_nan=False), encoding='utf-8')
            completed = True; return manifest, files
    except (zipfile.BadZipFile, RuntimeError, EOFError, KeyError, ValueError) as exc:
        if isinstance(exc, ArchiveError): raise
        raise ArchiveError(400, 'invalid project archive') from exc
    finally:
        if not completed:
            shutil.rmtree(destination, ignore_errors=True)


def _chapter_heading(text: str) -> str | None:
    # Match the renderer's canonical H1 grammar; phone plans require "# ".
    from services.longform_parser import _HEADING_RE
    heading = _HEADING_RE.fullmatch(text.strip())
    return '# '+heading.group(1).strip() if heading else None


def manuscript_document(project: dict) -> dict:
    """Electron's current LongformProject.draft is retained, not a legacy store clone."""
    draft = project.get('draft', {})
    _validate_desktop_draft(draft)
    mode = project.get('mode', 'stories'); cast = {}; lines = []; roles = {}
    characters = {c['id']: c for c in draft.get('cast', []) if isinstance(c,dict) and isinstance(c.get('id'),str)}
    for index, line in enumerate(draft.get('lines', [])):
        if not isinstance(line,dict) or not isinstance(line.get('text'),str): continue
        heading = _chapter_heading(line['text'])
        if heading is not None:
            lines.append(heading)
            continue
        role = 'line-'+str(index) if line.get('profileId') else str(line.get('character') or 'narrator')
        role = re.sub(r'[^A-Za-z0-9_-]', '_', role)[:100] or 'narrator'
        voice = line.get('profileId') or characters.get(line.get('character'), {}).get('profileId') or draft.get('voice')
        if voice: cast[role] = voice
        roles[role] = {'characterId':line.get('character') or 'narrator','name':characters.get(line.get('character'),{}).get('name') or role,**({'lineId':line.get('id'),'lineIndex':index} if line.get('profileId') else {})}
        speed = f"[speed:{line['speed']}] " if isinstance(line.get('speed'),(int,float)) else ''
        lines.append(f'[voice:{role}] {speed}{line["text"]}')
    lexicon = {r['word']: r['pronunciation'] for r in draft.get('lexicon', []) if isinstance(r,dict) and isinstance(r.get('word'),str) and isinstance(r.get('pronunciation'),str)}
    memory = draft.get('handoff') if isinstance(draft.get('handoff'),dict) else {}
    document = copy.deepcopy(memory.get('document') or {})
    snapshot = copy.deepcopy(project); snapshot.get('draft',{}).pop('handoff',None)
    document.update({'script': draft.get('script','') if mode == 'audiobook' else '\n'.join(lines), 'defaultVoice': draft.get('voice') or 'preset:0', 'cast': {k:v for k,v in draft.get('voiceCast',{}).items() if isinstance(v,str) and v} if mode == 'audiobook' else cast, 'speed': draft.get('globalSpeed',1), 'pronunciations': lexicon, 'desktop': snapshot, 'roles': roles})
    return clean_document(document)


def desktop_manuscript(manifest: dict) -> dict:
    """Rebuild editable fields from the current phone document; stale desktop text never wins."""
    doc = manifest['document']; project = copy.deepcopy(doc.get('desktop') or {})
    draft = project.setdefault('draft', {}); original_script = manuscript_document(project).get('script','') if project.get('draft') else ''
    draft.update(script=doc['script'], title=manifest['title'], globalSpeed=doc.get('speed',1), voice=doc.get('defaultVoice'), voiceCast=doc.get('cast',{}), lexicon=[{'word':k,'pronunciation':v} for k,v in doc.get('pronunciations',{}).items()])
    roles = doc.get('roles',{}) if isinstance(doc.get('roles'),dict) else {}
    if manifest['kind'] == 'stories':
        for role,voice in doc.get('cast',{}).items():
            origin = roles.get(role) or {}; line_id = origin.get('lineId')
            if line_id is not None:
                for line in draft.get('lines',[]):
                    if line.get('id') == line_id: line['profileId'] = voice
            else:
                for character in draft.get('cast',[]):
                    if character.get('id') == origin.get('characterId',role): character['profileId'] = voice
    if manifest['kind'] == 'stories' and doc['script'] != original_script:
        original = draft.get('lines', []); cast = list(draft.get('cast', [])); lines = []; role = 'narrator'; used_ids = set()
        known = {c.get('id') for c in cast if isinstance(c,dict)}
        for raw in doc['script'].splitlines():
            text = raw.strip()
            if not text: continue
            heading = _chapter_heading(text)
            if heading is not None:
                old = next((line for line in original
                            if line.get('id') not in used_ids
                            and _chapter_heading(line.get('text') or '') == heading), {})
                line_id = old.get('id') or str(uuid.uuid4())
                used_ids.add(line_id)
                lines.append({**old,'id':line_id,'text':heading,'character':'narrator',
                              'profileId':None,'speed':None})
                continue
            match = re.match(r'^\[voice:([^\]\r\n]+)\]\s*', text)
            if match: role = match[1]; text = text[match.end():]
            speed = None; match = re.match(r'^\[speed:([0-9.]+)\]\s*', text)
            if match: speed = float(match[1]); text = text[match.end():]
            if not text: continue
            origin = roles.get(role) or {}; character = origin.get('characterId') or role
            old = next((line for line in original if origin.get('lineId') and line.get('id') == origin['lineId']), None)
            if old is None: old = next((line for line in original if line.get('text') == text and line.get('character') == character), {})
            line_id = old.get('id')
            if not line_id or line_id in used_ids: line_id = str(uuid.uuid4())
            used_ids.add(line_id)
            line = dict(old); line.update(id=line_id,text=text,character=character,profileId=doc.get('cast',{}).get(role) if origin.get('lineId') else None,speed=speed)
            lines.append(line)
            if character not in known: cast.append({'id':character,'name':origin.get('name') or character,'profileId':doc.get('cast',{}).get(role)}); known.add(character)
        draft['lines'] = lines; draft['cast'] = cast
    draft.setdefault('cast',[]); draft.setdefault('lines',[])
    draft['handoff'] = {'document':{k:v for k,v in doc.items() if k != 'desktop'},'assets':[{**asset,'source':'asset:'+asset['path']} for asset in manifest.get('assets',[])]}
    project.update(name=manifest['title'], mode=manifest['kind'], draft=draft)
    return project
