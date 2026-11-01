"""Publish verified GitHub release APKs as a publisher-operated F-Droid repo.

Requires the isolated tools in store/fdroid/requirements.txt. The private
workspace and signing inputs are never part of the public Pages artifact.
"""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
from urllib.parse import quote, urlencode
from urllib.request import urlopen
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TAG = re.compile(r"v[0-9]+\.[0-9]+\.[0-9]+\Z")
ANDROID_NS = "{http://schemas.android.com/apk/res/android}"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as source:
        for block in iter(lambda: source.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def release_asset(release: dict, repository: str) -> dict:
    tag = release.get('tag_name', '')
    if release.get('draft') is not False or release.get('prerelease') is not False or not TAG.fullmatch(tag):
        raise ValueError('Only published stable version tags can enter this repository')
    origin = f'https://github.com/{repository}'
    if release.get('html_url') != f'{origin}/releases/tag/{tag}':
        raise ValueError('Unexpected release source')
    name = f'VoiceStudio-{tag[1:]}-android.apk'
    assets = [item for item in release.get('assets', []) if item.get('name') == name]
    if len(assets) != 1:
        raise ValueError('Release must contain exactly one Android APK')
    asset = assets[0]
    if asset.get('browser_download_url') != f'{origin}/releases/download/{tag}/{name}':
        raise ValueError('Unexpected APK source')
    if not isinstance(asset.get('size'), int) or not 0 < asset['size'] <= 512 * 1024**2:
        raise ValueError('Invalid APK size')
    if not re.fullmatch(r'sha256:[0-9a-f]{64}', asset.get('digest') or ''):
        raise ValueError('Release APK needs a GitHub SHA-256 digest')
    return asset


def verify_download(path: Path, asset: dict) -> None:
    if path.stat().st_size != asset['size'] or 'sha256:' + sha256(path) != asset['digest']:
        raise ValueError('APK does not match the published release digest')


def download_asset(asset: dict, directory: Path) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / asset['name']
    temporary = target.with_suffix('.partial')
    try:
        with urlopen(asset['browser_download_url'], timeout=120) as response, temporary.open('wb') as output:
            size = 0
            while block := response.read(1024 * 1024):
                size += len(block)
                if size > asset['size']:
                    raise ValueError('Download exceeded the declared APK size')
                output.write(block)
        verify_download(temporary, asset)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)
    return target


def validate_manifest(application, expected_id: str, version: str) -> dict:
    if application.get_package() != expected_id or application.get_androidversion_name() != version:
        raise ValueError('APK identity or version does not match the release')
    code = application.get_androidversion_code()
    if not code or not code.isdecimal() or int(code) <= 0:
        raise ValueError('APK needs a positive versionCode')
    manifest = application.get_android_manifest_xml()
    apps = list(manifest.iter('application')) if manifest is not None else []
    if len(apps) != 1:
        raise ValueError('APK needs exactly one application manifest entry')
    for name in ('debuggable', 'testOnly'):
        if apps[0].get(ANDROID_NS + name) not in (None, 'false', '0', '0x0', '0x00000000'):
            raise ValueError('Release APK must not be debuggable or testOnly')
    return {'applicationId': expected_id, 'versionName': version, 'versionCode': int(code)}


def inspect_apk(path: Path, settings: dict, version: str, apksigner: str) -> dict:
    # APK certificates are inspected only after cryptographic verification.
    result = subprocess.run([apksigner, 'verify', '--min-sdk-version', '29', '--print-certs', str(path)],
                            check=True, capture_output=True, text=True)
    certificates = re.findall(r'^Signer #[0-9]+ certificate SHA-256 digest: ([0-9a-fA-F]{64})$', result.stdout, re.MULTILINE)
    if [value.lower() for value in certificates] != [settings['apkSignerSha256']]:
        raise ValueError('APK signing identity changed')
    from loguru import logger
    logger.disable('androguard')
    from androguard.core.apk import APK
    metadata = validate_manifest(APK(str(path)), settings['applicationId'], version)
    return {**metadata, 'apkSignerSha256': certificates[0].lower(), 'sha256': sha256(path), 'bytes': path.stat().st_size}


