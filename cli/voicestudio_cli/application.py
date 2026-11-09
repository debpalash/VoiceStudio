"""Read installed application metadata without starting Electron or the backend."""
from dataclasses import dataclass, asdict
import hashlib
import json
import os
from pathlib import Path
import platform
import plistlib
import re
import shlex
import shutil
import struct
import subprocess
import sys
import tempfile

from . import desktop
from .config import cache_dir
from .errors import CLIError

VERSION = re.compile(r'^\d+\.\d+\.\d+(?:-beta\.\d+)?$')


@dataclass(frozen=True)
class Application:
    version: str
    executable: Path
    method: str
    platform: str
    arch: str
    distribution: str = 'direct'

    def info(self):
        return asdict(self) | {'executable': str(self.executable)}


def native_arch(path=None):
    if path and path.is_file():
        with path.open('rb') as stream:
            data = stream.read(64)
            if data.startswith(b'\x7fELF') and len(data) >= 20:
                machine = int.from_bytes(data[18:20], 'little' if data[5] == 1 else 'big')
                return {62: 'x64', 183: 'arm64'}.get(machine, platform.machine())
            if data.startswith(b'MZ') and len(data) >= 64:
                stream.seek(int.from_bytes(data[60:64], 'little') + 4)
                return {0x8664: 'x64', 0xaa64: 'arm64'}.get(int.from_bytes(stream.read(2), 'little'), 'x64')
    return {'amd64': 'x64', 'x86_64': 'x64', 'aarch64': 'arm64', 'arm64': 'arm64'}.get(
        platform.machine().lower(), platform.machine().lower())


def asar_package(path):
    # ASAR stores a JSON directory in a Pickle header; entry offsets start after it.
    with path.open('rb') as stream:
        size_pickle = stream.read(8)
        if len(size_pickle) != 8 or struct.unpack('<I', size_pickle[:4])[0] != 4:
            raise ValueError('invalid ASAR header')
        size = struct.unpack('<I', size_pickle[4:])[0]
        if not 8 <= size <= 64 * 1024 * 1024:
            raise ValueError('invalid ASAR header size')
        header = stream.read(size)
        if len(header) != size:
            raise ValueError('truncated ASAR header')
        length = struct.unpack('<I', header[4:8])[0]
        if length > size - 8:
            raise ValueError('invalid ASAR JSON length')
        entry = json.loads(header[8:8 + length])['files']['package.json']
        offset, length = int(entry['offset']), int(entry['size'])
        if entry.get('unpacked') or offset < 0 or not 0 < length <= 2 * 1024 * 1024:
            raise ValueError('invalid package metadata entry')
        if 8 + size + offset + length > os.fstat(stream.fileno()).st_size:
            raise ValueError('package metadata exceeds archive')
        stream.seek(8 + size + offset)
        return json.loads(stream.read(length))


def package_metadata(directory):
    archive = directory / 'app.asar'
    if archive.is_file():
        return asar_package(archive)
    return json.loads((directory / 'app/package.json').read_text(encoding='utf-8'))


def image_fingerprint(path):
    stat = path.stat()
    return {'path': str(path), 'device': stat.st_dev, 'inode': stat.st_ino,
            'size': stat.st_size, 'mtime_ns': stat.st_mtime_ns, 'ctime_ns': stat.st_ctime_ns}


def appimage_metadata(path):
    fingerprint = image_fingerprint(path)
    key = hashlib.sha256(os.fsencode(str(path))).hexdigest()
    cached = cache_dir() / f'appimage-{key}.json'
    try:
        with cached.open('rb') as stream:
            record = json.loads(stream.read(64 * 1024))
        metadata = record['metadata']
        if (record.get('schema') == 1 and record.get('fingerprint') == fingerprint and
                isinstance(metadata, dict) and isinstance(metadata.get('version'), str) and
                VERSION.fullmatch(metadata['version'])):
            return metadata
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        pass  # unreadable metadata: no version from this source
    with tempfile.TemporaryDirectory(prefix='voicestudio-metadata-') as work:
        result = subprocess.run([str(path), '--appimage-extract', 'resources/app.asar'],
                                cwd=work, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=30)
        if result.returncode:
            raise ValueError('AppImage metadata extraction failed')
        metadata = package_metadata(Path(work) / 'squashfs-root/resources')
    if not isinstance(metadata, dict) or not isinstance(metadata.get('version'), str) or not VERSION.fullmatch(metadata['version']):
        raise ValueError('invalid application version')
    if fingerprint != image_fingerprint(path):
        raise ValueError('AppImage changed while reading its metadata; retry the command')
    temporary = None
    try:
        cached.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.appimage-', dir=cached.parent)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump({'schema': 1, 'fingerprint': fingerprint,
                       'metadata': {'version': metadata['version'],
                                    'voicestudioDistribution': metadata.get('voicestudioDistribution', 'direct')}}, stream)
        os.replace(temporary, cached)
    except OSError:
        pass  # Cache availability must not affect offline application inspection.
    finally:
        if temporary:
            try:
                Path(temporary).unlink(missing_ok=True)
            except OSError:
                pass  # best-effort cleanup of the extracted temporary file
    return metadata


