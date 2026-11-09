#!/usr/bin/env python3
"""Static dependency/export check without executing an Android binary."""
import pathlib
import subprocess
import sys

root = pathlib.Path(__file__).resolve().parent
ndk = pathlib.Path(sys.argv[1])
prebuilt = next((ndk / 'toolchains/llvm/prebuilt').iterdir())
readelf = prebuilt / 'bin/llvm-readelf'
for abi in ('arm64-v8a', 'x86_64'):
    directory = root / 'build/jniLibs' / abi
    for runtime, exports in {
        'omni': ('omniAbi', 'cloneAudio', 'designAudio'),
        'llama': ('llamaDevices', 'translateText'),
        'mp3': ('create', 'write', 'finish', 'close'),
    }.items():
        library = directory / ('libvs_' + runtime + '.so')
        symbols = subprocess.check_output([str(readelf), '--dyn-syms', '--wide', str(library)], text=True)
        for name in exports:
            expected = 'Java_com_voicestudio_mobile_local_' + ('NativeMp3Bridge_' if runtime == 'mp3' else 'NativeBridge_') + name
            if expected not in symbols:
                raise ValueError('Missing JNI export: ' + expected)
        if any('ggml_' in line and ' UND ' not in line for line in symbols.splitlines()):
            raise ValueError('GGML symbols must remain isolated: ' + str(library))
        dependencies = subprocess.check_output([str(readelf), '-d', str(library)], text=True)
        for line in dependencies.splitlines():
            if '(NEEDED)' in line:
                name = line.split('[')[1].split(']')[0]
                if name not in ('libandroid.so', 'liblog.so', 'libm.so', 'libdl.so', 'libc.so', 'libc++_shared.so'):
                    raise ValueError('Unpackaged native dependency: ' + name)
        headers = subprocess.check_output([str(readelf), '-lW', str(library)], text=True)
        for line in headers.splitlines():
            if line.lstrip().startswith('LOAD ') and int(line.split()[-1], 16) < 16384:
                raise ValueError('ELF load segment needs 16 KB alignment: ' + str(library))
    if not (directory / 'libc++_shared.so').exists():
        raise ValueError('Missing C++ runtime for ' + abi)
print('JNI exports, isolated GGML, native dependencies and 16 KB alignment verified for both ABIs')
