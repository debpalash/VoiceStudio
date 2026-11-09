"""Updates are handed to the app's own updater; the CLI makes no network requests."""
import contextlib
import io
import json
from pathlib import Path
import re
import sys
import tempfile
import unittest
from unittest.mock import patch

PACKAGE = Path(__file__).resolve().parents[2] / 'cli' / 'voicestudio_cli'
sys.path.insert(0, str(PACKAGE.parent))
from voicestudio_cli import desktop
from voicestudio_cli.application import Application


class UpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.exe = self.root / 'VoiceStudio.AppImage'; self.exe.write_bytes(b'original'); self.exe.chmod(0o755)
        self.app = Application('1.0.2', self.exe, 'appimage', 'linux', 'x64')

    def run_main(self, *args):
        from voicestudio_cli.__main__ import main
        with patch('voicestudio_cli.__main__.installed_application', return_value=self.app), \
             contextlib.redirect_stdout(io.StringIO()) as output, \
             contextlib.redirect_stderr(io.StringIO()) as errors:
            code = main(list(args))
        return code, output.getvalue(), errors.getvalue()

    def test_update_opens_the_app_updater(self):
        for command in [('update',), ('update', 'check')]:  # check is the former spelling
            with self.subTest(command=command), patch.object(desktop, 'desktop_process') as launch:
                code, output, _ = self.run_main(*command, '--json')
            self.assertEqual(code, 0)
            result = json.loads(output)
            self.assertTrue(result['opened'])
            self.assertEqual(result['current'], '1.0.2')
            self.assertIn('Settings → Updates', result['message'])
            launch.assert_called_once_with([desktop.CHECK_UPDATES_FLAG], detached=True)

    def test_store_and_source_installs_keep_their_own_update_instructions(self):
        for method, distribution, fragment in [('store', 'snap-store', 'Snap'),
                                               ('source', 'direct', 'source checkout')]:
            self.app = Application('1.0.2', self.exe, method, 'linux', 'x64', distribution)
            with self.subTest(method=method), patch.object(desktop, 'desktop_process') as launch:
                code, _, errors = self.run_main('update', '--json')
            self.assertEqual(code, 1)
            self.assertIn(fragment, json.loads(errors)['error']['message'])
            launch.assert_not_called()

    def test_package_managed_installs_never_open_a_disabled_updater(self):
        with patch.dict('os.environ', {'VOICESTUDIO_DISABLE_UPDATER': '1'}), \
             patch.object(desktop, 'desktop_process') as launch:
            code, _, errors = self.run_main('update', '--json')
        self.assertEqual(code, 1)
        self.assertIn('package manager', json.loads(errors)['error']['message'])
        launch.assert_not_called()

    def test_removed_release_and_channel_options_are_rejected(self):
        for flag in [('--release', '1.0.1'), ('--channel', 'beta')]:
            with self.subTest(flag=flag), patch.object(desktop, 'desktop_process') as launch:
                code, _, _ = self.run_main('update', *flag)
            self.assertEqual(code, 2)
            launch.assert_not_called()

    def test_the_cli_has_no_internet_endpoints_of_its_own(self):
        # The app's updater is the sanctioned update check (CONTRIBUTING.md →
        # Local-first); a CLI that fetched releases itself would be a second one.
        for source in PACKAGE.glob('*.py'):
            text = source.read_text(encoding='utf-8')
            with self.subTest(module=source.name):
                self.assertIsNone(re.search(r'https://(?!127\.0\.0\.1|localhost)', text))
                self.assertNotIn('urlopen', text.replace('urllib.request', ''))


if __name__ == '__main__':
    unittest.main()
