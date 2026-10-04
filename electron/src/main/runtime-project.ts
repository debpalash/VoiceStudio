import { downloadProxyEnv } from './proxy-env';
import { downloadRuntimeArchive, downloadRuntimeInstaller } from './runtime-download';
import { asciiSafePthFiles } from './pth-ascii';
import windowsRocmRecipe from '../../../scripts/windows-rocm-recipe.json';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { existsSync } from 'node:fs';
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
import { join, posix } from 'node:path';

const SOURCES = [
  'backend',
  'frontend',
  'omnivoice',
  'pyproject.toml',
  'uv.lock',
  'README.md',
  'LICENSE',
];
export const UV_VERSION = '0.12.13';
export const CUDNN8_COMPAT_PIN = 'nvidia-cudnn-cu12==8.9.7.29';
export const ROCM_TORCH_INDEX = 'https://download.pytorch.org/whl/rocm6.4';
export const ROCM_TORCH_PINS = [
  'torch==2.8.0',
  'torchaudio==2.8.0',
  'torchvision==0.23.0',
] as const;
export const WINDOWS_ROCM_FIND_LINKS = windowsRocmRecipe.torch.find_links;
export const WINDOWS_ROCM_TORCH_PINS: readonly string[] = windowsRocmRecipe.torch.packages;
export const WINDOWS_ROCM_CT2_ARCHIVE = windowsRocmRecipe.ctranslate2.archive_url;
export const WINDOWS_ROCM_CT2_ARCHIVE_SHA256 = windowsRocmRecipe.ctranslate2.archive_sha256;
export const WINDOWS_ROCM_CT2_WHEEL = posix.basename(windowsRocmRecipe.ctranslate2.wheel_member);
export const WINDOWS_ROCM_CT2_WHEEL_SHA256 = windowsRocmRecipe.ctranslate2.wheel_sha256;
const WINDOWS_ROCM_CT2_EXTRACT = [
  'import hashlib, pathlib, sys, zipfile',
  'with zipfile.ZipFile(sys.argv[1]) as package:',
  '    if package.namelist().count(sys.argv[2]) != 1:',
  '        raise RuntimeError("Expected ROCm wheel missing or duplicated")',
  '    wheel = package.read(sys.argv[2])',
  'if hashlib.sha256(wheel).hexdigest() != sys.argv[3]:',
  '    raise RuntimeError("Invalid ROCm wheel checksum")',
  'pathlib.Path(sys.argv[4]).write_bytes(wheel)',
].join('\n');
export const WINDOWS_ROCM_CT2_PROBE = [
  'import importlib.util, mmap, os',
  'from contextlib import ExitStack',
  'from pathlib import Path',
  'ct2_spec = importlib.util.find_spec("ctranslate2")',
  'if ct2_spec is None or not ct2_spec.submodule_search_locations:',
  '    raise RuntimeError("CTranslate2 package is missing")',
  'ct2_dir = Path(next(iter(ct2_spec.submodule_search_locations)))',
  'library = ct2_dir / "ctranslate2.dll"',
  'if not library.is_file():',
  '    raise RuntimeError("CTranslate2 has no native Windows DLL")',
  'with library.open("rb") as source:',
  '    if os.fstat(source.fileno()).st_size == 0:',
  '        raise RuntimeError("CTranslate2 lacks the required HIP DLL markers")',
  '    with mmap.mmap(source.fileno(), 0, access=mmap.ACCESS_READ) as binary:',
  `        required_dlls = ${JSON.stringify(windowsRocmRecipe.ctranslate2.required_dlls)}`,
  '        if any(binary.find(name.encode("ascii") + b"\\0") < 0 for name in required_dlls):',
  '            raise RuntimeError("CTranslate2 lacks the required HIP DLL markers")',
  'torch_spec = importlib.util.find_spec("torch")',
  'if torch_spec is None or not torch_spec.submodule_search_locations:',
  '    raise RuntimeError("PyTorch package is missing")',
  'torch_dir = Path(next(iter(torch_spec.submodule_search_locations)))',
  'directories = [',
  '    ct2_dir,',
  '    ct2_dir.parent / "_rocm_sdk_core" / "bin",',
  '    ct2_dir.parent / "_rocm_sdk_libraries_custom" / "bin",',
  '    torch_dir / "lib",',
  ']',
  'with ExitStack() as stack:',
  '    for directory in dict.fromkeys(path.resolve() for path in directories):',
  '        if directory.is_dir():',
  '            stack.enter_context(os.add_dll_directory(str(directory)))',
  '    import ctranslate2',
  `    if ctranslate2.__version__ != ${JSON.stringify(windowsRocmRecipe.ctranslate2.version)}:`,
  '        raise RuntimeError("Unsupported CTranslate2 ROCm version")',
  '    if ctranslate2.get_cuda_device_count() <= 0:',
  '        raise RuntimeError("CTranslate2 cannot access the AMD GPU")',
  '    if "float16" not in ctranslate2.get_supported_compute_types("cuda", 0):',
  '        raise RuntimeError("CTranslate2 HIP float16 unavailable")',
].join('\n');
export type RuntimeTorchVariant = TorchVariant;