def obtainium_link(config: dict) -> str:
    deep_link = 'obtainium://app/' + quote(json.dumps(config, separators=(',', ':')), safe='')
    return 'https://apps.obtainium.imranr.dev/redirect?' + urlencode({'r': deep_link})


def validate_index(directory: Path, metadata: dict, fingerprint: str) -> None:
    index = json.loads((directory / 'index-v1.json').read_text())
    packages = index['packages']
    versions = packages.get(metadata['applicationId'], [])
    if set(packages) != {metadata['applicationId']} or len(versions) != 1:
        raise ValueError('Repository did not index exactly the verified APK')
    record = versions[0]
    if (record['versionCode'] != metadata['versionCode'] or record['versionName'] != metadata['versionName']
            or record['signer'].lower() != metadata['apkSignerSha256'] or record['hash'] != metadata['sha256']
            or sha256(directory / record['apkName']) != metadata['sha256']):
        raise ValueError('Indexed APK changed')
    from cryptography.hazmat.primitives.serialization.pkcs7 import load_der_pkcs7_certificates
    from cryptography.hazmat.primitives import hashes
    for name in ('entry.jar', 'index-v1.jar'):
        with zipfile.ZipFile(directory / name) as jar:
            blocks = [item for item in jar.namelist() if item.upper().endswith('.RSA')]
            if len(blocks) != 1:
                raise ValueError('Repository index needs exactly one signer')
            certificates = load_der_pkcs7_certificates(jar.read(blocks[0]))
            if [cert.fingerprint(hashes.SHA256()).hex() for cert in certificates] != [fingerprint]:
                raise ValueError('Index signing identity changed')


