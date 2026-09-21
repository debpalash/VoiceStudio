import { downloadProxyEnv } from './proxy-env';
import { downloadRuntimeInstaller } from './runtime-download';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import {
  cp,
  mkdir,
  readFile,
  readdir,
  rename,
  rm,
  stat,
  statfs,
  writeFile,
} from 'node:fs/promises';
import { join } from 'node:path';

const SOURCES = ['backend', 'omnivoice', 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'];
export const UV_VERSION = '0.12.13'; // Kept in sync with the Tauri tools contract.
export const CUDNN8_COMPAT_PIN = 'nvidia-cudnn-cu12==8.9.7.29';
const RUNTIME_SCHEMA = 'electron-runtime-v2-cudnn8';
const CUDNN8_PROBE_PREFIX = 'VOICESTUDIO_CUDNN8_PROBE=';
const REQUIRED_ENV_BYTES = 9 * 1024 ** 3; // Tauri setup.rs: REQUIRED_ENV_BYTES.
export type RuntimePhase = 'checking' | 'downloading_uv' | 'installing_deps' | 'verifying';
export type RuntimeRegion = 'auto' | 'global' | 'china' | 'russia' | 'restricted';
export type RuntimeRunner = (
  command: string,
  args: string[],
  cwd: string,
  env?: NodeJS.ProcessEnv,
) => Promise<string | void>;

const PYTHON_DOWNLOAD_MIRROR =
  'https://gh-proxy.com/https://github.com/astral-sh/python-build-standalone/releases/download';

async function probeLatency(url: string, signal: AbortSignal): Promise<number | null> {
  const started = performance.now();
  try {
    const response = await fetch(url, {
      method: 'HEAD',
      redirect: 'follow',
      signal: AbortSignal.any([signal, AbortSignal.timeout(4_000)]),
    });
    return response.ok ? performance.now() - started : null;
  } catch {
    return null;
  }
}

async function effectiveRegion(region: RuntimeRegion, signal: AbortSignal): Promise<RuntimeRegion> {
  if (region !== 'auto') return region;
  const [direct, mirror] = await Promise.all([
    probeLatency('https://github.com', signal),
    probeLatency('https://ghproxy.net/https://github.com', signal),
  ]);
  if (direct !== null && (mirror === null || mirror * 5 > direct * 4)) return 'global';
  return 'restricted';
}

async function runtimeDownloadEnv(
  region: RuntimeRegion,
  signal: AbortSignal,
): Promise<NodeJS.ProcessEnv> {
  const effective = await effectiveRegion(region, signal);
  return {
    UV_HTTP_TIMEOUT: process.env.UV_HTTP_TIMEOUT || '120',
    UV_HTTP_CONNECT_TIMEOUT: process.env.UV_HTTP_CONNECT_TIMEOUT || '30',
    UV_HTTP_RETRIES: process.env.UV_HTTP_RETRIES || '5',
    ...(effective === 'china' && !process.env.UV_INDEX_URL
      ? { UV_INDEX_URL: 'https://mirrors.aliyun.com/pypi/simple/' }
      : {}),
    ...(['china', 'russia', 'restricted'].includes(effective) &&
    !process.env.UV_PYTHON_INSTALL_MIRROR
      ? { UV_PYTHON_INSTALL_MIRROR: PYTHON_DOWNLOAD_MIRROR }
      : {}),
  };
}

export function runtimePython(root: string, platform = process.platform): string {
  return platform === 'win32'
    ? join(root, '.venv', 'Scripts', 'python.exe')
    : join(root, '.venv', 'bin', 'python');
}

async function dependencyStamp(bundle: string): Promise<string> {
  const hash = createHash('sha256');
  hash.update(RUNTIME_SCHEMA);
  for (const file of ['pyproject.toml', 'uv.lock']) hash.update(await readFile(join(bundle, file)));
  return hash.digest('hex');
}

interface Cudnn8Probe {
  device: 'cuda' | 'hip' | 'none' | 'unknown';
  sitePackages: string;
}

function parseCudnn8Probe(output: string | void): Cudnn8Probe | null {
  if (!output) return null;
  const line = output
    .split(/\r?\n/)
    .findLast((candidate) => candidate.startsWith(CUDNN8_PROBE_PREFIX));
  if (!line) return null;
  try {
    const parsed = JSON.parse(line.slice(CUDNN8_PROBE_PREFIX.length)) as Partial<Cudnn8Probe>;
    return ['cuda', 'hip', 'none', 'unknown'].includes(parsed.device ?? '') &&
      typeof parsed.sitePackages === 'string' &&
      parsed.sitePackages.length > 0
      ? (parsed as Cudnn8Probe)
      : null;
  } catch {
    return null;
  }
}

async function hasCudnn8Libraries(compatDir: string): Promise<boolean> {
  const libDir = join(compatDir, 'nvidia', 'cudnn', process.platform === 'win32' ? 'bin' : 'lib');
  const names = await readdir(libDir).catch(() => [] as string[]);
  return (
    names.filter((name) =>
      process.platform === 'win32'
        ? name.startsWith('cudnn') && name.endsWith('64_8.dll')
        : name.startsWith('libcudnn') && name.endsWith('.so.8'),
    ).length >= 5
  );
}

function readElfUint16(buffer: Buffer, offset: number, littleEndian: boolean): number {
  return littleEndian ? buffer.readUInt16LE(offset) : buffer.readUInt16BE(offset);
}

function readElfUint32(buffer: Buffer, offset: number, littleEndian: boolean): number {
  return littleEndian ? buffer.readUInt32LE(offset) : buffer.readUInt32BE(offset);
}

function writeElfUint32(
  buffer: Buffer,
  value: number,
  offset: number,
  littleEndian: boolean,
): void {
  if (littleEndian) buffer.writeUInt32LE(value, offset);
  else buffer.writeUInt32BE(value, offset);
}

/** Clear an obsolete executable-stack request in CTranslate2's Linux wheel. */
export async function clearCtranslate2ExecutableStack(
  sitePackages: string,
  platform = process.platform,
): Promise<number> {
  if (platform !== 'linux') return 0;
  const libraryDir = join(sitePackages, 'ctranslate2.libs');
  const names = await readdir(libraryDir).catch(() => [] as string[]);
  let patched = 0;
  for (const name of names.filter(
    (candidate) => candidate.startsWith('libctranslate2') && candidate.includes('.so'),
  )) {
    const path = join(libraryDir, name);
    const buffer = await readFile(path);
    if (
      buffer.length < 64 ||
      buffer[0] !== 0x7f ||
      buffer[1] !== 0x45 ||
      buffer[2] !== 0x4c ||
      buffer[3] !== 0x46
    )
      continue;
    const elfClass = buffer[4];
    const littleEndian = buffer[5] === 1;
    if ((elfClass !== 1 && elfClass !== 2) || (!littleEndian && buffer[5] !== 2)) continue;
    const programOffset =
      elfClass === 2
        ? Number(littleEndian ? buffer.readBigUInt64LE(32) : buffer.readBigUInt64BE(32))
        : readElfUint32(buffer, 28, littleEndian);
    const entrySize = readElfUint16(buffer, elfClass === 2 ? 54 : 42, littleEndian);
    const entryCount = readElfUint16(buffer, elfClass === 2 ? 56 : 44, littleEndian);
    let changed = false;
    for (let index = 0; index < entryCount; index += 1) {
      const entry = programOffset + index * entrySize;
      if (entry + entrySize > buffer.length) break;
      if (readElfUint32(buffer, entry, littleEndian) !== 0x6474e551) continue;
      const flagsOffset = entry + (elfClass === 2 ? 4 : 24);
      const flags = readElfUint32(buffer, flagsOffset, littleEndian);
      if ((flags & 1) !== 0) {
        writeElfUint32(buffer, flags & ~1, flagsOffset, littleEndian);
        changed = true;
      }
    }
    if (changed) {
      await writeFile(path, buffer);
      patched += 1;
    }
  }
  return patched;
}

async function ensureCudnn8Compat(
  uv: string,
  project: string,
  run: RuntimeRunner,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal,
): Promise<void> {
  if (process.platform === 'darwin') return;
  const python = runtimePython(project);
  const script = [
    'import json, sysconfig',
    "device = 'unknown'",
    'try:',
    '    import torch',
    "    device = 'hip' if getattr(torch.version, 'hip', None) else ('cuda' if torch.cuda.is_available() else 'none')",
    'except Exception:',
    '    pass',
    `print('${CUDNN8_PROBE_PREFIX}' + json.dumps({'device': device, 'sitePackages': sysconfig.get_paths()['purelib']}))`,
  ].join('\n');
  const probe = parseCudnn8Probe(await run(python, ['-c', script], project, env));
  signal.throwIfAborted();
  if (!probe) return;
  await clearCtranslate2ExecutableStack(probe.sitePackages);
  signal.throwIfAborted();
  if (probe.device !== 'cuda') return;
  const compatDir = join(probe.sitePackages, 'cudnn8_compat');
  if (await hasCudnn8Libraries(compatDir)) return;
  await run(
    uv,
    ['pip', 'install', '--no-deps', '--target', compatDir, '--python', python, CUDNN8_COMPAT_PIN],
    project,
    env,
  );
  signal.throwIfAborted();
  if (!(await hasCudnn8Libraries(compatDir))) {
    throw new Error('CUDA transcription compatibility libraries did not install completely.');
  }
}

async function runtimeIncomplete(project: string): Promise<boolean> {
  try {
    await stat(join(project, '.runtime-installing'));
    return true;
  } catch (error) {
    return (error as NodeJS.ErrnoException).code !== 'ENOENT';
  }
}

/** True only when a prior explicit runtime install left its durable marker behind. */
export async function runtimeInstallInterrupted(project: string): Promise<boolean> {
  try {
    return (await stat(join(project, '.runtime-installing'))).isFile();
  } catch {
    return false;
  }
}

/** Check the selected interpreter locally before trusting runtime metadata. */
export async function runtimeDependenciesReady(project: string): Promise<boolean> {
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  env.HF_HUB_OFFLINE = '1';
  env.TRANSFORMERS_OFFLINE = '1';
  env.PYTHONNOUSERSITE = '1';
  return new Promise((resolve) => {
    execFile(
      runtimePython(project),
      ['-c', 'import fastapi, uvicorn, omnivoice, faster_whisper, sentencepiece'],
      {
        cwd: project,
        windowsHide: true,
        timeout: 30_000,
        maxBuffer: 256 * 1024,
        env,
      },
      (error) => resolve(!error),
    );
  });
}

export async function runtimeReady(bundle: string, project: string): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  try {
    return (
      (await stat(runtimePython(project))).isFile() &&
      (await stat(join(project, '.venv', 'pyvenv.cfg'))).isFile() &&
      (await readFile(join(project, '.runtime-ready'), 'utf8')) === (await dependencyStamp(bundle))
    );
  } catch {
    return false;
  }
}

