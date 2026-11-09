"""Build the standalone CLI and carry interpreter/bootloader notices with it."""
from importlib import metadata
from pathlib import Path
import shutil
import sys
import sysconfig

from PyInstaller.__main__ import run


def main():
    arguments = sys.argv[1:]
    work = Path(arguments[arguments.index('--specpath') + 1])
    notices = work / 'notices'
    notices.mkdir()
    candidates = [Path(sysconfig.get_path('stdlib')) / 'LICENSE.txt', Path(sys.base_prefix) / 'LICENSE.txt']
    license_file = next((path for path in candidates if path.is_file()), None)
    if license_file is None:
        raise RuntimeError('Python runtime license is missing; refusing to bundle it without its notice.')
    shutil.copy2(license_file, notices / 'Python-LICENSE.txt')
    dist = metadata.distribution('pyinstaller')
    license_files = [file for file in dist.files or [] if '.dist-info/licenses/' in str(file).replace('\\', '/')]
    if not license_files:
        raise RuntimeError('PyInstaller bootloader license is missing.')
    for file in license_files:
        target = notices / 'PyInstaller' / Path(str(file).split('licenses/', 1)[-1])
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(dist.locate_file(file), target)
    run([*arguments, '--add-data', f'{notices}:licenses'])


if __name__ == '__main__':
    main()
