"""Exercise command replacement with real uv in temporary tool/config directories."""
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import tempfile


def main():
    if os.name == 'nt':
        raise SystemExit('This check exercises migration of a POSIX desktop wrapper.')
    root = Path(__file__).resolve().parents[1]
    with tempfile.TemporaryDirectory(prefix='voicestudio-cli-migrate-') as directory:
        work = Path(directory)
        bin_dir = work / 'bin'
        bin_dir.mkdir()
        record = work / 'launched.json'
        fixture = work / 'desktop.py'
        fixture.write_text('import json, os\nfrom pathlib import Path\n' +
                           f'Path({str(record)!r}).write_text(json.dumps({{"setting": os.getenv("CLI_TEST_SETTING")}}))\n')
        app = work / 'metadata-app'
        (app / 'resources/app').mkdir(parents=True)
        (app / 'resources/app/package.json').write_text(json.dumps({'version': '9.8.7'}))
        executable = app / 'voicestudio-desktop'
        executable.write_bytes(b'fixture application')
        executable.chmod(0o755)
        wrapper = bin_dir / 'voicestudio'
        original = '#!/bin/sh\nexport CLI_TEST_SETTING="value with spaces"\nexec ' + shlex.join([sys.executable, str(fixture)]) + '\n'
        wrapper.write_text(original)
        wrapper.chmod(0o755)
        env = {**os.environ, 'UV_TOOL_DIR': str(work / 'tools'), 'UV_TOOL_BIN_DIR': str(bin_dir),
               'VOICESTUDIO_CONFIG': str(work / 'config/config.json'), 'PYTHONPATH': str(root / 'cli')}
        env.pop('VOICESTUDIO_DESKTOP_APP', None)
        subprocess.run([sys.executable, '-m', 'voicestudio_cli.install', str(root / 'cli'),
                        '--replace-launcher', '--no-modify-path'], check=True, env=env, cwd=work)
        env.pop('PYTHONPATH')
        result = subprocess.run([str(wrapper), '--version'], check=True, capture_output=True, text=True,
                                env=env | {'VOICESTUDIO_DESKTOP_APP': str(executable)}, cwd=work)
        assert result.stdout.strip() == 'VoiceStudio 9.8.7'
        subprocess.run([str(wrapper)], check=True, env=env, cwd=work)
        assert json.loads(record.read_text()) == {'setting': 'value with spaces'}
        backup = work / 'config/desktop-launcher'
        assert backup.read_text() == original
        subprocess.run(['uv', 'tool', 'uninstall', 'voicestudio-cli'], check=True, env=env, cwd=work)
        assert backup.read_text() == original
    print('PASS: command installation, launcher dispatch and environment preservation')


if __name__ == '__main__':
    main()