/**
 * A Tauri-managed environment can be reused without downloading anything when
 * its interpreter is structurally intact and its dependency manifests exactly
 * match this Electron bundle. The Electron marker is intentionally optional:
 * Tauri predates it and owns the same frozen Python graph.
 */
export async function runtimeCompatible(bundle: string, project: string): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  try {
    const [python, config, bundledProject, installedProject, bundledLock, installedLock, marker] =
      await Promise.all([
        stat(runtimePython(project)),
        stat(join(project, '.venv', 'pyvenv.cfg')),
        readFile(join(bundle, 'pyproject.toml')),
        readFile(join(project, 'pyproject.toml')),
        readFile(join(bundle, 'uv.lock')),
        readFile(join(project, 'uv.lock')),
        readFile(join(project, '.runtime-ready'), 'utf8').catch((error: NodeJS.ErrnoException) => {
          if (error.code === 'ENOENT') return null;
          throw error;
        }),
      ]);
    return (
      python.isFile() &&
      config.isFile() &&
      bundledProject.equals(installedProject) &&
      bundledLock.equals(installedLock) &&
      (marker === null || marker === (await dependencyStamp(bundle)))
    );
  } catch {
    return false;
  }
}

/** Replace only bundled code. The interpreter, models and user data are never removed. */
export async function stageRuntimeSources(bundle: string, project: string): Promise<void> {
  await mkdir(project, { recursive: true });
  for (const name of SOURCES) {
    const target = join(project, name);
    const pending = join(project, `.incoming-${name}`);
    const previous = join(project, `.previous-${name}`);
    await rm(pending, { recursive: true, force: true });
    await cp(join(bundle, name), pending, {
      recursive: true,
      filter: (source) => !source.split(/[\\/]/).includes('__pycache__'),
    });
    await rm(previous, { recursive: true, force: true });
    let moved = false;
    try {
      await rename(target, previous);
      moved = true;
    } catch (error) {
      if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
    }
    try {
      await rename(pending, target);
    } catch (error) {
      if (moved) await rename(previous, target);
      throw error;
    }
    await rm(previous, { recursive: true, force: true });
  }
}

