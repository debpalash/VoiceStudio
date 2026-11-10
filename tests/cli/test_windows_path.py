"""The Windows installer's PATH step: unexpanded entries, silent installs, update rollback."""
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import uuid

PACKAGING = Path(__file__).resolve().parents[2] / 'electron' / 'packaging'
SCRIPT = PACKAGING / 'cli-path.ps1'
INSTALLER = PACKAGING / 'cli-installer.nsh'


class InstallerScriptTests(unittest.TestCase):
    def test_path_is_edited_as_stored_in_the_registry(self):
        text = SCRIPT.read_text(encoding='utf-8')
        self.assertIn('DoNotExpandEnvironmentNames', text)
        self.assertNotIn("SetEnvironmentVariable('Path'", text)

    def test_a_silent_install_never_waits_on_a_dialog(self):
        for line in INSTALLER.read_text(encoding='utf-8').splitlines():
            if line.strip().startswith('MessageBox'):
                self.assertIn('/SD ', line)

    def test_an_update_keeps_the_path_entry_for_a_rollback(self):
        uninstall = INSTALLER.read_text(encoding='utf-8').split('!macro customUnInstall', 1)[1]
        self.assertLess(uninstall.index('${IfNot} ${isUpdated}'), uninstall.index('-Action Uninstall'))

    def test_uninstall_offers_to_delete_downloads_but_defaults_to_keeping_them(self):
        uninstall = INSTALLER.read_text(encoding='utf-8').split('!macro customUnInstall', 1)[1]
        prompt = next(l for l in uninstall.splitlines() if l.strip().startswith('MessageBox'))
        self.assertIn('MB_DEFBUTTON2', prompt)  # No is the default button
        self.assertIn('/SD IDNO', prompt)  # a silent uninstall keeps them
        removals = [l.strip() for l in uninstall.splitlines() if 'RMDir' in l]
        self.assertEqual(removals, [r'RMDir /r "$APPDATA\VoiceStudio\runtime"', r'RMDir /r "$LOCALAPPDATA\OmniVoice\hf_cache"'])
        # Never during an update, and never voices/projects (%APPDATA%\OmniVoice).
        self.assertLess(uninstall.index('${IfNot} ${isUpdated}'), uninstall.index('RMDir'))


@unittest.skipUnless(sys.platform == 'win32', 'runs the real script against the Windows registry')
class RegistryTests(unittest.TestCase):
    def setUp(self):
        import winreg
        self.winreg = winreg
        self.key = rf'Software\VoiceStudioTests\{uuid.uuid4().hex}'
        self.addCleanup(self._delete_key)
        # The script adds its own folder, so run a copy from a scratch folder.
        folder = Path(tempfile.mkdtemp()); self.addCleanup(shutil.rmtree, folder, True)
        self.script = folder / 'path.ps1'
        shutil.copy(SCRIPT, self.script)
        self.folder = str(folder.resolve())

    def _delete_key(self):
        try:
            self.winreg.DeleteKey(self.winreg.HKEY_CURRENT_USER, self.key)
        except OSError:
            pass

    def run_script(self, action):
        powershell = Path(os.environ.get('SystemRoot', r'C:\Windows')) / 'System32/WindowsPowerShell/v1.0/powershell.exe'
        subprocess.run([str(powershell), '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass',
                        '-File', str(self.script), '-Action', action, '-TestKey', self.key], check=True, timeout=60)

    def read(self):
        with self.winreg.OpenKey(self.winreg.HKEY_CURRENT_USER, self.key) as key:
            return self.winreg.QueryValueEx(key, 'Path')

    def test_install_and_uninstall_keep_unexpanded_entries_and_the_value_type(self):
        original = r'%SystemRoot%\system32;%USERPROFILE%\bin'
        with self.winreg.CreateKey(self.winreg.HKEY_CURRENT_USER, self.key) as key:
            self.winreg.SetValueEx(key, 'Path', 0, self.winreg.REG_EXPAND_SZ, original)
        self.run_script('Install')
        self.assertEqual(self.read(), (f'{self.folder};{original}', self.winreg.REG_EXPAND_SZ))
        self.run_script('Install')  # idempotent
        self.assertEqual(self.read()[0], f'{self.folder};{original}')
        self.run_script('Uninstall')
        self.assertEqual(self.read(), (original, self.winreg.REG_EXPAND_SZ))


if __name__ == '__main__':
    unittest.main()
