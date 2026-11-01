"""Trust boundaries of the independent Android repository publisher."""
import copy
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tempfile
import types
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, unquote, urlparse
from xml.etree.ElementTree import Element, SubElement

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('android_repository', ROOT / 'scripts/prepare_android_repository.py')
publisher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(publisher)


class AndroidRepositoryTests(unittest.TestCase):
    def release(self):
        url = 'https://github.com/debpalash/VoiceStudio/releases'
        return {'draft': False, 'prerelease': False, 'tag_name': 'v0.5.7',
                'html_url': url + '/tag/v0.5.7', 'assets': [{
                    'name': 'VoiceStudio-0.5.7-android.apk', 'size': 3,
                    'browser_download_url': url + '/download/v0.5.7/VoiceStudio-0.5.7-android.apk',
                    'digest': 'sha256:' + hashlib.sha256(b'apk').hexdigest()}]}

    def test_release_download_is_bound_to_the_published_project_and_digest(self):
        release = self.release()
        asset = publisher.release_asset(release, 'debpalash/VoiceStudio')
        with tempfile.TemporaryDirectory() as temporary:
            apk = Path(temporary) / 'release.apk'
            apk.write_bytes(b'apk')
            publisher.verify_download(apk, asset)
            apk.write_bytes(b'bad')
            with self.assertRaisesRegex(ValueError, 'digest'):
                publisher.verify_download(apk, asset)

    def test_rejects_unpublished_prerelease_duplicate_and_redirected_assets(self):
        changes = [
            ('draft', True), ('prerelease', True), ('tag_name', '../unreviewed'),
            ('html_url', 'https://github.com/other/project/releases/tag/v0.5.7'),
        ]
        for key, value in changes:
            with self.subTest(key=key):
                release = self.release(); release[key] = value
                with self.assertRaises(ValueError):
                    publisher.release_asset(release, 'debpalash/VoiceStudio')
        for key, value in [('browser_download_url', 'https://untrusted.example/app.apk'),
                           ('size', 0), ('size', 513 * 1024**2), ('digest', None)]:
            with self.subTest(asset_field=key):
                release = self.release(); release['assets'][0][key] = value
                with self.assertRaises(ValueError):
                    publisher.release_asset(release, 'debpalash/VoiceStudio')
        release = self.release(); release['assets'].append(copy.deepcopy(release['assets'][0]))
        with self.assertRaisesRegex(ValueError, 'exactly one'):
            publisher.release_asset(release, 'debpalash/VoiceStudio')

    def test_interrupted_or_oversized_download_preserves_the_previous_file(self):
        asset = self.release()['assets'][0]
        with tempfile.TemporaryDirectory() as temporary:
            directory = Path(temporary)
            original = directory / asset['name']
            original.write_bytes(b'previous')
            with patch.object(publisher, 'urlopen', return_value=io.BytesIO(b'oversized')):
                with self.assertRaisesRegex(ValueError, 'exceeded'):
                    publisher.download_asset(asset, directory)
            self.assertEqual(original.read_bytes(), b'previous')
            self.assertEqual(list(directory.iterdir()), [original])
            with patch.object(publisher, 'urlopen', return_value=io.BytesIO(b'bad')):
                with self.assertRaisesRegex(ValueError, 'digest'):
                    publisher.download_asset(asset, directory)
            self.assertEqual(original.read_bytes(), b'previous')

    def manifest(self, **flags):
        manifest = Element('manifest')
        app = SubElement(manifest, 'application')
        app.attrib.update({publisher.ANDROID_NS + name: value for name, value in flags.items()})
        return types.SimpleNamespace(get_package=lambda: 'sh.voicestudio.mobile',
            get_androidversion_name=lambda: '0.5.7', get_androidversion_code=lambda: '3586502',
            get_android_manifest_xml=lambda: manifest)

    def test_checks_both_flags_even_when_debuggable_is_explicitly_false(self):
        publisher.validate_manifest(self.manifest(debuggable='false', testOnly='false'), 'sh.voicestudio.mobile', '0.5.7')
        for flags in ({'debuggable': 'true'}, {'debuggable': 'false', 'testOnly': 'true'}, {'testOnly': '@bool/reference'}):
            with self.subTest(flags=flags), self.assertRaisesRegex(ValueError, 'debuggable or testOnly'):
                publisher.validate_manifest(self.manifest(**flags), 'sh.voicestudio.mobile', '0.5.7')

    def test_rejects_wrong_application_version_and_nonpositive_version_code(self):
        for appid, version in [('other.app', '0.5.7'), ('sh.voicestudio.mobile', '0.5.8')]:
            with self.assertRaises(ValueError):
                publisher.validate_manifest(self.manifest(), appid, version)
        apk = self.manifest(); apk.get_androidversion_code = lambda: '0'
        with self.assertRaisesRegex(ValueError, 'versionCode'):
            publisher.validate_manifest(apk, 'sh.voicestudio.mobile', '0.5.7')

    def test_obtainium_link_round_trips_the_actual_import_configuration(self):
        config = json.loads((ROOT / 'store/obtainium.json').read_text())
        link = publisher.obtainium_link(config)
        deep = parse_qs(urlparse(link).query)['r'][0]
        self.assertEqual(json.loads(unquote(deep.removeprefix('obtainium://app/'))), config)
        self.assertEqual(config['url'], 'https://github.com/debpalash/VoiceStudio')

    def test_store_metadata_is_complete_and_reuses_the_current_brand_art(self):
        locale = ROOT / 'fastlane/metadata/android/en-US'
        self.assertLessEqual(len((locale / 'short_description.txt').read_text().strip()), 80)
        self.assertEqual((locale / 'images/icon.png').read_bytes(), (ROOT / 'store/google-play/icon-512.png').read_bytes())
        self.assertGreaterEqual(len(list((locale / 'images/phoneScreenshots').glob('*.png'))), 2)
        config = json.loads((ROOT / 'store/fdroid/repository.json').read_text())
        self.assertRegex(config['apkSignerSha256'], r'^[0-9a-f]{64}$')
        self.assertRegex(config['repositorySignerSha256'], r'^[0-9a-f]{64}$')
        self.assertNotEqual(config['apkSignerSha256'], config['repositorySignerSha256'])


if __name__ == '__main__':
    unittest.main()