def build(apk: Path, release: dict, output: Path, keystore: Path, alias: str,
          apksigner: str, preview: bool = False) -> dict:
    settings = json.loads((ROOT / 'store/fdroid/repository.json').read_text())
    asset = release_asset(release, settings['sourceRepository'])
    verify_download(apk, asset)
    metadata = inspect_apk(apk, settings, release['tag_name'][1:], apksigner)
    certificate = subprocess.check_output(['keytool', '-exportcert', '-keystore', str(keystore),
        '-alias', alias, '-storepass:env', 'FDROID_KEYSTORE_PASSWORD'], stderr=subprocess.DEVNULL)
    fingerprint = hashlib.sha256(certificate).hexdigest()
    if not preview and fingerprint != settings['repositorySignerSha256']:
        raise ValueError('Repository signing identity changed')
    if output.exists():
        raise ValueError('Output already exists; use a fresh staging directory')
    # Private files live outside the Pages artifact, even if fdroid update fails.
    with tempfile.TemporaryDirectory(prefix='voicestudio-fdroid-') as temporary:
        work = Path(temporary)
        repo = work / 'repo'
        repo.mkdir()
        destination = repo / f'{metadata["applicationId"]}_{metadata["versionCode"]}.apk'
        shutil.copyfile(apk, destination)
        config = {
            'repo_url': settings['repositoryUrl'],
            'repo_name': 'VoiceStudio' + (' preview (not published)' if preview else ''),
            'repo_description': 'Android releases published by VoiceStudio. Models download only when requested.',
            'repo_icon': 'icon.png', 'archive_older': 0,
            'keystore': str(keystore), 'repo_keyalias': alias,
            'keystorepass': {'env': 'FDROID_KEYSTORE_PASSWORD'},
            'keypass': {'env': 'FDROID_KEYSTORE_PASSWORD'}, 'apksigner': apksigner,
        }
        config_file = work / 'config.yml'
        config_file.write_text(json.dumps(config, indent=2))
        config_file.chmod(0o600)
        (repo / 'icons').mkdir()
        shutil.copyfile(ROOT / 'store/google-play/icon-512.png', repo / 'icons/icon.png')
        app_metadata = {
            'Name': 'VoiceStudio', 'Categories': ['Multimedia'], 'License': 'AGPL-3.0-only',
            'WebSite': 'https://voicestudio.sh', 'SourceCode': f'https://github.com/{settings["sourceRepository"]}',
            'IssueTracker': f'https://github.com/{settings["sourceRepository"]}/issues',
            'Summary': 'Offline speech, voice cloning, dubbing and transcription',
            'Description': ('Run voice models on your phone after downloading their weights, or pair with your own computer. '
                            'No account is required. Optional models have separate licences, including restrictive and unassessed terms; review them before downloading.'),
            'AntiFeatures': ['NonFreeAssets'],
            'CurrentVersion': metadata['versionName'], 'CurrentVersionCode': metadata['versionCode'],
            'AutoUpdateMode': 'None', 'UpdateCheckMode': 'None',
        }
        (work / 'metadata').mkdir()
        (work / 'metadata' / f'{metadata["applicationId"]}.yml').write_text(json.dumps(app_metadata, indent=2))
        locale = work / 'metadata' / metadata['applicationId'] / 'en-US'
        shutil.copytree(ROOT / 'fastlane/metadata/android/en-US', locale)
        (locale / 'changelogs').mkdir(exist_ok=True)
        (locale / 'changelogs' / f'{metadata["versionCode"]}.txt').write_text(
            f'VoiceStudio {metadata["versionName"]}: signed Android release. Release notes: {release["html_url"]}\n')
        # Keep the tool log private: only repo/ is copied into the public tree.
        with (work / 'fdroid-update.log').open('wb') as log:
            completed = subprocess.run([str(Path(sys.executable).with_name('fdroid')), 'update', '--pretty'],
                                       cwd=work, stdout=log, stderr=subprocess.STDOUT)
        if completed.returncode:
            detail = (work / 'fdroid-update.log').read_text()[-3000:]
            raise RuntimeError('fdroid update failed: ' + detail.replace(os.environ['FDROID_KEYSTORE_PASSWORD'], '[redacted]'))
        validate_index(repo, metadata, fingerprint)
        # Verify both signed index containers, including the modern v2 entry.
        from fdroidserver import common
        common.config = {'apksigner': apksigner}
        common.fill_config_defaults(common.config)
        common.verify_deprecated_jar_signature(str(repo / 'index-v1.jar'))
        common.verify_jar_signature(str(repo / 'entry.jar'))
        output.mkdir(parents=True)
        shutil.copytree(repo, output / 'fdroid/repo')
    obtainium = json.loads((ROOT / 'store/obtainium.json').read_text())
    (output / 'obtainium.json').write_text(json.dumps([obtainium], indent=2) + '\n')
    proof = {**metadata, 'release': release['html_url'], 'preview': preview,
             'repositoryUrl': settings['repositoryUrl'], 'repositorySignerSha256': fingerprint}
    (output / 'distribution.json').write_text(json.dumps(proof, indent=2) + '\n')
    repo_href = 'fdroid/repo/?fingerprint=' + fingerprint.upper()
    (output / 'index.html').write_text(
        '<!doctype html><meta charset="utf-8"><title>VoiceStudio Android</title>'
        '<h1>VoiceStudio Android</h1><p>Publisher-operated Android release repository.</p>'
        f'<p><a href="{html.escape(repo_href)}">F-Droid repository</a> · '
        f'<a href="{html.escape(obtainium_link(obtainium))}">Add to Obtainium</a></p>'
        '<p>Use the repository URL and fingerprint to add it in an F-Droid client. '
        'Play-delivered builds may use a different signing certificate; preserve your projects before switching channels.</p>'
        f'<p>Repository certificate SHA-256: <code>{fingerprint.upper()}</code></p>')
    return proof


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--apk', type=Path)
    source.add_argument('--download-directory', type=Path)
    parser.add_argument('--release-json', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--keystore', type=Path, required=True)
    parser.add_argument('--alias', default='voicestudio')
    parser.add_argument('--apksigner', required=True)
    parser.add_argument('--preview', action='store_true', help='Use an ephemeral test key; never deploy this output')
    args = parser.parse_args()
    if not os.environ.get('FDROID_KEYSTORE_PASSWORD'):
        parser.error('FDROID_KEYSTORE_PASSWORD is required')
    release = json.loads(args.release_json.read_text())
    settings = json.loads((ROOT / 'store/fdroid/repository.json').read_text())
    asset = release_asset(release, settings['sourceRepository'])
    apk = args.apk or download_asset(asset, args.download_directory)
    proof = build(apk.resolve(), release, args.output.resolve(),
                  args.keystore.resolve(), args.alias, args.apksigner, args.preview)
    print(json.dumps(proof))


if __name__ == '__main__':
    main()