/** Called only by the user's install action; normal startup never downloads. */
export async function installRuntime(
  bundle: string,
  project: string,
  uv: string | null,
  run: RuntimeRunner,
  signal: AbortSignal,
  phase: (value: RuntimePhase) => void = () => {},
  region: RuntimeRegion = 'auto',
): Promise<void> {
  signal.throwIfAborted();
  phase('checking');
  await mkdir(project, { recursive: true });
  const disk = await statfs(project);
  if (disk.bavail * disk.bsize < REQUIRED_ENV_BYTES) {
    throw Object.assign(new Error('Runtime setup needs at least 9 GiB of free disk space.'), {
      code: 'ENOSPC',
    });
  }
  const probe = join(project, `.write-probe-${randomUUID()}`);
  try {
    await writeFile(probe, '', { flag: 'wx' });
  } finally {
    await rm(probe, { force: true });
  }
  signal.throwIfAborted();
  await writeFile(join(project, '.runtime-installing'), '');
  await rm(join(project, '.runtime-ready'), { force: true });
  await stageRuntimeSources(bundle, project);
  await promoteLegacyRuntimeCaches(project);
  const tools = join(project, '.tools');
  const privateUv = join(tools, process.platform === 'win32' ? 'uv.exe' : 'uv');
  if (
    !uv &&
    (await stat(privateUv).then(
      (info) => info.isFile(),
      () => false,
    ))
  )
    uv = privateUv;
  const env: NodeJS.ProcessEnv = {
    ...downloadProxyEnv(),
    ...(await runtimeDownloadEnv(region, signal)),
    UV_PROJECT_ENVIRONMENT: join(project, '.venv'),
    // Keep immutable downloads beside the replaceable project. Clean & Retry can
    // rebuild a broken venv without paying the multi-gigabyte transfer twice.
    UV_CACHE_DIR: join(project, '..', '.uv-cache'),
    UV_PYTHON_INSTALL_DIR: join(project, '..', '.python'),
  };
  if (!uv) {
    phase('downloading_uv');
    await mkdir(tools, { recursive: true });
    const windows = process.platform === 'win32';
    const script = join(tools, windows ? 'install.ps1' : 'install.sh');
    const installer = await downloadRuntimeInstaller(
      `https://astral.sh/uv/${UV_VERSION}/install.${windows ? 'ps1' : 'sh'}`,
      env,
      signal,
    );
    await writeFile(script, installer);
    signal.throwIfAborted();
    await run(
      windows ? 'powershell.exe' : 'sh',
      windows
        ? ['-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-File', script]
        : [script],
      project,
      { ...env, UV_UNMANAGED_INSTALL: tools, UV_NO_MODIFY_PATH: '1' },
    );
    uv = join(tools, windows ? 'uv.exe' : 'uv');
  }
  signal.throwIfAborted();
  // New environments must not borrow another application's Python from PATH.
  // Existing compatible environments are kept; Clean & Retry rebuilds explicitly.
  phase('installing_deps');
  const interpreterExists = await stat(runtimePython(project)).then(
    (info) => info.isFile(),
    () => false,
  );
  let existingPython = false;
  if (interpreterExists) {
    try {
      await run(
        runtimePython(project),
        ['-c', 'import sys, sentencepiece; assert sys.version_info[:2] == (3, 11)'],
        project,
      );
      existingPython = true;
    } catch {
      // Retry must not keep a wrong-base or native-crashing interpreter simply
      // because its executable survived interrupted setup. uv selects the managed
      // replacement; immutable downloads and user data remain outside the venv.
      signal.throwIfAborted();
    }
  }
  const pythonArgs = existingPython
    ? ['--python', runtimePython(project)]
    : ['--managed-python', '--python', '3.11'];
  // A failed native import may leave distribution metadata intact, so uv's
  // ordinary sync would otherwise consider the broken wheel already satisfied.
  const repairArgs =
    interpreterExists && !existingPython ? ['--reinstall-package', 'sentencepiece'] : [];
  signal.throwIfAborted();
  if (repairArgs.length) {
    // uv may hardlink installed files to its unpacked wheel cache. A corrupted
    // native file can therefore poison the cached copy too; evict only this
    // package from the app-private cache before reinstalling its locked wheel.
    await run(uv, ['cache', 'clean', 'sentencepiece'], project, env);
    signal.throwIfAborted();
  }
  await run(uv, ['sync', '--frozen', '--no-dev', ...pythonArgs, ...repairArgs], project, env);
  signal.throwIfAborted();
  await ensureCudnn8Compat(uv, project, run, env, signal);
  signal.throwIfAborted();
  phase('verifying');
  await run(
    runtimePython(project),
    ['-c', 'import fastapi, uvicorn, omnivoice, faster_whisper, sentencepiece'],
    project,
  );
  signal.throwIfAborted();
  await writeFile(join(project, '.runtime-ready'), await dependencyStamp(bundle));
  await rm(join(project, '.runtime-installing'), { force: true });
}

/** Move caches created by Electron runtime v1 outside the replaceable project. */
export async function promoteLegacyRuntimeCaches(project: string): Promise<void> {
  for (const name of ['.uv-cache', '.python']) {
    const legacy = join(project, name);
    const shared = join(project, '..', name);
    const [legacyInfo, sharedInfo] = await Promise.all([
      stat(legacy).catch(() => null),
      stat(shared).catch(() => null),
    ]);
    if (!legacyInfo?.isDirectory() || sharedInfo) continue;
    await rename(legacy, shared);
  }
}
