#!/usr/bin/env python3
"""Materialize upstream legal texts from the locked runtime/dependency sources."""
import json
from pathlib import Path
import re
import tarfile
import zipfile

import build

OUTPUT = build.ROOT / 'mobile/ios/VoiceStudio/Resources/NativeLicences'
# These libraries are merged into sherpa's default iOS archive; header-only
# dependencies and nested dependencies retain their notices too.
DEPENDENCIES = ['espeak-ng-for-piper', 'piper-phonemize', 'kaldi-native-fbank',
                'kaldi-decoder', 'simple-sentencepiece', 'json', 'hclust-cpp', 'eigen']


def license_name(name):
    name = name.upper()
    return name in ('LICENSE', 'LICENCE', 'COPYING', 'NOTICE', 'COPYRIGHT') or name.startswith(('LICENSE.', 'COPYING.', 'LICENCE.'))


def legal_file(path):
    return license_name(Path(path).name) or path.endswith("/LICENSES/BSD-3-Clause")


def archive_files(archive):
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as source:
            return {n: source.read(n) for n in source.namelist() if not n.endswith('/') and (legal_file(n) or '/cmake/' in n)}
    with tarfile.open(archive) as source:
        return {m.name: source.extractfile(m).read() for m in source.getmembers() if m.isfile() and (legal_file(m.name) or '/cmake/' in m.name)}


def prepare():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    manifest = []
    def record(name, url, files, checksum):
        text = []
        for path, data in sorted(files.items()):
            if legal_file(path):
                text.append(path + '\n\n' + data.decode('utf-8', errors='replace'))
        if not text:
            raise RuntimeError('Missing upstream licence: ' + name)
        content = name + '\nSource: ' + url + '\n\n' + '\n\n'.join(text)
        target = OUTPUT / (name + '.txt')
        target.write_text(content)
        manifest.append({'name': name, 'source': url, 'source_sha256': checksum,
                         'notice_sha256': build.digest(target)})

    roots = {}
    for name in ['sherpa-onnx', 'omnivoice.cpp', 'llama.cpp']:
        pin = build.PINS[name]['source']
        root, modules = build.source(pin['repo'], pin['commit'])
        roots[name] = root
        files = {str(p.relative_to(root)): p.read_bytes() for p in root.iterdir() if p.is_file() and license_name(p.name)}
        record(name, pin['repo'] + '/tree/' + pin['commit'], files, json.loads((root / '.voicestudio-source.json').read_text()).get('archive_sha256', 'git-tree-verified'))
        if name == 'omnivoice.cpp':
            commit = next(m['sha'] for m in modules if m['path'] == 'ggml')
            ggml, _ = build.source('https://github.com/ServeurpersoCom/ggml', commit)
            record('omnivoice-ggml', 'https://github.com/ServeurpersoCom/ggml/tree/' + commit, {'LICENSE': (ggml / 'LICENSE').read_bytes()}, 'git-tree-verified')
        elif name == 'llama.cpp':
            # llama's ggml shares its MIT notice; retain a separately named entry.
            record('llama-ggml', pin['repo'] + '/tree/' + pin['commit'] + '/ggml', {'LICENSE': (root / 'LICENSE').read_bytes()}, 'git-tree-verified')

    seen = set()
    def dependency(name, cmake):
        if name in seen:
            return
        seen.add(name)
        # Upstream places sherpa's cmake directory first, overriding nested pins.
        override = roots['sherpa-onnx'] / 'cmake' / (name + '.cmake')
        if override.exists():
            cmake = override.read_text()
        url = re.search(r'set\([^\s]+_URL\s+"(https://[^"\n]+)"', cmake)
        checksum = re.search(r'_HASH\s+"SHA256=([0-9a-f]{64})"', cmake)
        if not url or not checksum:
            raise RuntimeError('Missing dependency pin: ' + name)
        url, checksum = url.group(1), checksum.group(1)
        suffix = '.zip' if url.endswith('.zip') else '.tar.gz'
        archive = build.download(url, build.BUILD / 'downloads' / ('notice-' + checksum + suffix), checksum)
        files = archive_files(archive)
        record(name, url, files, checksum)
        for path, data in files.items():
            if Path(path).name in ('kissfft.cmake', 'kaldifst.cmake', 'openfst.cmake', 'ucd.cmake', 'eigen.cmake'):
                dependency(Path(path).stem, data.decode())
    for name in DEPENDENCIES:
        dependency(name, (roots['sherpa-onnx'] / 'cmake' / (name + '.cmake')).read_text())
    version = re.search(r'SHERPA_ONNX_ONNXRUNTIME_VERSION:-([^}]+)', (roots['sherpa-onnx'] / 'build-ios.sh').read_text()).group(1)
    cache = build.onnxruntime_notice(roots['sherpa-onnx'])
    record('onnxruntime', 'https://github.com/microsoft/onnxruntime/tree/v' + version, {'LICENSE': cache.read_bytes()}, build.digest(cache))
    (OUTPUT / 'sources.json').write_text(json.dumps(manifest, indent=2) + '\n')
    (OUTPUT / 'Corresponding-source.txt').write_text(
        'VoiceStudio native runtime corresponding sources\n\n'
        'eSpeak NG is GPL-3.0. Its complete pinned upstream source archive,\n'
        'including its build scripts, is listed below. The pinned sherpa-onnx\n'
        'source and VoiceStudio mobile/native/ios/build.py and build.sh contain\n'
        'the build configuration used to combine these dependencies.\n'
        'VoiceStudio source: https://github.com/debpalash/VoiceStudio\n'
        'Runtime pins: mobile/native/pins.json\n\n' +
        '\n'.join(m['name'] + ': ' + m['source'] for m in manifest) + '\n')
    print('Prepared', len(manifest), 'native dependency notices in', OUTPUT)


if __name__ == '__main__':
    prepare()
