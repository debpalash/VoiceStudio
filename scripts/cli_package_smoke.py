"""Install a built wheel in a throwaway environment and exercise its entry point."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import venv
import zipfile

ROOT = Path(__file__).resolve().parent.parent

# The installed command's startup rules name real commands and never conflict:
# OFFLINE commands never contact a backend, OBSERVE commands report live state
# without starting one. tests/cli/test_contracts.py runs this against the source.
STARTUP_RULES_CHECK = '''
from voicestudio_cli.__main__ import OFFLINE, build_parser
from voicestudio_cli.runtime import OBSERVE
_, nodes = build_parser()
commands = {parser.get_default('handler') for parser in nodes.values()} - {None}
assert not OBSERVE - commands, sorted(OBSERVE - commands)
assert not set(OFFLINE) - commands, sorted(set(OFFLINE) - commands)
assert not OBSERVE & set(OFFLINE), sorted(OBSERVE & set(OFFLINE))
'''


def main():
    wheel = Path(sys.argv[1]).resolve()
    canonical = json.loads((ROOT / 'package.json').read_text(encoding='utf-8'))['version']
    with zipfile.ZipFile(wheel) as archive:
        metadata = archive.read(next(n for n in archive.namelist() if n.endswith('.dist-info/METADATA'))).decode()
        assert 'Requires-Dist:' not in metadata, 'CLI must not install inference dependencies'
        assert 'voicestudio_cli/contracts.json' in archive.namelist()

    class Health(BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass
        def do_GET(self):
            data = json.dumps({'status': 'ok', 'version': canonical}).encode()
            self.send_response(200)
            self.send_header('Content-Type', 'application/json')
            self.send_header('X-OmniVoice-Backend', canonical)
            self.send_header('Content-Length', str(len(data)))
            self.end_headers(); self.wfile.write(data)

    server = ThreadingHTTPServer(('127.0.0.1', 0), Health)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix='voicestudio-cli-smoke-') as directory:
            env_path = Path(directory) / 'venv'
            venv.create(env_path, with_pip=True)
            binary_dir = env_path / ('Scripts' if os.name == 'nt' else 'bin')
            python = binary_dir / ('python.exe' if os.name == 'nt' else 'python')
            subprocess.run([str(python), '-m', 'pip', 'install', '--no-index', '--no-deps', str(wheel)], check=True)
            executable = binary_dir / ('voicestudio.exe' if os.name == 'nt' else 'voicestudio')
            env = {**os.environ, 'VOICESTUDIO_CONFIG': str(Path(directory) / 'config.json'),
                   'VOICESTUDIO_URL': f'http://127.0.0.1:{server.server_port}'}
            app = Path(directory) / 'application'
            (app / 'resources/app').mkdir(parents=True)
            (app / 'resources/app/package.json').write_text(json.dumps({'version': '9.8.7'}), encoding='utf-8')
            desktop = app / ('VoiceStudio.exe' if os.name == 'nt' else 'voicestudio-desktop')
            desktop.write_bytes(b'fixture application')
            desktop.chmod(0o755)
            env['VOICESTUDIO_DESKTOP_APP'] = str(desktop)
            for key in ['PYTHONPATH', 'OMNIVOICE_API_KEY']:
                env.pop(key, None)
            rules = subprocess.run([str(python), '-c', STARTUP_RULES_CHECK],
                                   cwd=directory, env=env, capture_output=True, text=True, timeout=30)
            assert rules.returncode == 0, rules.stderr
            for args in [['version'], ['--version'], ['-v'], ['voices', 'reference', '--help'], ['status', '--json']]:
                result = subprocess.run([str(executable), *args], cwd=directory, env=env,
                                        capture_output=True, text=True, timeout=30)
                assert result.returncode == 0, result.stderr
                assert ('9.8.7' if args[0] in {'version', '--version', '-v'} else canonical) in result.stdout or '--help' in args, result.stdout
    finally:
        server.shutdown(); server.server_close(); thread.join()
    print('PASS: installed wheel, entry point, offline help/version and HTTP health')


if __name__ == '__main__':
    main()
