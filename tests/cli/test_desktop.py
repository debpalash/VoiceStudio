"""Desktop dispatch and migration use fixture executables, never the installed app."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / 'cli'))
from voicestudio_cli import desktop, install, updates
from voicestudio_cli.__main__ import main
from voicestudio_cli.errors import CLIError


def _symlink_or_skip(test, link, target):
    """Windows needs Developer Mode or elevation to create symlinks; skip, never error."""
    try:
        link.symlink_to(target)
    except (OSError, NotImplementedError) as exc:
        test.skipTest(f'symlinks unavailable in this environment: {exc}')


class DesktopTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # The CLI resolves executables (symlinks, macOS /private, Windows 8.3 names).
        self.root = Path(self.temp.name).resolve()
        self.env = patch.dict(os.environ, {
            'VOICESTUDIO_CONFIG': str(self.root / 'config.json'),
            'VOICESTUDIO_DESKTOP_APP': str(self.root / 'missing'),
        })
        self.env.start()
        self.addCleanup(self.env.stop)

    def executable(self, name='desktop'):
        path = self.root / name
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        return path

    def test_empty_arguments_launch_desktop_without_backend_or_config(self):
        (self.root / 'config.json').write_text('{broken')
        with patch('voicestudio_cli.__main__.launch_desktop', return_value=0) as launch:
            self.assertEqual(main([]), 0)
            launch.assert_called_once_with([])

    def test_files_from_the_desktop_launcher_open_the_app(self):
        # Exec=... %U: a file dropped on the menu entry, or a URL, is not a command.
        dropped = str(self.root / 'take one.wav')
        for launcher_args in ([dropped], ['file:///tmp/take%20one.wav'], [dropped, 'https://example.com/a.wav']):
            with self.subTest(launcher_args=launcher_args), \
                    patch('voicestudio_cli.__main__.launch_desktop', return_value=0) as launch:
                self.assertEqual(main(['--no-sandbox', *launcher_args]), 0)
                launch.assert_called_once_with(['--no-sandbox'])
        # A command with a path argument is still a command.
        self.assertFalse(desktop.launcher_targets(['voices', 'show-file', dropped]))
        self.assertFalse(desktop.launcher_targets(['./take.wav']))

    def test_commands_never_launch_gui(self):
        with patch('voicestudio_cli.__main__.launch_desktop') as launch, \
             patch('voicestudio_cli.__main__.app_version', return_value='9.8.7'):
            self.assertEqual(main(['version']), 0)
            self.assertEqual(main(['voices']), 0)
            self.assertEqual(main(['udpate']), 2)
            launch.assert_not_called()

    def test_snap_switch_does_not_turn_cli_help_into_gui_launch(self):
        self.assertEqual(desktop.split_arguments(['--no-sandbox', '--help']), (['--help'], ['--no-sandbox']))
        self.assertEqual(desktop.split_arguments(['--ozone-platform', 'wayland']),
                         ([], ['--ozone-platform', 'wayland']))
        self.assertEqual(desktop.split_arguments(['voices', '--no-sandbox']),
                         (['voices', '--no-sandbox'], []))
        for missing in (['--user-data-dir'], ['--user-data-dir=', 'voices', 'list']):
            with self.assertRaises(CLIError):
                desktop.split_arguments(missing)

    def test_app_switches_without_a_command_open_the_app(self):
        # The AppImage/.deb entry is this command. Playwright starts it with these
        # (v0.5.8-beta.4's packaged smoke failed when they were a usage error).
        playwright = ['--no-sandbox', '--inspect=0', '--remote-debugging-port=0', '--user-data-dir=/p']
        for argv, forwarded in ((playwright, playwright),
                                (['--enable-features=UseOzonePlatform', '--disable-gpu-sandbox'],
                                 ['--enable-features=UseOzonePlatform', '--disable-gpu-sandbox'])):
            with self.subTest(argv=argv), \
                    patch('voicestudio_cli.__main__.launch_desktop', return_value=0) as launch:
                self.assertEqual(main(argv), 0)
                launch.assert_called_once_with(forwarded)
        # CLI options, a bare --, and any command keep their CLI meaning.
        with patch('voicestudio_cli.__main__.launch_desktop') as launch, \
                patch('voicestudio_cli.__main__.app_version', return_value='9.8.7'), \
                redirect_stdout(StringIO()):
            self.assertEqual(main(['--json']), 0)
            self.assertEqual(main(['--inspect=0', 'version']), 2)
            self.assertEqual(main(['--', '--inspect=0']), 2)
            launch.assert_not_called()

    def test_explicit_missing_app_does_not_fall_back_to_real_app(self):
        with patch.object(desktop, 'desktop_candidates', side_effect=AssertionError('must not discover')):
            with self.assertRaises(CLIError) as error:
                desktop.resolve_desktop()
        self.assertEqual(error.exception.code, 'desktop_not_found')

    def test_linux_installer_location_is_discovered(self):
        path = self.root / '.local/bin/VoiceStudio'
        path.parent.mkdir(parents=True)
        path.write_text('#!/bin/sh\nexit 0\n')
        path.chmod(0o755)
        os.environ.pop('VOICESTUDIO_DESKTOP_APP')
        with patch.object(Path, 'home', return_value=self.root), patch.object(sys, 'platform', 'linux'):
            with patch.object(desktop.shutil, 'which', return_value=None):
                self.assertEqual(desktop.resolve_desktop(), [str(path)])

    def test_background_launches_keep_the_launchers_switches(self):
        # Snap runs "voicestudio --no-sandbox voices list"; Chromium cannot start
        # under strict confinement without that switch, window or not.
        self.addCleanup(desktop.launcher_arguments.clear)
        path = self.executable()
        with patch('voicestudio_cli.__main__.app_version', return_value='9.8.7'):
            self.assertEqual(main(['--no-sandbox', '--ozone-platform', 'x11', desktop.STOP_FLAG, 'version']), 0)
        with patch.dict(os.environ, {'VOICESTUDIO_DESKTOP_APP': str(path)}), \
             patch.object(desktop.subprocess, 'Popen') as popen:
            desktop.desktop_process([desktop.BACKGROUND_FLAG], detached=True)
        self.assertEqual(popen.call_args.args[0],
                         [str(path), '--no-sandbox', '--ozone-platform', 'x11', desktop.BACKGROUND_FLAG])

    def test_a_chosen_profile_is_used_for_discovery_and_launch(self):
        from voicestudio_cli import runtime
        self.addCleanup(desktop.launcher_arguments.clear)
        profile = self.root / 'custom profile'
        path = self.executable()
        for arguments in (['--user-data-dir', str(profile)], ['--user-data-dir=' + str(profile)]):
            with self.subTest(arguments=arguments), patch.dict(os.environ, {'VOICESTUDIO_DESKTOP_APP': str(path)}), \
                 patch('voicestudio_cli.__main__.app_version', return_value='9.8.7'), \
                 patch.object(desktop.subprocess, 'Popen') as popen:
                self.assertEqual(main([*arguments, 'version']), 0)
                self.assertEqual(runtime.user_data(), profile)  # where backend.json is read
                desktop.desktop_process([desktop.BACKGROUND_FLAG])
                self.assertEqual(popen.call_args.args[0],
                                 [str(path), desktop.BACKGROUND_FLAG, '--user-data-dir=' + str(profile)])

    def test_launch_preserves_environment_and_uses_argument_array(self):
        path = self.executable('app with spaces')
        with patch.dict(os.environ, {'VOICESTUDIO_DESKTOP_APP': str(path), 'CLI_TEST_SETTING': 'value with spaces'}):
            with patch.object(desktop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(desktop.launch_desktop(['--user-data-dir', 'profile with spaces']), 0)
                self.assertEqual(run.call_args.args[0], [str(path), '--user-data-dir', 'profile with spaces'])
                self.assertEqual(run.call_args.kwargs['env']['CLI_TEST_SETTING'], 'value with spaces')

    def test_foreground_launch_opens_the_selected_profile(self):
        path = self.executable('app')
        profile = self.root / 'chosen profile'
        with patch.dict(os.environ, {'VOICESTUDIO_DESKTOP_APP': str(path), 'VOICESTUDIO_USER_DATA': str(profile)}):
            with patch.object(desktop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                self.assertEqual(desktop.launch_desktop(), 0)
                self.assertEqual(run.call_args.args[0], [str(path), '--user-data-dir=' + str(profile)])
                desktop.launch_desktop(['--user-data-dir', 'explicit'])  # an explicit profile wins
                self.assertEqual(run.call_args.args[0], [str(path), '--user-data-dir', 'explicit'])

    def test_launch_failure_is_actionable(self):
        with patch.object(desktop, 'resolve_desktop', return_value=['fixture']):
            with patch.object(desktop.subprocess, 'run', side_effect=OSError('permission denied')):
                with self.assertRaises(CLIError) as error:
                    desktop.launch_desktop()
                self.assertEqual(error.exception.code, 'desktop_launch_failed')
            with patch.object(desktop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 7)):
                with self.assertRaisesRegex(CLIError, 'status 7'):
                    desktop.launch_desktop()

    def test_saved_wrapper_and_frozen_manifest(self):
        os.environ.pop('VOICESTUDIO_DESKTOP_APP')
        path = self.executable()
        desktop.launcher_config().write_text(json.dumps({'executable': str(path)}))
        self.assertEqual(desktop.resolve_desktop(), [str(path)])
        frozen = self.executable('voicestudio')
        frozen.with_name('desktop.json').write_text(json.dumps({'executable': 'desktop', 'application': 'desktop',
                                                               'version': '9.8.7', 'method': 'directory', 'arch': 'x64'}))
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'executable', str(frozen)):
            self.assertEqual(desktop.resolve_desktop(), [str(path)])
            self.assertEqual(updates.install_info()['version'], '9.8.7')
            frozen.with_name('desktop.json').write_text(json.dumps({'executable': 'voicestudio'}))
            with self.assertRaisesRegex(CLIError, 'back to the CLI'):
                desktop.resolve_desktop()

    def test_frozen_launch_restores_system_library_environment(self):
        with patch.object(sys, 'frozen', True, create=True), patch.object(sys, 'platform', 'linux'):
            with patch.dict(os.environ, {'LD_LIBRARY_PATH': '/frozen', 'LD_LIBRARY_PATH_ORIG': '/system'}):
                with patch.object(desktop, 'resolve_desktop', return_value=['fixture']):
                    with patch.object(desktop.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
                        desktop.launch_desktop()
                        env = run.call_args.kwargs['env']
                        self.assertEqual(env['LD_LIBRARY_PATH'], '/system')
                        self.assertNotIn('LD_LIBRARY_PATH_ORIG', env)
                        self.assertEqual(env['PYINSTALLER_RESET_ENVIRONMENT'], '1')

    def test_macos_app_bundle_uses_open(self):
        app = self.root / 'VoiceStudio.app'
        app.mkdir()
        with patch.dict(os.environ, {'VOICESTUDIO_DESKTOP_APP': str(app)}), patch.object(sys, 'platform', 'darwin'):
            self.assertEqual(desktop.resolve_desktop(), ['/usr/bin/open', '-a', str(app), '--args'])


class InstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        # The CLI resolves executables (symlinks, macOS /private, Windows 8.3 names).
        self.root = Path(self.temp.name).resolve()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.target = self.bin / ('voicestudio.exe' if sys.platform == 'win32' else 'voicestudio')
        self.wrapper = b'#!/bin/sh\nexport CLI_TEST_SETTING="value with spaces"\nexec /opt/example/app "$@"\n'
        self.env = patch.dict(os.environ, {'VOICESTUDIO_CONFIG': str(self.root / 'config/config.json')})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.query = patch.object(install.subprocess, 'check_output', side_effect=[str(self.bin) + '\n', ''])
        self.query.start()
        self.addCleanup(self.query.stop)

    def test_refuses_collision_without_explicit_replacement(self):
        self.target.write_bytes(self.wrapper)
        with patch.object(install.subprocess, 'run') as run:
            with self.assertRaises(CLIError):
                install.install('./cli')
            run.assert_not_called()
        self.assertEqual(self.target.read_bytes(), self.wrapper)

    def test_replacement_preserves_wrapper_and_configuration(self):
        self.target.write_bytes(self.wrapper)
        self.target.chmod(0o755)
        output = StringIO()
        with patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)) as run:
            with redirect_stdout(output):
                install.install('./cli', replace_launcher=True)
            self.assertEqual(run.call_args_list[0].args[0], ['uv', 'tool', 'install', '--force', './cli'])
            self.assertEqual(run.call_args_list[1].args[0], ['uv', 'tool', 'update-shell'])
        manifest = json.loads(desktop.launcher_config().read_text(encoding='utf-8'))
        backup = Path(manifest['executable'])
        self.assertEqual(backup.read_bytes(), self.wrapper)
        self.assertTrue(os.access(backup, os.X_OK))
        self.assertEqual(output.getvalue().splitlines(), [f'Installed command: {self.target}'])

    def test_fresh_install_reports_the_installed_command(self):
        output = StringIO()
        with patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            with redirect_stdout(output):
                install.install('./cli', update_shell=False)
        self.assertEqual(output.getvalue().splitlines(), [f'Installed command: {self.target}'])

    def test_failed_fresh_install_does_not_claim_a_launcher_was_restored(self):
        with patch.object(install.subprocess, 'run', side_effect=subprocess.CalledProcessError(1, 'uv')):
            with self.assertRaises(CLIError) as error:
                install.install('./cli', update_shell=False)
        self.assertEqual(str(error.exception), 'CLI installation failed.')

    def test_path_warning_is_reported_only_when_shell_configuration_fails(self):
        with patch.object(install.subprocess, 'run', side_effect=[subprocess.CompletedProcess([], 0),
                                                                subprocess.CompletedProcess([], 1)]):
            with patch.object(sys, 'stderr', new_callable=StringIO) as errors, redirect_stdout(StringIO()):
                install.install('./cli')
        self.assertIn(str(self.bin), errors.getvalue())
        self.assertIn('PATH configuration failed', errors.getvalue())

    def test_failed_install_restores_wrapper_and_config(self):
        self.target.write_bytes(self.wrapper)
        self.target.chmod(0o755)
        desktop.launcher_config().parent.mkdir()
        desktop.launcher_config().write_text('{"executable": "previous"}')
        def fail(*args, **kwargs):
            self.target.write_text('damaged replacement')
            raise subprocess.CalledProcessError(1, args)
        with patch.object(install.subprocess, 'run', side_effect=fail):
            with self.assertRaises(CLIError):
                install.install('./cli', replace_launcher=True)
        self.assertEqual(self.target.read_bytes(), self.wrapper)
        self.assertEqual(json.loads(desktop.launcher_config().read_text(encoding='utf-8')), {'executable': 'previous'})
        self.assertFalse(desktop.launcher_config().with_name('desktop-launcher').exists())

    def test_symlink_migration_keeps_real_desktop_in_place(self):
        app = self.root / 'VoiceStudio'
        app.write_bytes(b'fixture native desktop')
        _symlink_or_skip(self, self.target, app)
        with patch.object(install.subprocess, 'run', return_value=subprocess.CompletedProcess([], 0)):
            install.install('./cli', replace_launcher=True, update_shell=False)
        self.assertEqual(json.loads(desktop.launcher_config().read_text(encoding='utf-8'))['executable'], str(app))
        self.assertEqual(app.read_bytes(), b'fixture native desktop')

    def test_native_binary_is_not_moved_away_from_its_resources(self):
        self.target.write_bytes(b'MZ native executable')
        with patch.object(install.subprocess, 'run') as run:
            with self.assertRaises(CLIError):
                install.install('./cli', replace_launcher=True)
            run.assert_not_called()
        self.assertEqual(self.target.read_bytes(), b'MZ native executable')

    def test_other_cli_symlink_cannot_be_saved_as_a_recursive_desktop_launcher(self):
        other = self.root / 'other-cli'
        other.write_bytes(b'#!/usr/bin/python\nfrom voicestudio_cli.__main__ import main\nmain()\n')
        _symlink_or_skip(self, self.target, other)
        with patch.object(install.subprocess, 'run') as run:
            with self.assertRaises(CLIError):
                install.install('./cli', replace_launcher=True)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