export function runtimeTorchVariantFromEnv(): RuntimeTorchVariant {
  return resolveTorchVariant().variant;
}

export function rocmTorchApplies(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): boolean {
  return platform !== 'darwin' && (platform !== 'win32' || arch === 'x64');
}
/** The user explicitly asked for ROCm torch AND this OS can have it. */
export function rocmTorchOptIn(platform: NodeJS.Platform = process.platform): boolean {
  return (
    process.env.OMNIVOICE_TORCH_VARIANT?.trim().toLowerCase() === 'rocm' &&
    rocmTorchApplies(platform)
  );
}
/**
 * CPU-only PyTorch for hosts without an NVIDIA driver. The lock pins the
 * `+cu128` build on Linux/Windows x64, whose wheels (plus ~15 `nvidia-*`
 * packages on Linux) are several GB a CPU-only machine can never use. These
 * pins mirror `backend/services/sidecar_install.py::_torch_pin_args` and are
 * kept equal to the `torch==` constraints in pyproject.toml by
 * tests/test_rocm_torch_pins_match_pyproject.py.
 */
export const CPU_TORCH_INDEX = 'https://download.pytorch.org/whl/cpu';
export const CPU_TORCH_PINS = [
  'torch==2.8.0+cpu',
  'torchaudio==2.8.0+cpu',
  'torchvision==0.23.0+cpu',
] as const;
export const CPU_TORCH_ARGS = [
  '--extra-index-url',
  CPU_TORCH_INDEX,
  '--index-strategy',
  'unsafe-best-match',
] as const;
/** Windows on ARM runs the x64 interpreter: PyTorch ships no win_arm64 torchaudio. */
export const WIN_ARM64_PYTHON_REQUEST = 'cpython-3.11-windows-x86_64';
export const RUNTIME_REPAIR_PACKAGES = ['torch', 'torchaudio', 'torchvision'] as const;
export const RUNTIME_NATIVE_IMPORT_PROBE = 'import torch, torchaudio, torchvision';
export const RUNTIME_IMPORT_PROBE =
  'import fastapi, uvicorn, omnivoice, faster_whisper, sentencepiece, torch, torchaudio, torchvision';
const WINDOWS_ROCM_PROBE = [
  'import torch',
  'if not torch.version.hip or not torch.cuda.is_available():',
  '    raise RuntimeError("ROCm cannot access the AMD GPU")',
  'tensor = torch.ones((4, 4), device="cuda")',
  'result = (tensor @ tensor)[0, 0].item()',
  'if result != 4:',
  '    raise RuntimeError("ROCm GPU matrix multiplication returned an invalid result")',
].join('\n');
const RUNTIME_SCHEMA = 'electron-runtime-v2-cudnn8';
const CUDNN8_PROBE_PREFIX = 'VOICESTUDIO_CUDNN8_PROBE=';
const REQUIRED_ENV_BYTES = 9 * 1024 ** 3;
// No CUDA wheels: torch itself is ~0.2 GB; the rest is ordinary dependencies plus
// the uv cache copy that cannot hardlink across volumes.
const REQUIRED_CPU_ENV_BYTES = 5 * 1024 ** 3;
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

export function rocmTorchInstallArgs(python: string, platform = process.platform): string[] {
  if (platform === 'win32') {
    return [
      '--no-config',
      'pip',
      'install',
      '--python',
      python,
      ...WINDOWS_ROCM_TORCH_PINS,
      '--reinstall-package',
      'torch',
      '--reinstall-package',
      'torchaudio',
      '--reinstall-package',
      'torchvision',
      '--find-links',
      process.env.OMNIVOICE_WINDOWS_ROCM_FIND_LINKS || WINDOWS_ROCM_FIND_LINKS,
    ];
  }
  return [
    'pip',
    'install',
    '--reinstall',
    '--python',
    python,
    ...ROCM_TORCH_PINS,
    '--index-url',
    process.env.OMNIVOICE_TORCH_INDEX || ROCM_TORCH_INDEX,
  ];
}

