#!/usr/bin/env python3
"""Pin-driven native preparation. Source blobs are checked against their pinned Git tree."""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[2]
PINS = json.loads((HERE.parent / 'pins.json').read_text())
BUILD = HERE / 'build'
HEADERS = ROOT / 'mobile/ios/VoiceStudioKit/Sources/VSNative/include/generated'


def run(*args, cwd=None, env=None):
    subprocess.run([str(a) for a in args], cwd=cwd, env=env, check=True)


def download(url, target, sha256=None):
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists() or (sha256 and digest(target) != sha256):
        part = target.with_suffix(target.suffix + '.part')
        try:
            run('curl', '--fail', '--location', '--retry', '3', '--connect-timeout', '30', '--max-time', '1800', url, '-o', part)
            if sha256 and digest(part) != sha256:
                raise RuntimeError('SHA-256 mismatch: ' + url)
            part.replace(target)
        finally:
            part.unlink(missing_ok=True)
    if sha256 and digest(target) != sha256:
        raise RuntimeError('SHA-256 mismatch: ' + str(target))
    return target


def digest(path):
    with path.open('rb') as stream:
        value = hashlib.sha256()
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            value.update(chunk)
        return value.hexdigest()


def extract_tar(archive, output):
    output = output.resolve()
    with tarfile.open(archive) as tar:
        members = []
        inert_links = []
        for member in tar.getmembers():
            destination = (output / member.name).resolve()
            if output != destination and output not in destination.parents:
                raise RuntimeError('Unsafe archive path')
            if member.isdev() or member.isfifo():
                raise RuntimeError('Unsupported archive entry')
            if member.issym() or member.islnk():
                link = (destination.parent if member.issym() else output) / member.linkname
                if output != link.resolve() and output not in link.resolve().parents:
                    inert_links.append((destination, member.linkname))
                    continue
            members.append(member)
        tar.extractall(output, members=members)
        # Upstream includes absolute symlinks in unused Go examples. Keep their Git blob
        # as inert text rather than create a link into another user's filesystem.
        for destination, value in inert_links:
            destination.parent.mkdir(parents=True, exist_ok=True)
            destination.write_text(value)


def api(path):
    headers = {'User-Agent': 'VoiceStudio-native-builder'}
    # Anonymous API calls share a 60/hour limit per IP, which shared CI runners
    # exhaust; CI passes its read-only token. Responses are cached under build/.
    token = os.environ.get('GITHUB_TOKEN')
    if token:
        headers['Authorization'] = 'Bearer ' + token
    request = urllib.request.Request('https://api.github.com/repos/' + path, headers=headers)
    with urllib.request.urlopen(request, timeout=90) as response:
        return json.load(response)


def source(repo, commit):
    slug = repo.removeprefix('https://github.com/').removesuffix('.git')
    cache = BUILD / 'sources' / (slug.replace('/', '-') + '-' + commit)
    archive = download('https://codeload.github.com/' + slug + '/tar.gz/' + commit, BUILD / 'downloads' / (slug.replace('/', '-') + '-' + commit + '.tar.gz'))
    tree_file = archive.with_suffix('.tree.json')
    if not tree_file.exists():
        tree_file.write_text(json.dumps(api(slug + '/git/trees/' + commit + '?recursive=1')))
    tree = json.loads(tree_file.read_text())
    if tree.get('truncated'):
        raise RuntimeError('Incomplete source tree for ' + slug)
    if not (cache / '.voicestudio-source.json').exists():
        if cache.exists():
            shutil.rmtree(cache)
        cache.mkdir(parents=True)
        with tarfile.open(archive) as tar:
            # GitHub embeds the requested full commit in the archive root name.
            members = tar.getmembers()
            prefix = members[0].name.split('/')[0]
            if not prefix.endswith('-' + commit):
                raise RuntimeError('Archive does not identify pinned commit')
            extract_tar(archive, cache / '.extract')
            for child in (cache / '.extract' / prefix).iterdir():
                shutil.move(str(child), cache / child.name)
            shutil.rmtree(cache / '.extract')
    submodules = []
    for entry in tree['tree']:
        path = cache / entry['path']
        if entry['type'] == 'commit':
            submodules.append(entry)
        elif entry['type'] == 'blob':
            payload = os.readlink(path).encode() if path.is_symlink() else path.read_bytes()
            actual = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
            if actual != entry['sha']:
                raise RuntimeError('Pinned source blob mismatch: ' + str(path))
    (cache / '.voicestudio-source.json').write_text(json.dumps({'commit': commit, 'archive_sha256': digest(archive), 'tree': tree['sha']}))
    return cache, submodules


