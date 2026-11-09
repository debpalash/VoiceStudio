#!/usr/bin/env python3
"""Run linked iOS engines sequentially on an isolated simulator with provisioned models."""
import argparse
import json
from pathlib import Path
import plistlib
import subprocess
import tempfile

IOS = Path(__file__).resolve().parents[1]
TESTS = [
    'realNativePinnedRuntimesAreLoadable',
    'phoneVideoMuxPreservesOrientationAndMarksPlayableAudio',
    'realSherpaSynthesizesAndTranscribesOffline',
    'realAuxiliaryCloneAndTranslation',
    'realAuxiliaryPhoneVoiceCreationAndSpeak',
    'realAuxiliaryPhoneDubOfflineAndResume',
]


def run(*args):
    subprocess.run([str(arg) for arg in args], check=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', required=True, help='Booted validation simulator UDID')
    parser.add_argument('--derived-data', type=Path, default=IOS / '.cache/simulator-inference')
    parser.add_argument('--test', choices=TESTS, action='append', help='Run only selected tests')
    args = parser.parse_args()
    if not (IOS / '.cache/inference-models/acceptance.json').is_file():
        parser.error('Provision test models and explicitly accept their licences first; see README.md')
    derived = args.derived_data.resolve()
    destination = 'platform=iOS Simulator,id=' + args.device
    run('xcodegen', 'generate', '--spec', IOS / 'VoiceStudio/project.yml')
    run('xcodebuild', '-project', IOS / 'VoiceStudio/VoiceStudio.xcodeproj',
        '-scheme', 'VoiceStudioNative', '-destination', destination,
        '-derivedDataPath', derived, 'CODE_SIGNING_ALLOWED=NO', 'build-for-testing')
    products = derived / 'Build/Products'
    candidates = list(products.glob('VoiceStudioNative_*.xctestrun'))
    if len(candidates) != 1:
        raise RuntimeError(f'Expected one native test run, found {len(candidates)}; use clean derived data')
    results = Path(tempfile.mkdtemp(prefix='native-results-', dir=derived))
    empty_hf_cache = results / 'hf-empty'
    empty_hf_cache.mkdir()
    config = plistlib.loads(candidates[0].read_bytes())
    config['VoiceStudioKitTests'].setdefault('EnvironmentVariables', {}).update({
        'VOICESTUDIO_REAL_INFERENCE': '1', 'VOICESTUDIO_REAL_AUX': '1',
        'VOICESTUDIO_REAL_NATIVE': '1', 'SHERPA_ONNX_LIBRARY': 'linked',
        'OMNIVOICE_LIBRARY': 'linked', 'LLAMA_LIBRARY': 'linked',
        'HF_HUB_OFFLINE': '1', 'HF_HUB_CACHE': str(empty_hf_cache),
    })
    # Keep beside the generated run: its __TESTROOT__ is relative to this file.
    configured = products / 'VoiceStudioNative-real.xctestrun'
    configured.write_bytes(plistlib.dumps(config))
    print(f'Results: {results}', flush=True)
    for test in args.test or TESTS:
        result = results / (test + '.xcresult')
        run('xcodebuild', 'test-without-building', '-xctestrun', configured,
            '-destination', destination, '-parallel-testing-enabled', 'NO',
            '-only-testing:VoiceStudioKitTests/' + test + '()', '-resultBundlePath', result)
        summary = json.loads(subprocess.check_output([
            'xcrun', 'xcresulttool', 'get', 'test-results', 'summary', '--path', str(result), '--format', 'json'
        ]))
        if (summary['totalTestCount'], summary['passedTests'], summary['failedTests'], summary['skippedTests']) != (1, 1, 0, 0):
            raise RuntimeError(f'{test} did not execute and pass exactly once: {summary}')
        print(f'PASS {test}', flush=True)


if __name__ == '__main__':
    main()
