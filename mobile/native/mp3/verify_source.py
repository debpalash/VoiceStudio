#!/usr/bin/env python3
"""Offline verification of every upstream encoder input against its pinned release."""
import argparse
import hashlib
import json
from pathlib import Path
import tarfile

ROOT = Path(__file__).resolve().parents[3]
SOURCE = ROOT / 'mobile/ios/VoiceStudioKit/Sources/VSNative/Lame'
ARCHIVE_SHA256 = 'ddfe36cab873794038ae2c1210557ad34857a4b6bdc515785d1da9e175b1da1e'


def verify(archive=None):
    pin = json.loads((SOURCE / 'source.json').read_text())
    assert pin['version'] == '3.100' and pin['archive_sha256'] == ARCHIVE_SHA256
    for name, digest in pin['files'].items():
        path = Path(name)
        assert not path.is_absolute() and '..' not in path.parts
        assert hashlib.sha256((SOURCE / path).read_bytes()).hexdigest() == digest, name
    actual = {str(p.relative_to(SOURCE)) for p in SOURCE.rglob('*') if p.suffix in ('.c', '.h')}
    expected = {p for p in pin['files'] if Path(p).suffix in ('.c', '.h')} | {'config.h'}
    assert actual == expected, 'Unpinned encoder input'
    if archive:
        assert hashlib.sha256(Path(archive).read_bytes()).hexdigest() == ARCHIVE_SHA256
        with tarfile.open(archive) as source:
            for name, digest in pin['files'].items():
                assert hashlib.sha256(source.extractfile('lame-3.100/' + name).read()).hexdigest() == digest, name
    return pin


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--archive')
    args = parser.parse_args(); pin = verify(args.archive)
    print('Verified LAME ' + pin['version'] + ' encoder sources and original notices offline')