export type TorchVariant = 'default' | 'cpu' | 'rocm';
export interface TorchChoice {
  variant: TorchVariant;
  /** True when pinned via OMNIVOICE_TORCH_VARIANT; false when inferred from the host. */
  explicit: boolean;
}

/** Linux/Windows x64 (and Windows on ARM, via emulation) are where the lock selects CUDA wheels. */
function cpuTorchApplies(platform: NodeJS.Platform, arch: string): boolean {
  return (
    (platform === 'linux' && arch === 'x64') ||
    (platform === 'win32' && (arch === 'x64' || arch === 'arm64'))
  );
}

/**
 * Whether an NVIDIA driver is installed. File checks only (no subprocess), and
 * deliberately generous: a false positive costs one avoidable GPU-wheel
 * download, a false negative would silently strand a GPU host on CPU torch.
 */
export function nvidiaDriverPresent(
  platform: NodeJS.Platform = process.platform,
  exists: (path: string) => boolean = existsSync,
  env: NodeJS.ProcessEnv = process.env,
): boolean {
  if (platform === 'win32') {
    const root = env.SystemRoot || env.windir || 'C:\\Windows';
    return (
      ['nvcuda.dll', 'nvml.dll', 'nvidia-smi.exe'].some((name) =>
        exists(join(root, 'System32', name)),
      ) || exists('C:\\Program Files\\NVIDIA Corporation\\NVSMI\\nvidia-smi.exe')
    );
  }
  if (platform !== 'linux') return false;
  return [
    '/proc/driver/nvidia/version',
    '/dev/nvidiactl',
    '/usr/lib/wsl/lib/libcuda.so.1',
    '/usr/lib/x86_64-linux-gnu/libcuda.so.1',
    '/usr/lib64/libcuda.so.1',
    '/usr/lib/libcuda.so.1',
    '/lib/x86_64-linux-gnu/libcuda.so.1',
    '/usr/local/nvidia/lib64/libcuda.so.1',
    '/usr/bin/nvidia-smi',
  ].some((path) => exists(path));
}

/**
 * Pick the PyTorch flavour to install. `OMNIVOICE_TORCH_VARIANT` =
 * `cuda` | `cpu` | `rocm` | `auto` (default). Auto installs the CPU build on
 * Linux/Windows x64 hosts with no NVIDIA driver (no multi-GB CUDA download) and
 * always on Windows on ARM; every other host keeps the lock's default wheels
 * (macOS: PyPI MPS/CPU, Linux arm64: PyPI CPU).
 */
export function resolveTorchVariant(
  env: NodeJS.ProcessEnv = process.env,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  hasNvidia: () => boolean = () => nvidiaDriverPresent(platform),
): TorchChoice {
  const raw = env.OMNIVOICE_TORCH_VARIANT?.trim().toLowerCase() ?? '';
  if (raw === 'rocm' && rocmTorchApplies(platform, arch))
    return { variant: 'rocm', explicit: true };
  if (raw === 'cuda' || raw === 'default') return { variant: 'default', explicit: true };
  if (raw === 'cpu') {
    return { variant: cpuTorchApplies(platform, arch) ? 'cpu' : 'default', explicit: true };
  }
  if (!cpuTorchApplies(platform, arch)) return { variant: 'default', explicit: false };
  if (platform === 'win32' && arch === 'arm64') return { variant: 'cpu', explicit: false };
  return { variant: hasNvidia() ? 'default' : 'cpu', explicit: false };
}

/** Python request for a fresh venv; Windows on ARM needs the emulated x64 build. */
export function managedPythonRequest(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  variant: TorchVariant = resolveTorchVariant(process.env, platform, arch).variant,
): string {
  if (platform === 'win32' && arch === 'x64' && variant === 'rocm')
    return windowsRocmRecipe.python.version;
  return platform === 'win32' && arch === 'arm64' ? WIN_ARM64_PYTHON_REQUEST : '3.11';
}

