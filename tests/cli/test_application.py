"""Installed app metadata is independent of the Python distribution version."""
import io
import json
import os
from pathlib import Path
import plistlib
import struct
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'cli'))
from voicestudio_cli import application
from voicestudio_cli.errors import CLIError


def asar(path, metadata):
    payload = json.dumps(metadata).encode()
    header = json.dumps({'files': {'package.json': {'offset': '0', 'size': len(payload)}}}).encode()
    body = struct.pack('<II', len(header) + 4, len(header)) + header
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(struct.pack('<II', 4, len(body)) + body + payload)


class ApplicationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        environment = patch.dict(os.environ, {'VOICESTUDIO_CONFIG': str(self.root / 'config.json')})
        environment.start(); self.addCleanup(environment.stop)

    def test_asar_version_and_distribution_are_read_offline(self):
        exe = self.root / 'voicestudio-desktop'; exe.write_bytes(b'fixture')
        asar(self.root / 'resources/app.asar', {'version': '1.2.3-beta.2', 'voicestudioDistribution': 'snap-store'})
        with patch.object(application.desktop, 'resolve_desktop', return_value=[str(exe)]), \
             patch.object(application.desktop, 'bundled_launcher', return_value=None):
            info = application.installed_application()
        self.assertEqual(info.version, '1.2.3-beta.2')
        self.assertEqual(info.method, 'store')

    def test_corrupt_app_metadata_does_not_fall_back_to_package_version(self):
        exe = self.root / 'voicestudio-desktop'; exe.write_bytes(b'fixture')
        (self.root / 'resources').mkdir()
        (self.root / 'resources/app.asar').write_bytes(b'invalid archive')
        with patch.object(application.desktop, 'resolve_desktop', return_value=[str(exe)]), \
             patch.object(application.desktop, 'bundled_launcher', return_value=None):
            with self.assertRaises(CLIError) as exc:
                application.installed_application()
        self.assertEqual(exc.exception.code, 'app_metadata_error')

    def test_legacy_appimage_version_comes_from_its_contents(self):
        image = self.root / 'VoiceStudio.AppImage'
        image.write_bytes(b'\x7fELF\x02\x01\x01\x00AI\x02' + b'\0' * 53)
        def extract(command, **kwargs):
            self.assertEqual(command, [str(image), '--appimage-extract', 'resources/app.asar'])
            asar(Path(kwargs['cwd']) / 'squashfs-root/resources/app.asar', {'version': '2.3.4'})
            return type('Result', (), {'returncode': 0})()
        with patch.object(application.subprocess, 'run', side_effect=extract):
            info = application.inspect_application(image)
        self.assertEqual(info.version, '2.3.4')
        self.assertEqual(info.method, 'appimage')

    def appimage(self):
        path = self.root / 'VoiceStudio.AppImage'
        path.write_bytes(b'\x7fELF\x02\x01\x01\x00AI\x02' + b'\0' * 53)
        return path

    def extractor(self, version):
        def extract(command, **kwargs):
            asar(Path(kwargs['cwd']) / 'squashfs-root/resources/app.asar', {'version': version})
            return type('Result', (), {'returncode': 0})()
        return extract

    def test_unchanged_appimage_does_not_require_repeated_extraction(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')) as extract:
            first = application.inspect_application(image)
        with patch.object(application.subprocess, 'run', side_effect=AssertionError('must use cached metadata')):
            second = application.inspect_application(image)
        self.assertEqual(first, second)
        extract.assert_called_once()

    def test_replaced_appimage_invalidates_cache_even_with_same_size_and_mtime(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')):
            application.inspect_application(image)
        stat = image.stat()
        replacement = self.root / 'replacement'
        replacement.write_bytes(image.read_bytes())
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        os.replace(replacement, image)
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('2.0.0')) as extract:
            self.assertEqual(application.inspect_application(image).version, '2.0.0')
        extract.assert_called_once()

    def test_corrupt_cache_is_rebuilt(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')):
            application.inspect_application(image)
        for content in ['{invalid', '[]', '{"schema": 999}', '{"metadata": null}']:
            cached = next((self.root / 'cache').glob('appimage-*.json'))
            cached.write_text(content)
            with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')) as extract:
                self.assertEqual(application.inspect_application(image).version, '1.2.3')
            extract.assert_called_once()

    def test_cache_write_failure_does_not_break_version(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')), \
             patch.object(application.os, 'replace', side_effect=PermissionError('read-only cache')):
            self.assertEqual(application.inspect_application(image).version, '1.2.3')
        self.assertFalse(list((self.root / 'cache').glob('.appimage-*')))

    def test_failed_extraction_is_not_cached(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', return_value=type('Result', (), {'returncode': 1})()):
            with self.assertRaises(ValueError):
                application.inspect_application(image)
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')) as extract:
            self.assertEqual(application.inspect_application(image).version, '1.2.3')
        extract.assert_called_once()

    def test_removed_image_does_not_return_cached_version(self):
        image = self.appimage()
        with patch.object(application.subprocess, 'run', side_effect=self.extractor('1.2.3')):
            application.inspect_application(image)
        image.unlink()
        with self.assertRaises(FileNotFoundError):
            application.inspect_application(image)

    def test_literal_shell_launcher_is_inspected_without_running_it(self):
        exe = self.root / 'native app'; exe.write_bytes(b'fixture')
        wrapper = self.root / 'launcher'
        wrapper.write_text(f'#!/bin/sh\nexport APP_SETTING=sample\nexec "{exe}" "$@"\n')
        with patch.object(application.subprocess, 'run', side_effect=AssertionError('must not run launcher')):
            self.assertEqual(application.unwrap_launcher(wrapper), exe)
        wrapper.write_text('#!/bin/sh\nexec "$(untrusted-command)"\n')
        with self.assertRaises(ValueError):
            application.unwrap_launcher(wrapper)

    def test_macos_bundle_uses_installed_plist_version(self):
        app = self.root / 'VoiceStudio.app'
        (app / 'Contents').mkdir(parents=True)
        (app / 'Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleShortVersionString': '3.2.1'}))
        asar(app / 'Contents/Resources/app.asar', {'version': '3.2.1'})
        self.assertEqual(application.inspect_application(app).version, '3.2.1')

    def test_invalid_archive_sizes_are_rejected(self):
        archive = self.root / 'app.asar'
        for data in [b'', struct.pack('<II', 4, 2**31), struct.pack('<II', 4, 8) + b'\0'*8]:
            archive.write_bytes(data)
            with self.assertRaises((ValueError, KeyError, json.JSONDecodeError)):
                application.asar_package(archive)

    def test_bundled_application_metadata_controls_version(self):
        manifest = self.root / 'desktop.json'
        manifest.write_text(json.dumps({'version': '4.5.6', 'application': '../VoiceStudio.exe',
                                        'method': 'nsis', 'arch': 'x64'}))
        with patch.object(application.desktop, 'bundled_launcher', return_value=manifest):
            with patch.dict(os.environ, {}, clear=True):
                self.assertEqual(application.app_version(), '4.5.6')


if __name__ == '__main__':
    unittest.main()
