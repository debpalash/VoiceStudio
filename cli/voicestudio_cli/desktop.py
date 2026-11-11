"""Resolve and launch installed desktop distributions."""
import ctypes
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

from .config import config_path
from .errors import CLIError


# Desktop-ownership switches understood by the Electron app (electron/src/main/headless.ts).
BACKGROUND_FLAG = '--voicestudio-background'
STOP_FLAG = '--voicestudio-stop'
CHECK_UPDATES_FLAG = '--voicestudio-check-updates'

# Snap injects --no-sandbox even for CLI invocations. Route launcher switches
# separately so they do not enter the HTTP command parser. The Linux package's
# public executable is this CLI, so the app's own relaunches pass through here.
DESKTOP_SWITCHES = {'--no-sandbox', '--disable-gpu', '--disable-gpu-compositing',
                    BACKGROUND_FLAG, STOP_FLAG, CHECK_UPDATES_FLAG}
DESKTOP_VALUES = {'--user-data-dir', '--ozone-platform', '--force-device-scale-factor'}
INTENT_FLAGS = {BACKGROUND_FLAG, STOP_FLAG, CHECK_UPDATES_FLAG}
# Switches the launcher passed with this command (Snap's --no-sandbox, a chosen
# --ozone-platform, ...). Background launches need them as much as a window does.
launcher_arguments = []


def remember_launcher_arguments(arguments):
    kept, items = [], iter(arguments)
    for item in items:
        name, _, value = item.partition('=')
        if name in INTENT_FLAGS:
            continue
        if name == '--user-data-dir':
            # The CLI must find backend.json where the app it starts will write it;
            # desktop_process passes the folder on to the app from here.
            os.environ['VOICESTUDIO_USER_DATA'] = value or next(items)
            continue
        kept.append(item)
    launcher_arguments[:] = kept


def split_arguments(argv):
    desktop, commands = [], []
    index = 0
    while index < len(argv):
        item = argv[index]
        name = item.split('=', 1)[0]
        if not commands and name in DESKTOP_SWITCHES:
            desktop.append(item)
        elif not commands and name in DESKTOP_VALUES:
            if item.endswith('='):
                raise CLIError('usage_error', f'{name} requires a value.', exit_code=2)
            desktop.append(item)
            if '=' not in item:
                index += 1
                if index >= len(argv) or argv[index].startswith('-'):
                    raise CLIError('usage_error', f'{name} requires a value.', exit_code=2)
                desktop.append(argv[index])
        else:
            commands.extend(argv[index:])
            break
        index += 1
    return commands, desktop


def launcher_targets(argv):
    """Files or URLs from a desktop launcher (``Exec=... %U``), not a command.

    A file dropped on the menu entry arrives as an absolute path or a URL;
    no command name has either form, so this opens the app instead of failing.
    """
    import re
    return bool(argv) and all(os.path.isabs(item) or re.match(r'[A-Za-z][A-Za-z0-9+.-]*:', item)
                              for item in argv)


def app_switches(argv, cli_options):
    """Only ``--switches`` the CLI does not define, and no command: they are for the app.

    The AppImage and .deb start this command, so Chromium, Electron and Node switches
    given to them (``--enable-features=...``, a test driver's ``--inspect=0``) arrive
    here and must reach the app, not fail as an unknown command.
    """
    return bool(argv) and all(
        item.startswith('--') and item.split('=', 1)[0] not in {'--', *cli_options} for item in argv)


def launcher_config() -> Path:
    # Separate from HTTP credentials: corrupt connection settings must not block launch.
    return config_path().with_name('desktop.json')


def _read_launcher(path):
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
        executable = data['executable']
        if not isinstance(executable, str) or not executable:
            raise ValueError('executable must be a nonempty path')
        target = Path(executable).expanduser()
        return target if target.is_absolute() else path.parent / target
    except FileNotFoundError:
        return None
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise CLIError('desktop_config_error', f'Cannot read desktop launcher {path}: {exc}') from None


def bundled_launcher():
    if getattr(sys, 'frozen', False):
        return Path(sys.executable).resolve().with_name('desktop.json')
    return None


def desktop_candidates():
    """Return locations used by supported desktop installers."""
    home = Path.home()
    if sys.platform == 'darwin':
        return [home / 'Applications/VoiceStudio.app', Path('/Applications/VoiceStudio.app')]
    if sys.platform == 'win32':
        roots = [Path(os.environ[key]) for key in ['LOCALAPPDATA', 'ProgramFiles', 'ProgramFiles(x86)']
                 if os.environ.get(key)]
        candidates = [root / sub / 'VoiceStudio.exe' for root in roots
                      for sub in ['Programs/VoiceStudio', 'VoiceStudio', 'voicestudio-electron']]
        # Respect NSIS's user-selected installation directory.
        import winreg
        for hive in [winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE]:
            for view in [winreg.KEY_WOW64_64KEY, winreg.KEY_WOW64_32KEY]:
                try:
                    with winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall',
                                        0, winreg.KEY_READ | view) as uninstall:
                        index = 0
                        while True:
                            try:
                                name = winreg.EnumKey(uninstall, index)
                            except OSError:
                                break
                            index += 1
                            try:
                                with winreg.OpenKey(uninstall, name) as entry:
                                    title = winreg.QueryValueEx(entry, 'DisplayName')[0]
                                    if title == 'VoiceStudio':
                                        location = winreg.QueryValueEx(entry, 'InstallLocation')[0]
                                        candidates.insert(0, Path(location) / 'VoiceStudio.exe')
                            except OSError:
                                pass  # an unreadable uninstall entry is skipped
                except OSError:
                    pass  # no per-user uninstall registry; other locations follow
        return candidates
    candidates = [home / '.local/bin/VoiceStudio']
    for root in [Path('/opt/VoiceStudio'), Path('/opt/voicestudio-electron')]:
        candidates.extend(root / name for name in ['voicestudio-desktop', 'voicestudio-electron'])
    if found := shutil.which('VoiceStudio'):
        candidates.append(Path(found))
    return candidates


