"""Install the application's terminal command and handle launcher conflicts."""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile

from .desktop import launcher_config
from .errors import CLIError


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp = tempfile.mkstemp(prefix='.launcher-', dir=path.parent)
    try:
        with os.fdopen(fd, 'wb') as stream:
            stream.write(content)
        os.chmod(temp, mode)
        os.replace(temp, path)
    finally:
        Path(temp).unlink(missing_ok=True)


def install(source, *, replace_launcher=False, update_shell=True, uv='uv'):
    """Install with uv, preserving an explicitly replaced launcher for desktop dispatch."""
    bin_dir = Path(subprocess.check_output([uv, 'tool', 'dir', '--bin'], text=True).strip())
    target = bin_dir / ('voicestudio.exe' if sys.platform == 'win32' else 'voicestudio')
    installed = subprocess.check_output([uv, 'tool', 'list'], text=True)
    owns_command = any(line.startswith('voicestudio-cli v') for line in installed.splitlines())
    manifest = launcher_config()
    previous_manifest = manifest.read_bytes() if manifest.exists() else None
    saved = None
    symlink = None
    old_mode = None
    backup = manifest.with_name('desktop-launcher')
    backup_created = False
    if (target.exists() or target.is_symlink()) and not owns_command:
        if not replace_launcher:
            raise CLIError('command_exists', f'{target} already exists. Rerun with --replace-launcher to '
                           'back up the existing launcher and replace the command.')
        if target.is_symlink():
            symlink = os.readlink(target)
            executable = str(target.resolve())
            if not target.is_file():
                raise CLIError('invalid_launcher', 'Existing launcher does not point to a file.')
            with target.open('rb') as stream:
                header = stream.read(8192)
            if header.startswith(b'#!') and b'voicestudio_cli' in header:
                raise CLIError('invalid_launcher', 'Existing command points to another CLI installation. '
                               'Remove it with its package manager before migrating.')
        else:
            with target.open('rb') as stream:
                header = stream.read(8192)
            old_mode = target.stat().st_mode & 0o777
            # Moving a native application binary away from its resources breaks it.
            if not header.startswith(b'#!') or b'voicestudio_cli' in header:
                raise CLIError('invalid_launcher', 'Existing executable is not a desktop shell wrapper. '
                               'Use a different UV_TOOL_BIN_DIR to install without replacing this executable.')
            if backup.exists() or backup.is_symlink():
                raise CLIError('command_exists', f'A saved launcher already exists at {backup}; inspect it before replacing another launcher.')
            saved = target.read_bytes()
            atomic_write(backup, saved, 0o700)
            backup_created = True
            executable = str(backup)
        atomic_write(manifest, (json.dumps({'executable': executable}, indent=2) + '\n').encode())
    try:
        subprocess.run([uv, 'tool', 'install', '--force', str(source)], check=True)
    except (OSError, subprocess.CalledProcessError):
        if symlink is not None:
            target.unlink(missing_ok=True)
            target.symlink_to(symlink)
        elif saved is not None:
            atomic_write(target, saved, old_mode)
        if previous_manifest is not None:
            atomic_write(manifest, previous_manifest)
        else:
            manifest.unlink(missing_ok=True)
        if backup_created:
            backup.unlink(missing_ok=True)
        message = 'CLI installation failed.'
        if saved is not None or symlink is not None:
            message += ' The previous launcher was restored.'
        raise CLIError('install_failed', message) from None
    if update_shell:
        result = subprocess.run([uv, 'tool', 'update-shell'])
        if result.returncode:
            print(f'voicestudio: warning: PATH configuration failed; add {bin_dir} to PATH.', file=sys.stderr)
    print(f'Installed command: {target}')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Install the VoiceStudio terminal command using uv.')
    parser.add_argument('source', help='CLI package directory, wheel, or explicit package specifier')
    parser.add_argument('--replace-launcher', action='store_true', help='Preserve an existing desktop launcher and replace its public command')
    parser.add_argument('--no-modify-path', action='store_true', help='Skip uv tool update-shell')
    args = parser.parse_args(argv)
    try:
        install(args.source, replace_launcher=args.replace_launcher, update_shell=not args.no_modify_path)
    except (CLIError, OSError, subprocess.CalledProcessError) as exc:
        print(f'voicestudio: {exc}', file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
