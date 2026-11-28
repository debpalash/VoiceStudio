"""Build the standalone CLI and carry interpreter/bootloader notices with it."""
from importlib import metadata
from pathlib import Path
import shutil
import struct
import sys
import sysconfig


def clear_linux_runtime_execstack(library):
    # The bundled standalone CPython can request an executable stack. Newer
    # glibc refuses to dlopen it, even though CPython works with a normal stack.
    # The supported Linux bundle architectures use little-endian ELF64.
    with library.open('r+b') as binary:
        header = binary.read(64)
        if header[:6] != b'\x7fELF\x02\x01':
            raise RuntimeError(f'Expected a little-endian ELF64 Python runtime: {library}')
        table_offset = struct.unpack_from('<Q', header, 32)[0]
        entry_size, entry_count = struct.unpack_from('<HH', header, 54)
        for index in range(entry_count):
            offset = table_offset + index * entry_size
            binary.seek(offset)
            segment_type, flags = struct.unpack('<II', binary.read(8))
            if segment_type == 0x6474e551:  # PT_GNU_STACK
                if flags & 1:  # PF_X
                    binary.seek(offset + 4)
                    binary.write(struct.pack('<I', flags & ~1))
                return
        raise RuntimeError(f'Python runtime is missing its GNU_STACK header: {library}')


def main():
    from PyInstaller.__main__ import run

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
    if sys.platform == 'linux':
        distpath = Path(arguments[arguments.index('--distpath') + 1])
        runtime = f'libpython{sys.version_info.major}.{sys.version_info.minor}.so.1.0'
        clear_linux_runtime_execstack(distpath / 'voicestudio' / '_internal' / runtime)


if __name__ == '__main__':
    main()
