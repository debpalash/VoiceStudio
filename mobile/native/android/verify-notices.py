#!/usr/bin/env python3
"""Fail closed if checked-in native notices drift from their texts or runtime pins."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ASSETS = ROOT.parents[1] / 'android/app/src/main/assets/licenses'
pins = json.loads((ROOT.parent / 'pins.json').read_text())
manifest = json.loads((ASSETS / 'native-sources.json').read_text())
entries = {entry['name']: entry for entry in manifest}
required = {'sherpa-onnx', 'onnxruntime', 'espeak-ng-for-piper', 'piper-phonemize',
            'kaldi-native-fbank', 'kissfft', 'kaldi-decoder', 'kaldifst', 'openfst',
            'simple-sentencepiece', 'json', 'hclust-cpp', 'omnivoice.cpp',
            'omnivoice-ggml', 'llama.cpp', 'llama-ggml', 'eigen'}
assert set(entries) == required, 'Incomplete native dependency notices'
for name, entry in entries.items():
    path = ASSETS / ('native-' + name + '.txt')
    assert hashlib.sha256(path.read_bytes()).hexdigest() == entry['notice_sha256'], name
for name in ('sherpa-onnx', 'omnivoice.cpp', 'llama.cpp'):
    pin = pins[name]['source']
    assert entries[name]['source'] == pin['repo'] + '/tree/' + pin['commit'], name
ggml = json.loads((ROOT / 'source-checksums.json').read_text())['ggml']
assert entries['omnivoice-ggml']['source'] == 'https://github.com/' + ggml['repo'] + '/tree/' + ggml['commit']
assert entries['llama-ggml']['source'] == entries['llama.cpp']['source'] + '/ggml'
corresponding = (ASSETS / 'native-Corresponding-source.txt').read_text()
assert 'GPL-3.0' in corresponding and entries['espeak-ng-for-piper']['source'] in corresponding
assert all(entry['source'] in corresponding for entry in entries.values())
print('Verified 17 native dependency notices and pinned corresponding-source references')
