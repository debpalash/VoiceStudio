#!/usr/bin/env python3
"""Inspect locked PyAV wheel libraries without executing foreign binaries."""

import argparse
import concurrent.futures
import hashlib
import json
import re
import tomllib
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path

FFMPEG_LIBRARY = re.compile(
    r'(lib)?(avcodec|avformat|avutil|avfilter|avdevice|swresample|swscale)[-.]',
    re.IGNORECASE,
)
LICENSE_STRING = re.compile(
    rb'(?:lib\w+ license: )?(L?GPL) version [0-9.]+ or later'
)


def inspect_wheel(wheel: dict, cache: Path) -> dict:
    """Download one hash-pinned wheel and inventory its embedded library strings."""
    source = urllib.parse.urlsplit(wheel['url'])
    if source.scheme != 'https' or source.hostname != 'files.pythonhosted.org':
        raise ValueError('This audit only downloads locked PyPI wheel artifacts')
    name = wheel['url'].rsplit('/', 1)[1]
    target = cache / name
    if not target.exists():
        with urllib.request.urlopen(wheel['url'], timeout=60) as response:
            target.write_bytes(response.read())
    digest = hashlib.sha256(target.read_bytes()).hexdigest()
    if 'sha256:' + digest != wheel['hash']:
        raise ValueError(f'Wheel hash mismatch: {name}')
    result = {
        'wheel': name,
        'source_url': wheel['url'],
        'sha256': digest,
        'sha256_matches_lock': True,
        'libraries': [],
        'codec_libraries': [],
        'notices': [],
    }
    with zipfile.ZipFile(target) as archive:
        for member in archive.namelist():
            if member.endswith('/'):
                continue
            base = member.rsplit('/', 1)[-1]
            if 'x264' in base.lower() or 'x265' in base.lower():
                result['codec_libraries'].append({
                    'member': member,
                    'sha256': hashlib.sha256(archive.read(member)).hexdigest(),
                })
            is_library = '.so' in base or base.endswith(('.dll', '.dylib'))
            if FFMPEG_LIBRARY.search(base) and is_library:
                data = archive.read(member)
                strings = re.findall(rb'[\x20-\x7e]{12,}', data)
                configurations = sorted({
                    value.decode('ascii') for value in strings
                    if b'--enable-' in value and b'--disable-' in value
                })
                licences = sorted({
                    value.decode('ascii') for value in strings
                    if LICENSE_STRING.fullmatch(value)
                })
                result['libraries'].append({
                    'member': member,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'configuration_strings': configurations,
                    'license_strings': licences,
                })
            if any(word in base.lower() for word in ('license', 'licence', 'notice', 'copying', 'copyright')):
                data = archive.read(member)
                result['notices'].append({
                    'member': member,
                    'sha256': hashlib.sha256(data).hexdigest(),
                    'size': len(data),
                })
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        '--lockfile', type=Path,
        default=Path(__file__).resolve().parents[1] / 'uv.lock',
    )
    parser.add_argument('--cache-dir', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--python-tag', default='cp311')
    args = parser.parse_args()
    args.cache_dir.mkdir(parents=True, exist_ok=True)
    packages = tomllib.loads(args.lockfile.read_text(encoding='utf-8'))['package']
    package = next(item for item in packages if item['name'] == 'av')
    tag = f'-{args.python_tag}-{args.python_tag}-'
    wheels = [wheel for wheel in package['wheels'] if tag in wheel['url']]
    if not wheels:
        parser.error('No locked wheels match the requested Python tag')
    with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:
        results = list(pool.map(lambda wheel: inspect_wheel(wheel, args.cache_dir), wheels))
    report = {
        'pyav_version': package['version'],
        'scope': (
            f'Exact locked {args.python_tag} wheels; static inspection of embedded '
            'FFmpeg shared-library strings, not platform execution or full '
            'transitive/source-offer compliance.'
        ),
        'wheels': results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + '\n', encoding='utf-8')
    for wheel in results:
        print(f"{wheel['wheel']}: {len(wheel['libraries'])} FFmpeg libraries, "
              f"{len(wheel['codec_libraries'])} x264/x265 libraries, "
              f"{len(wheel['notices'])} notice files")


if __name__ == '__main__':
    main()
