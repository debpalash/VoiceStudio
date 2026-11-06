"""Black-box CLI contracts; no backend, models or third-party packages required."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]


class CommandTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        search_path = os.pathsep.join([str(ROOT / 'cli'), str(ROOT / 'tests/cli/network_guard')])
        self.env = {**os.environ, 'PYTHONPATH': search_path,
                    'VOICESTUDIO_CONFIG': str(Path(self.temp.name) / 'config.json'),
                    'VOICESTUDIO_URL': 'http://127.0.0.1:1',
                    'HF_HUB_OFFLINE': '1', 'HF_HUB_CACHE': str(Path(self.temp.name) / 'empty-hf')}
        self.env.pop('OMNIVOICE_API_KEY', None)
        self.app_version = '9.8.7'
        app = Path(self.temp.name) / 'app'
        (app / 'resources/app').mkdir(parents=True)
        (app / 'resources/app/package.json').write_text(json.dumps({'version': self.app_version}))
        executable = app / ('VoiceStudio.exe' if os.name == 'nt' else 'voicestudio-desktop')
        executable.write_bytes(b'fixture application')
        executable.chmod(0o755)
        self.env['VOICESTUDIO_DESKTOP_APP'] = str(executable)

    def run_cli(self, *args, input=None):
        return subprocess.run([sys.executable, '-m', 'voicestudio_cli', *args],
                              input=input, capture_output=True, env=self.env, timeout=10)

    def test_version_aliases_are_offline_and_equal(self):
        results = [self.run_cli(*args) for args in [('version',), ('--version',), ('-v',)]]
        self.assertTrue(all(r.returncode == 0 for r in results))
        self.assertEqual(len({r.stdout for r in results}), 1)
        self.assertEqual(results[0].stdout, f'VoiceStudio {self.app_version}\n'.encode())

    def test_nested_help(self):
        for args in [('--help',), ('voices', '--help'),
                     ('voices', 'reference', '--help'), ('voices', 'clone', '--help'),
                     ('help', 'voices', 'clone'), ('--json', 'help', 'voices', 'reference')]:
            with self.subTest(args=args):
                r = self.run_cli(*args)
                self.assertEqual(r.returncode, 0, r.stderr)
                self.assertIn(b'usage:', r.stdout)

    def test_typo_is_not_executed(self):
        r = self.run_cli('udpate')
        self.assertEqual(r.returncode, 2)
        self.assertIn(b'update', r.stderr)
        self.assertEqual(r.stdout, b'')

    def test_invalid_usage_json(self):
        r = self.run_cli('voices', 'show', '--json')
        self.assertEqual(r.returncode, 2)
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'usage_error')

    def test_connection_refused_is_actionable(self):
        r = self.run_cli('status', '--json', '--timeout', '0.1')
        self.assertEqual(r.returncode, 1)
        self.assertEqual(json.loads(r.stderr)['error']['code'], 'connection_error')
        self.assertIn(b'Open VoiceStudio', r.stderr)

    def test_removed_command_is_unknown(self):
        r = self.run_cli('capabilities')
        self.assertEqual(r.returncode, 2)

    def test_unmanaged_application_updates_give_instructions(self):
        r = self.run_cli('update', 'info', '--json')
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(json.loads(r.stdout)['method'], 'nsis' if os.name == 'nt' else 'directory')

    def test_version_and_help_ignore_corrupt_configuration(self):
        Path(self.env['VOICESTUDIO_CONFIG']).write_text('{broken')
        for args in [('version',), ('version', '--json'), ('--version',), ('voices', '--help'), ('update', 'info')]:
            with self.subTest(args=args):
                result = self.run_cli(*args)
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_errors_read_as_sentences_with_one_suggestion(self):
        r = self.run_cli('udpate')
        self.assertEqual(r.stderr.decode().strip(),
                         "voicestudio: Unknown command 'udpate'. Did you mean 'update'? Run voicestudio --help.")
        r = self.run_cli('settings', 'get', 'compute')
        self.assertIn(b"'compute' is not a valid setting. Did you mean 'compute-device'?", r.stderr)
        self.assertNotIn(b'?.', r.stderr)

    def test_missing_app_is_explained_without_internal_detail(self):
        self.env['VOICESTUDIO_DESKTOP_APP'] = str(Path(self.temp.name) / 'missing')
        r = self.run_cli('version')
        self.assertEqual(r.returncode, 1)
        self.assertIn(b"Can't find the VoiceStudio app", r.stderr)


if __name__ == '__main__':
    unittest.main()