def prepare_headers(sherpa, omni, llama):
    HEADERS.mkdir(parents=True, exist_ok=True)
    shutil.copy2(sherpa / 'sherpa-onnx/c-api/c-api.h', HEADERS / 'c-api.h')
    if omni:
        shutil.copy2(omni / 'src/omnivoice.h', HEADERS / 'omnivoice.h')
    if llama:
        shutil.copy2(llama / 'include/llama.h', HEADERS / 'llama.h')
        for header in (llama / 'ggml/include').glob('*.h'):
            shutil.copy2(header, HEADERS / header.name)


def macos_sherpa():
    architecture = platform.machine()
    archive_key = {'arm64': 'macos_arm64_shared', 'x86_64': 'macos_x64_shared'}.get(architecture)
    if archive_key is None:
        raise RuntimeError('Unsupported macOS sherpa architecture: ' + architecture)
    item = PINS['sherpa-onnx'][archive_key]
    archive = download(item['url'], BUILD / 'downloads' / ('sherpa-macos-' + item['sha256'] + '.tar.bz2'), item['sha256'])
    output = BUILD / 'macos' / item['sha256']
    output.mkdir(parents=True, exist_ok=True)
    extract_tar(archive, output)
    sherpa, _ = source(**{k: PINS['sherpa-onnx']['source'][k] for k in ('repo', 'commit')})
    prepare_headers(sherpa, None, None)
    library = next(output.glob('*/lib/libsherpa-onnx-c-api.dylib'))
    (BUILD / 'macos/sherpa-path.txt').write_text(str(library))
    print('SHERPA_ONNX_LIBRARY=' + str(library))
    return str(library)


def prepare_onnxruntime(sherpa):
    # The runtime version comes from the pinned sherpa source, never an environment override.
    script = (sherpa / 'build-ios.sh').read_text()
    version = re.search(r'SHERPA_ONNX_ONNXRUNTIME_VERSION:-([^}]+)', script).group(1)
    if os.environ.get('SHERPA_ONNX_ONNXRUNTIME_VERSION', version) != version:
        raise RuntimeError('ONNXRuntime override differs from the pinned sherpa dependency')
    name = 'onnxruntime-ios-static-xcframework-' + version + '.xcframework.zip'
    metadata = BUILD / 'downloads' / ('onnxruntime-' + version + '.release.json')
    if not metadata.exists():
        metadata.parent.mkdir(parents=True, exist_ok=True)
        metadata.write_text(json.dumps(api('csukuangfj/onnxruntime-libs/releases/tags/v' + version)))
    release = json.loads(metadata.read_text())
    asset = next(a for a in release['assets'] if a['name'] == name)
    checksum = asset.get('digest', '')
    if not checksum.startswith('sha256:') or len(checksum) != 71:
        raise RuntimeError('ONNXRuntime release has no SHA-256; extend pins.json before building')
    archive = download(asset['browser_download_url'], BUILD / 'downloads' / name, checksum[7:])
    target = sherpa / 'build-ios/ios-onnxruntime' / version
    if not (target / 'onnxruntime.xcframework').exists():
        target.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(target)
    link = target.parent / 'onnxruntime.xcframework'
    if not link.exists():
        link.symlink_to(target / 'onnxruntime.xcframework', target_is_directory=True)