def unwrap_launcher(path):
    """Inspect literal exec targets; never evaluate a shell launcher as code."""
    seen = set()
    for _ in range(8):
        path = path.resolve()
        if path in seen:
            raise ValueError('recursive desktop launcher')
        seen.add(path)
        if path.is_dir():
            return path
        with path.open('rb') as stream:
            header = stream.read(65536)
        if not header.startswith(b'#!'):
            return path
        targets = []
        for line in header.decode('utf-8').splitlines():
            if not line.strip().startswith('exec '):
                continue
            words = shlex.split(line.strip(), comments=True)[1:]
            if not words:
                continue
            if Path(words[0]).name == 'env':
                words = words[1:]
                while words and '=' in words[0] and not words[0].startswith('/'):
                    words.pop(0)
            if words and Path(words[0]).name == 'open' and '-a' in words:
                targets.append(words[words.index('-a') + 1])
            elif words:
                targets.append(words[0])
        if len(targets) != 1 or any(c in targets[0] for c in ['$(', '`', '\n']):
            raise ValueError('launcher does not have one literal desktop executable')
        target = os.path.expandvars(os.path.expanduser(targets[0]))
        if '$' in target:
            raise ValueError('unresolved launcher variable')
        if not Path(target).is_absolute():
            target = shutil.which(target) or ''
        if not target:
            raise ValueError('launcher executable was not found')
        path = Path(target)
    raise ValueError('too many launcher indirections')


def inspect_application(path):
    path = unwrap_launcher(path)
    arch = native_arch(path)
    if path.suffix.lower() == '.app':
        with (path / 'Contents/Info.plist').open('rb') as stream:
            info = plistlib.load(stream)
        metadata = package_metadata(path / 'Contents/Resources')
        metadata['version'] = info['CFBundleShortVersionString']
        method = 'dmg'
    elif path.suffix.lower() == '.appimage':
        with path.open('rb') as stream:
            header = stream.read(11)
        if not header.startswith(b'\x7fELF') or header[8:11] != b'AI\x02':
            raise ValueError('not a type-2 AppImage')
        metadata = appimage_metadata(path)
        method = 'appimage'
    else:
        # A macOS executable can be selected directly instead of its .app bundle.
        if path.parent.name == 'MacOS' and path.parent.parent.name == 'Contents':
            return inspect_application(path.parent.parent.parent)
        metadata = package_metadata(path.parent / 'resources')
        method = 'nsis' if sys.platform == 'win32' else 'directory'
        if sys.platform == 'linux' and (path.parent == Path('/opt/VoiceStudio') or
                                        path.parent == Path('/opt/voicestudio-electron')):
            method = 'deb'
    version = metadata['version']
    if not isinstance(version, str) or not VERSION.fullmatch(version):
        raise ValueError('invalid application version')
    distribution = metadata.get('voicestudioDistribution', 'direct')
    if distribution in {'snap-store', 'mac-app-store'}:
        method = 'store'
    return Application(version, path, method, sys.platform, arch, distribution)


def installed_application():
    try:
        manifest = desktop.bundled_launcher()
        if manifest:
            record = json.loads(manifest.read_text(encoding='utf-8'))
            version = record.get('version')
            if not isinstance(version, str) or not VERSION.fullmatch(version):
                raise ValueError('bundled application version is missing or invalid')
            if record.get('method') not in {'appimage', 'dmg', 'nsis', 'deb', 'directory', 'store'}:
                raise ValueError('invalid application installation format')
            if record.get('arch') not in {'x64', 'arm64'}:
                raise ValueError('invalid application architecture')
            executable = Path(record['application']).expanduser()
            if not executable.is_absolute():
                executable = manifest.parent / executable
            method = record['method']
            if sys.platform == 'linux' and os.environ.get('APPIMAGE'):
                executable, method = Path(os.environ['APPIMAGE']), 'appimage'
            elif sys.platform == 'linux' and str(executable.resolve()).startswith('/opt/'):
                method = 'deb'
            if record.get('distribution') in {'snap-store', 'mac-app-store'}:
                method = 'store'
            return Application(version, executable.resolve(), method, sys.platform,
                               record['arch'], record.get('distribution', 'direct'))
        command = desktop.resolve_desktop()
        target = command[2] if command[0] == '/usr/bin/open' else command[0]
        return inspect_application(Path(target))
    except CLIError as exc:
        # A checkout is itself an application installation, but never overrides an
        # explicitly selected or registered desktop installation.
        if exc.code == 'desktop_not_found' and not os.environ.get('VOICESTUDIO_DESKTOP_APP') and not desktop.launcher_config().exists():
            root = Path(__file__).resolve().parents[2]
            canonical = root / 'package.json'
            if canonical.is_file() and (root / 'electron').is_dir():
                version = json.loads(canonical.read_text(encoding='utf-8'))['version']
                if VERSION.fullmatch(version):
                    return Application(version, root, 'source', sys.platform, native_arch())
        raise
    except (OSError, ValueError, KeyError, TypeError, struct.error, subprocess.TimeoutExpired) as exc:
        # The technical reason stays available to scripts and --verbose, not the headline.
        raise CLIError('app_metadata_error', "Can't find the VoiceStudio app. Install VoiceStudio, or set "
                       'VOICESTUDIO_DESKTOP_APP to where the app is installed.', details=str(exc)) from None


def app_version():
    return installed_application().version