/** The lock's CUDA-only runtime wheels: every `nvidia-*` package but the tiny NVML binding. */
export function cudaOnlyPackages(lockText: string): string[] {
  const names = new Set<string>();
  for (const match of lockText.matchAll(/^name = "(nvidia-[a-z0-9-]+)"$/gm)) {
    if (match[1] !== 'nvidia-ml-py') names.add(match[1]!);
  }
  return [...names].sort();
}

function runtimeTorchChoice(selection?: RuntimeTorchVariant | TorchChoice): TorchChoice {
  if (typeof selection === 'string') return { variant: selection, explicit: true };
  return selection ?? resolveTorchVariant();
}

async function dependencyStamp(
  bundle: string,
  variant: TorchVariant = resolveTorchVariant().variant,
): Promise<string> {
  const hash = createHash('sha256');
  hash.update(RUNTIME_SCHEMA);
  if (variant === 'rocm') {
    hash.update(':torch=rocm');
    if (process.platform === 'win32')
      hash.update(`:windows-rocm=${JSON.stringify(windowsRocmRecipe)}`);
    hash.update(
      process.platform === 'win32'
        ? process.env.OMNIVOICE_WINDOWS_ROCM_FIND_LINKS || WINDOWS_ROCM_FIND_LINKS
        : process.env.OMNIVOICE_TORCH_INDEX || ROCM_TORCH_INDEX,
    );
  }
  if (variant === 'cpu') hash.update(':torch=cpu');
  for (const file of ['pyproject.toml', 'uv.lock']) hash.update(await readFile(join(bundle, file)));
  return hash.digest('hex');
}

/**
 * Markers proving the installed environment fits this host's torch flavour.
 * An inferred CPU choice also accepts a pre-existing default (CUDA) environment:
 * it runs fine on CPU and must not be discarded just because this version learned
 * to pick a smaller download. The reverse is not true: a CPU environment on a
 * host that has since gained an NVIDIA driver is stale and needs reinstalling.
 */
async function acceptedStamps(bundle: string, choice: TorchChoice): Promise<string[]> {
  const stamps = [await dependencyStamp(bundle, choice.variant)];
  if (choice.variant === 'cpu' && !choice.explicit) {
    stamps.push(await dependencyStamp(bundle, 'default'));
  }
  return stamps;
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

/**
 * How long the import probe may run before it is declared inconclusive. A cold
 * `import torch, torchaudio, torchvision` is dominated by disk and antivirus
 * scanning of hundreds of MB of native libraries, not by the CPU: a first
 * launch after install on a spinning disk or a scanner-contended Windows host
 * routinely needs 40-90 s, so the old 30 s ceiling declared healthy runtimes
 * broken (#2445, #2465).
 */
export const RUNTIME_PROBE_TIMEOUT_MS = 180_000;

/** Node marks an `execFile` that exceeded `timeout` as killed by its signal. */
function probeTimedOut(error: unknown): boolean {
  const failure = error as { killed?: boolean; signal?: string | null } | null;
  return Boolean(failure?.killed && failure.signal);
}

/**
 * Check the selected interpreter locally before trusting runtime metadata.
 *
 * Only a probe that actually FAILED (a missing module, a bad DLL, an
 * interpreter that will not start) proves the runtime broken. A probe that was
 * still importing when the generous ceiling expired proves only that this host
 * is slow, so it is inconclusive and the runtime is trusted: treating "slow" as
 * "broken" is what sent working installs back to the multi-GB setup screen, or
 * reported "environment missing or incomplete" for an intact one. If the
 * interpreter really is wedged the backend launch that follows fails with the
 * process's own output and the startup budget, which is a far better diagnostic
 * than a silent false negative here.
 */
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
      ['-c', RUNTIME_IMPORT_PROBE],
      {
        cwd: project,
        windowsHide: true,
        timeout: RUNTIME_PROBE_TIMEOUT_MS,
        maxBuffer: 256 * 1024,
        env,
      },
      (error) => resolve(!error || probeTimedOut(error)),
    );
  });
}