def stamp_omni_source(verified, output):
    # Archive builds cannot derive an identity from a .git directory. Do not let
    # upstream accidentally stamp VoiceStudio's enclosing checkout commit.
    target = output / 'omnivoice-source'
    if not target.exists():
        shutil.copytree(verified, target)
    commit = PINS['omnivoice.cpp']['source']['commit']
    content = '#pragma once\n#define OMNIVOICE_VERSION "' + commit + ' (pinned)"\n'
    cmake = 'set(CONTENT [=[' + content + ']=])\nif(EXISTS "${OUTPUT}")\n  file(READ "${OUTPUT}" EXISTING)\n  if("${EXISTING}" STREQUAL "${CONTENT}")\n    return()\n  endif()\nendif()\nfile(WRITE "${OUTPUT}" "${CONTENT}")\n'
    (target / 'tools/version.cmake').write_text(cmake)
    patches = [HERE / 'patches/omnivoice-metal-transfers.patch', HERE / 'patches/omnivoice-scoped-backend.patch']
    # Fail closed and apply to the private build tree, never the verified source.
    env = {**os.environ, 'GIT_CEILING_DIRECTORIES': str(target.parent)}
    for patch in patches:
        if subprocess.run(['git', 'apply', '--reverse', '--check', str(patch)], cwd=target, env=env,
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode:
            run('git', 'apply', '--check', patch, cwd=target, env=env)
            run('git', 'apply', patch, cwd=target, env=env)
        run('git', 'apply', '--reverse', '--check', patch, cwd=target, env=env)
    return target


def framework(name, library, headers, output, platform_name):
    import plistlib
    target = output / (name + '.framework')
    (target / 'Headers').mkdir(parents=True, exist_ok=True)
    shutil.copy2(library, target / name)
    for header in headers:
        shutil.copy2(header, target / 'Headers' / header.name)
    (target / 'Modules').mkdir(exist_ok=True)
    (target / 'Modules/module.modulemap').write_text('framework module ' + name + ' { umbrella "../Headers" export * }\n')
    (target / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleIdentifier': 'com.voicestudio.native.' + name, 'CFBundleName': name, 'CFBundleExecutable': name, 'CFBundlePackageType': 'FMWK', 'CFBundleShortVersionString': '1.0', 'CFBundleVersion': '1', 'MinimumOSVersion': '17.0', 'CFBundleSupportedPlatforms': [platform_name]}))
    run('install_name_tool', '-id', '@rpath/' + name + '.framework/' + name, target / name)
    return target


def notices(output):
    source = ROOT / 'mobile/ios/VoiceStudio/Resources/NativeLicences'
    entries = json.loads((source / 'sources.json').read_text())
    for entry in entries:
        if digest(source / (entry['name'] + '.txt')) != entry['notice_sha256']:
            raise RuntimeError('Native notice changed: ' + entry['name'])
        if entry['name'] in PINS:
            pin = PINS[entry['name']]['source']
            if entry['source'] != pin['repo'] + '/tree/' + pin['commit']:
                raise RuntimeError('Native notice pin drift: ' + entry['name'])
    shutil.copytree(source, output / 'Notices', dirs_exist_ok=True)


def onnxruntime_notice(sherpa):
    version = re.search(r'SHERPA_ONNX_ONNXRUNTIME_VERSION:-([^}]+)', (sherpa / 'build-ios.sh').read_text()).group(1)
    cache = BUILD / 'downloads' / ('onnxruntime-' + version + '-LICENSE.json')
    if not cache.exists():
        cache.write_text(json.dumps(api('microsoft/onnxruntime/contents/LICENSE?ref=v' + version)))
    entry = json.loads(cache.read_text())
    payload = base64.b64decode(entry['content'])
    blob = hashlib.sha1(b'blob ' + str(len(payload)).encode() + b'\0' + payload).hexdigest()
    if blob != entry['sha']:
        raise RuntimeError('ONNXRuntime licence blob mismatch')
    text = cache.with_suffix('.txt')
    text.write_bytes(payload)
    return text


def macos_runtimes():
    paths = {'sherpa-onnx': macos_sherpa()}
    omni, modules = source(**{k: PINS['omnivoice.cpp']['source'][k] for k in ('repo', 'commit')})
    ggml, _ = source('https://github.com/ServeurpersoCom/ggml', next(e['sha'] for e in modules if e['path'] == 'ggml'))
    if not (omni / 'ggml/CMakeLists.txt').exists():
        shutil.copytree(ggml, omni / 'ggml', dirs_exist_ok=True)
    llama, _ = source(**{k: PINS['llama.cpp']['source'][k] for k in ('repo', 'commit')})
    sherpa, _ = source(**{k: PINS['sherpa-onnx']['source'][k] for k in ('repo', 'commit')})
    prepare_headers(sherpa, omni, llama)
    for name, directory, target in [('omnivoice', omni, 'omnivoice'), ('llama', llama, 'llama')]:
        commit = PINS['omnivoice.cpp' if name == 'omnivoice' else 'llama.cpp']['source']['commit']
        build = BUILD / 'macos' / (name + '-' + commit + '-' + platform.machine() + '-archive')
        if name == 'omnivoice':
            build.mkdir(parents=True, exist_ok=True)
            directory = stamp_omni_source(directory, build)
        run('cmake', '-S', directory, '-B', build, '-DCMAKE_BUILD_TYPE=Release', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DBUILD_SHARED_LIBS=ON' if name == 'llama' else '-DBUILD_SHARED_LIBS=OFF', '-DOMNIVOICE_SHARED=ON', '-DGGML_BACKEND_DL=OFF', '-DGGML_NATIVE=OFF', '-DGGML_METAL=OFF', '-DGGML_OPENMP=OFF', '-DGGML_BUILD_TESTS=OFF', '-DGGML_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_TESTS=OFF', '-DLLAMA_BUILD_EXAMPLES=OFF', '-DLLAMA_BUILD_TOOLS=OFF', '-DLLAMA_CURL=OFF')
        run('cmake', '--build', build, '--target', target, '--parallel', '4')
        library = next(build.rglob('lib' + name + '.dylib'))
        print(name.upper() + '_LIBRARY=' + str(library), flush=True)
        paths['omnivoice.cpp' if name == 'omnivoice' else 'llama.cpp'] = str(library)
    (BUILD / 'macos/runtime-paths.json').write_text(json.dumps(paths, indent=2))


def ios():
    # Fail before downloading if the SDK is absent.
    run('xcrun', '--sdk', 'iphoneos', '--show-sdk-path')
    toolchain = subprocess.check_output(['xcodebuild', '-version'], text=True) + subprocess.check_output(['xcrun', '--sdk', 'iphoneos', '--show-sdk-version'], text=True)
    pin_key = hashlib.sha256((HERE.parent / 'pins.json').read_bytes() + (HERE / 'build.py').read_bytes() + (HERE / 'patches/omnivoice-metal-transfers.patch').read_bytes() + (HERE / 'patches/omnivoice-scoped-backend.patch').read_bytes() + (ROOT / 'mobile/ios/VoiceStudio/Resources/NativeLicences/sources.json').read_bytes() + toolchain.encode()).hexdigest()
    output = BUILD / 'ios' / pin_key
    stable = BUILD / 'xcframeworks'
    sherpa, _ = source(**{k: PINS['sherpa-onnx']['source'][k] for k in ('repo', 'commit')})
    omni, modules = source(**{k: PINS['omnivoice.cpp']['source'][k] for k in ('repo', 'commit')})
    ggml_pin = next(e['sha'] for e in modules if e['path'] == 'ggml')
    ggml, nested = source('https://github.com/ServeurpersoCom/ggml', ggml_pin)
    if nested:
        raise RuntimeError('New nested submodule requires review')
    if not (omni / 'ggml/CMakeLists.txt').exists():
        shutil.copytree(ggml, omni / 'ggml', dirs_exist_ok=True)
    llama, _ = source(**{k: PINS['llama.cpp']['source'][k] for k in ('repo', 'commit')})
    prepare_headers(sherpa, omni, llama)
    if not (output / 'complete.json').exists():
        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)
        # Verify the dependency before invoking upstream, so its unverified wget path is skipped.
        prepare_onnxruntime(sherpa)
        run('bash', 'build-ios.sh', cwd=sherpa)
        omni = stamp_omni_source(omni, output)
        shutil.copytree(sherpa / 'build-ios/sherpa-onnx.xcframework', output / 'sherpa-onnx.xcframework', dirs_exist_ok=True)
        shutil.copytree(sherpa / 'build-ios/ios-onnxruntime/onnxruntime.xcframework', output / 'onnxruntime.xcframework', dirs_exist_ok=True)
        llama_zip = PINS['llama.cpp']['ios_xcframework']
        archive = download(llama_zip['url'], BUILD / 'downloads' / ('llama-' + llama_zip['sha256'] + '.zip'), llama_zip['sha256'])
        unpack = output / 'llama-unpacked'; unpack.mkdir(exist_ok=True)
        with zipfile.ZipFile(archive) as zipped:
            zipped.extractall(unpack)
        xc = next(unpack.rglob('llama.xcframework'))
        (xc / 'ios-arm64/llama.framework/llama').chmod(0o755)
        # The pinned prebuilt contains device + macOS, but no simulator slice.
        # Build only the missing simulator at the same source commit, then combine.
        run('bash', 'build-xcframework.sh', 'ios-sim', cwd=llama)
        simulator = next((llama / 'build-apple/llama.xcframework').glob('ios-*-simulator/llama.framework'))
        run('xcodebuild', '-create-xcframework', '-framework', xc / 'ios-arm64/llama.framework', '-framework', simulator, '-output', output / 'llama.xcframework')
        # Keep OmniVoice's distinct GGML ABI inside its own dynamic framework; export only ov_*.
        export_file = output / 'omnivoice.exports'; export_file.write_text('_ov_*\n')
        frameworks = []
        for sdk, arch, platform_name in [('iphoneos', 'arm64', 'iPhoneOS'), ('iphonesimulator', 'arm64;x86_64', 'iPhoneSimulator')]:
            build = output / ('omni-' + sdk)
            run('cmake', '-S', omni, '-B', build, '-G', 'Xcode', '-DCMAKE_SYSTEM_NAME=iOS', '-DCMAKE_OSX_SYSROOT=' + sdk, '-DCMAKE_OSX_ARCHITECTURES=' + arch, '-DCMAKE_OSX_DEPLOYMENT_TARGET=17.0', '-DCMAKE_POLICY_VERSION_MINIMUM=3.5', '-DCMAKE_XCODE_ATTRIBUTE_CODE_SIGNING_ALLOWED=NO', '-DCMAKE_CXX_VISIBILITY_PRESET=hidden', '-DCMAKE_C_VISIBILITY_PRESET=hidden', '-DCMAKE_VISIBILITY_INLINES_HIDDEN=ON', '-DOMNIVOICE_SHARED=ON', '-DBUILD_SHARED_LIBS=OFF', '-DGGML_BACKEND_DL=OFF', '-DGGML_NATIVE=OFF', '-DGGML_METAL=ON', '-DGGML_METAL_EMBED_LIBRARY=ON', '-DGGML_OPENMP=OFF', '-DGGML_BUILD_TESTS=OFF', '-DGGML_BUILD_EXAMPLES=OFF', '-DCMAKE_SHARED_LINKER_FLAGS=-Wl,-exported_symbols_list,' + str(export_file))
            run('cmake', '--build', build, '--config', 'Release', '--target', 'omnivoice', '--parallel', '4')
            library = next(build.rglob('libomnivoice.dylib'))
            frameworks.append(framework('OmniVoice', library, [omni / 'src/omnivoice.h'], output / sdk, platform_name))
        run('xcodebuild', '-create-xcframework', '-framework', frameworks[0], '-framework', frameworks[1], '-output', output / 'OmniVoice.xcframework')
        notices(output)
        receipt = {'toolchain': toolchain, 'pins_sha256': digest(HERE.parent / 'pins.json'), 'build_script_sha256': digest(HERE / 'build.py'), 'patches': {p.name: digest(p) for p in sorted((HERE / 'patches').glob('*.patch'))}, 'artifacts': {str(p.relative_to(output)): digest(p) for p in output.rglob('*') if p.is_file()}}
        (output / 'complete.json').write_text(json.dumps(receipt, indent=2))
    receipt = json.loads((output / 'complete.json').read_text())
    for path, expected in receipt['artifacts'].items():
        if digest(output / path) != expected:
            raise RuntimeError('Cached build was changed: ' + path)
    if stable.is_symlink():
        stable.unlink()
    elif stable.exists():
        raise RuntimeError('Refusing to replace a non-symlink: ' + str(stable))
    stable.symlink_to(output, target_is_directory=True)
    print(stable)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('action', choices=['ios', 'macos-sherpa', 'macos-runtimes', 'sources'])
    args = parser.parse_args()
    BUILD.mkdir(parents=True, exist_ok=True)
    if args.action == 'macos-sherpa':
        macos_sherpa()
    elif args.action == 'macos-runtimes':
        macos_runtimes()
    elif args.action == 'sources':
        for runtime in PINS.values():
            if isinstance(runtime, dict) and 'source' in runtime:
                item = runtime['source']; print(source(item['repo'], item['commit'])[0])
    else:
        ios()