def resolve_desktop():
    explicit = os.environ.get('VOICESTUDIO_DESKTOP_APP')
    manifest = bundled_launcher()
    selected = Path(explicit).expanduser() if explicit else (
        _read_launcher(manifest) if manifest else _read_launcher(launcher_config()))
    candidates = [selected] if selected is not None else desktop_candidates()
    own = Path(sys.executable).resolve() if getattr(sys, 'frozen', False) else None
    # Never resolve the lowercase public command through PATH: that would recurse.
    for candidate in candidates:
        if candidate is None:
            continue
        candidate = candidate.resolve()
        if candidate == own:
            raise CLIError('desktop_config_error', 'Desktop executable points back to the CLI.')
        if sys.platform == 'darwin' and candidate.suffix == '.app' and candidate.is_dir():
            return ['/usr/bin/open', '-a', str(candidate), '--args']
        if candidate.is_file() and (sys.platform == 'win32' or os.access(candidate, os.X_OK)):
            return [str(candidate)]
    raise CLIError('desktop_not_found', "Can't find the VoiceStudio app. Install VoiceStudio, or set "
                   'VOICESTUDIO_DESKTOP_APP to where the app is installed.')


def desktop_environment():
    env = dict(os.environ)
    env.pop('ELECTRON_RUN_AS_NODE', None)
    # The frozen interpreter's library path must not leak into Electron or its Python backend.
    if getattr(sys, 'frozen', False):
        env['PYINSTALLER_RESET_ENVIRONMENT'] = '1'
        for key in ['LD_LIBRARY_PATH', 'LIBPATH']:
            original = env.pop(key + '_ORIG', None)
            if original is None:
                env.pop(key, None)
            else:
                env[key] = original
        if sys.platform == 'win32':
            ctypes.windll.kernel32.SetDllDirectoryW(None)
    return env


def _direct_command(command):
    """An executable that receives arguments even when the app is already running.

    ``open -a`` drops arguments for a running app, and a command inside an
    AppImage mount dies with this process; launch the bundle binary or the
    image itself instead.
    """
    if getattr(sys, 'frozen', False) and os.environ.get('APPIMAGE'):
        return [os.environ['APPIMAGE']]
    if command[:2] == ['/usr/bin/open', '-a'] and len(command) >= 3:
        import plistlib
        bundle = Path(command[2])
        try:
            with (bundle / 'Contents/Info.plist').open('rb') as stream:
                name = plistlib.load(stream)['CFBundleExecutable']
        except (OSError, KeyError, ValueError, plistlib.InvalidFileException):
            name = bundle.stem
        return [str(bundle / 'Contents/MacOS' / name)]
    return command


def _with_profile(arguments):
    """Add the selected profile (VOICESTUDIO_USER_DATA) unless the arguments already name one."""
    arguments = list(arguments)
    user_data = os.environ.get('VOICESTUDIO_USER_DATA')
    if user_data and not any(a == '--user-data-dir' or a.startswith('--user-data-dir=') for a in arguments):
        arguments.append('--user-data-dir=' + str(Path(user_data).expanduser()))
    return arguments


def desktop_process(arguments, *, detached=False):
    """Start the desktop app with ownership switches; it may hand them to a running instance."""
    command = _direct_command(resolve_desktop())
    arguments = _with_profile(arguments)
    options = {'stdin': subprocess.DEVNULL, 'stdout': subprocess.DEVNULL, 'stderr': subprocess.DEVNULL}
    if detached:
        options.update({'creationflags': subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP,
                        'close_fds': True} if os.name == 'nt' else {'start_new_session': True})
    try:
        return subprocess.Popen([*command, *launcher_arguments, *arguments], env=desktop_environment(), **options)
    except OSError as exc:
        raise CLIError('desktop_launch_failed', f'Could not launch VoiceStudio: {exc}') from None


def launch_desktop(arguments=()):
    command = resolve_desktop()
    env = desktop_environment()
    try:
        # Wait so AppImage's mount stays alive while Electron uses its resources.
        # The same profile as background launches, so bare `voicestudio` opens that app.
        result = subprocess.run([*command, *_with_profile(arguments)], env=env)
    except OSError as exc:
        raise CLIError('desktop_launch_failed', f'Could not launch VoiceStudio: {exc}') from None
    if result.returncode:
        raise CLIError('desktop_launch_failed', f'VoiceStudio desktop exited with status {result.returncode}.')
    return 0