export async function runtimeReady(
  bundle: string,
  project: string,
  selection?: RuntimeTorchVariant | TorchChoice,
): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  try {
    return (
      (await stat(runtimePython(project))).isFile() &&
      (await stat(join(project, '.venv', 'pyvenv.cfg'))).isFile() &&
      (await acceptedStamps(bundle, runtimeTorchChoice(selection))).includes(
        await readFile(join(project, '.runtime-ready'), 'utf8'),
      )
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
export async function runtimeCompatible(
  bundle: string,
  project: string,
  selection?: RuntimeTorchVariant | TorchChoice,
): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  const choice = runtimeTorchChoice(selection);
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
      (marker === null
        ? // A Tauri environment carries the lock's default (CUDA) torch: fine for
          // inferred CPU hosts, wrong for an explicit ROCm or CPU request.
          choice.variant === 'default' || (choice.variant === 'cpu' && !choice.explicit)
        : (await acceptedStamps(bundle, choice)).includes(marker))
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
  selection?: RuntimeTorchVariant | TorchChoice,
): Promise<void> {
  signal.throwIfAborted();
  phase('checking');
  if (process.platform === 'darwin' && process.arch === 'x64') {
    throw Object.assign(new Error('INTEL_MAC_UNSUPPORTED'), { code: 'INTEL_MAC_UNSUPPORTED' });
  }
  await mkdir(project, { recursive: true });
  const torch = runtimeTorchChoice(selection);
  const requiredBytes = torch.variant === 'cpu' ? REQUIRED_CPU_ENV_BYTES : REQUIRED_ENV_BYTES;
  const disk = await statfs(project);
  if (disk.bavail * disk.bsize < requiredBytes) {
    throw Object.assign(
      new Error(
        `Runtime setup needs at least ${requiredBytes / 1024 ** 3} GiB of free disk space.`,
      ),
      { code: 'ENOSPC', requiredGib: requiredBytes / 1024 ** 3 },
    );
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
  // Heal an existing environment first so its interpreter can run the probes
  // below (and uv's own interpreter query) instead of dying in `site` (#1783).
  // Repair is best-effort; the interpreter/import probes remain authoritative.
  await asciiSafePthFiles(join(project, '.venv')).catch(() => []);
  const interpreterExists = await stat(runtimePython(project)).then(
    (info) => info.isFile(),
    () => false,
  );
  let existingPython = false;
  const windowsRocm = process.platform === 'win32' && torch.variant === 'rocm';
  const expectedPython = windowsRocm
    ? `tuple(map(int, ${JSON.stringify(windowsRocmRecipe.python.version)}.split(".")))`
    : '(3, 11)';
  const repairPackages: string[] = [];
  if (interpreterExists) {
    try {
      await run(
        runtimePython(project),
        [
          '-c',
          `import sys\nif sys.version_info[:2] != ${expectedPython}:\n    raise RuntimeError("Unsupported runtime Python version")` +
            // A native ARM64 interpreter cannot install torchaudio/torchvision.
            (managedPythonRequest() === WIN_ARM64_PYTHON_REQUEST
              ? '\nimport platform\nif platform.machine().upper() not in ("AMD64", "X86_64"):\n    raise RuntimeError("Windows ARM requires an x64 runtime interpreter")'
              : ''),
        ],
        project,
      );
      existingPython = true;
      try {
        await run(runtimePython(project), ['-c', RUNTIME_IMPORT_PROBE], project);
      } catch {
        // Classify the failed full probe before evicting multi-GB native wheels.
        // A missing FastAPI/uvicorn install is repaired by ordinary frozen sync
        // and must retain the app-private wheel cache for offline recovery.
        try {
          await run(runtimePython(project), ['-c', RUNTIME_NATIVE_IMPORT_PROBE], project);
        } catch {
          // A native wheel can be missing or ABI-broken while its dist-info
          // still convinces uv sync that it is installed. Reinstall only then.
          repairPackages.push(...RUNTIME_REPAIR_PACKAGES);
        }
        // Sentencepiece has its own native wheel, independent of PyTorch.
        signal.throwIfAborted();
        try {
          await run(runtimePython(project), ['-c', 'import sentencepiece'], project);
        } catch {
          repairPackages.push('sentencepiece');
        }
      }
    } catch {
      // Retry must not keep a wrong-base or native-crashing interpreter simply
      // because its executable survived interrupted setup. uv selects the managed
      // replacement; immutable downloads and user data remain outside the venv.
      signal.throwIfAborted();
    }
  }
  const pythonArgs = existingPython
    ? ['--python', runtimePython(project)]
    : [
        '--managed-python',
        '--python',
        managedPythonRequest(process.platform, process.arch, torch.variant),
      ];
  const cpuTorch = torch.variant === 'cpu';
  const skipLockedTorch = cpuTorch || windowsRocm;
  // A failed native import may leave distribution metadata intact, so uv's
  // ordinary sync would otherwise consider the broken wheel already satisfied.
  const torchPackages: readonly string[] = RUNTIME_REPAIR_PACKAGES;
  // CPU and Windows ROCm repairs use their variant-specific install below,
  // never the lock's CUDA torch through `uv sync --reinstall-package`.
  const repairArgs = repairPackages
    .filter((name) => !skipLockedTorch || !torchPackages.includes(name))
    .flatMap((name) => ['--reinstall-package', name]);
  signal.throwIfAborted();
  if (repairPackages.length) {
    // uv may hardlink installed files to its unpacked wheel cache. A corrupted
    // native file can therefore poison the cached copy too; evict only this
    // package from the app-private cache before reinstalling its locked wheel.
    await run(uv, ['cache', 'clean', ...repairPackages], project, env);
    signal.throwIfAborted();
  }
  // The lock resolves torch to the CUDA build (and, on Linux, ~3 GB of nvidia-*
  // runtime wheels) for every Linux/Windows x64 host. CPU and Windows ROCm
  // install their own wheels right after the frozen sync instead.
  const skipArgs = skipLockedTorch
    ? [
        ...torchPackages,
        ...cudaOnlyPackages(await readFile(join(project, 'uv.lock'), 'utf8')),
      ].flatMap((name) => ['--no-install-package', name])
    : [];
  await run(
    uv,
    ['sync', '--frozen', '--no-dev', ...pythonArgs, ...repairArgs, ...skipArgs],
    project,
    env,
  );
  signal.throwIfAborted();
  if (cpuTorch) {
    await run(
      uv,
      [
        'pip',
        'install',
        '--python',
        runtimePython(project),
        ...repairPackages
          .filter((name) => torchPackages.includes(name))
          .flatMap((name) => ['--reinstall-package', name]),
        ...CPU_TORCH_PINS,
        ...CPU_TORCH_ARGS,
      ],
      project,
      env,
    );
    signal.throwIfAborted();
  }
  if (torch.variant === 'rocm') {
    phase('installing_deps');
    await run(uv, rocmTorchInstallArgs(runtimePython(project)), project, env);
    signal.throwIfAborted();
    if (windowsRocm) {
      await run(runtimePython(project), ['-c', WINDOWS_ROCM_PROBE], project);
      signal.throwIfAborted();
      const cache = join(project, '..', '.uv-cache');
      await mkdir(cache, { recursive: true });
      const archive = join(
        cache,
        `${posix.basename(new URL(WINDOWS_ROCM_CT2_ARCHIVE).pathname, '.zip')}-v${windowsRocmRecipe.ctranslate2.version}.zip`,
      );
      const wheel = join(cache, WINDOWS_ROCM_CT2_WHEEL);
      const pendingWheel = `${wheel}.incoming-${randomUUID()}`;
      phase('installing_deps');
      await downloadRuntimeArchive(
        WINDOWS_ROCM_CT2_ARCHIVE,
        archive,
        WINDOWS_ROCM_CT2_ARCHIVE_SHA256,
        env,
        signal,
      );
      signal.throwIfAborted();
      try {
        await run(
          runtimePython(project),
          [
            '-c',
            WINDOWS_ROCM_CT2_EXTRACT,
            archive,
            windowsRocmRecipe.ctranslate2.wheel_member,
            WINDOWS_ROCM_CT2_WHEEL_SHA256,
            pendingWheel,
          ],
          project,
          env,
        );
        signal.throwIfAborted();
        await rename(pendingWheel, wheel);
      } finally {
        await rm(pendingWheel, { force: true });
      }
      await run(
        uv,
        ['--no-config', 'pip', 'install', '--python', runtimePython(project), '--no-deps', wheel],
        project,
        env,
      );
      signal.throwIfAborted();
      await run(runtimePython(project), ['-c', WINDOWS_ROCM_CT2_PROBE], project);
      signal.throwIfAborted();
    }
  }
  await ensureCudnn8Compat(uv, project, run, env, signal);
  signal.throwIfAborted();
  // A non-English profile path in uv's editable .pth crashes Python 3.11 at
  // startup on a non-UTF-8 Windows code page (#1783).
  await asciiSafePthFiles(join(project, '.venv')).catch(() => []);
  phase('verifying');
  await run(runtimePython(project), ['-c', RUNTIME_IMPORT_PROBE], project);
  signal.throwIfAborted();
  await writeFile(join(project, '.runtime-ready'), await dependencyStamp(bundle, torch.variant));
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
