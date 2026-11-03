import {
  nvidiaDriverPresent,
  cudaOnlyPackages,
  windowsRocmSyncArgs,
} from '../../scripts/torch-variant.mjs';
export { nvidiaDriverPresent, cudaOnlyPackages } from '../../scripts/torch-variant.mjs';
import { inspectRuntimeInterpreter } from './runtime-interpreter';
import { RuntimeLaunchRefusedError, runtimeLaunchRefusal } from './runtime-launch-refusal';
import { WINDOWS_ROCM_IMPORT_TIMEOUT_MS } from './runtime-probe';
import {
  windowsRocmSelected,
  windowsRocmStamp,
  windowsRocmReady,
  windowsRocmRecipeMatches,
  WINDOWS_ROCM_IMPORT_PREFIX,
  WINDOWS_ROCM_MODULE,
  RUNTIME_TORCH_MARKER,
  WINDOWS_ROCM_PYTHON_REQUEST,
} from './runtime-windows-rocm';
import { clearCtranslate2ExecutableStack } from '../../scripts/native-compat.mjs';
export { clearCtranslate2ExecutableStack } from '../../scripts/native-compat.mjs';
import { downloadProxyEnv } from './proxy-env';
import {
  DIRECTORY_RENAME,
  renameWithRetry,
  retryTransient,
  type RenameRetryOptions,
} from './rename-retry';
import { downloadRuntimeInstaller } from './runtime-download';
import { asciiSafePthFiles } from './pth-ascii';
import { pruneInterruptedUvDownloads, setupDownloadHosts } from './setup-downloads';
import {
  AUTO_PYPI_MIRROR,
  PYPI_FILES_ORIGIN,
  mirrorLockUrls,
  normalizeLockHosts,
  pypiFilesMirror,
  raceAutoPypiMirror,
} from './pypi-mirror';
import { scrubText } from '../shared/utils/scrub';
import {
  clearPrefetchedWheels,
  httpsRangeTransport,
  localizeLockUrls,
  planWheelPrefetch,
  prefetchConnections,
  prefetchWheels,
} from './wheel-prefetch';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { cp, mkdir, readFile, readdir, rm, stat, statfs, writeFile } from 'node:fs/promises';
import { dirname, join } from 'node:path';

// The web UI is not staged: the backend serves it straight from the app's
// resources via OMNIVOICE_FRONTEND_DIST, so it always matches this version.
const SOURCES = [
  'backend',
  'config',
  'omnivoice',
  'pyproject.toml',
  'uv.lock',
  'scripts/windows-rocm-recipe.json',
  'scripts/smoke_windows_rocm.py',
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
/**
 * `uv pip install` arguments that swap the main venv's torch stack for the
 * ROCm wheels.
 *
 * The ROCm index is an EXTRA index and only the torch packages are reinstalled.
 * With `--index-url` plus `--reinstall`, every dependency had to come from
 * PyTorch's index, which carries no Pillow 13, so the project's
 * `pillow>=13.0.0` override made the swap unsatisfiable and a ROCm install
 * never finished (#3158, #3418). The local tag comes from the index name
 * (`…/rocm6.4` → `+rocm6.4`), mirroring `sidecar_install._rocm_pin_args`; a
 * mirror without such a name keeps the bare pins, and the tagged build still
 * wins by PEP 440 ordering. Kept equal to `scripts/setup.py`.
 */
export function rocmTorchInstallArgs(index: string): string[] {
  const tail = index.replace(/\/+$/, '').split('/').pop() ?? '';
  const tag = /^rocm\d[\d.]*$/.test(tail) ? `+${tail}` : '';
  return [
    ...ROCM_TORCH_PINS.flatMap((pin) => ['--reinstall-package', pin.split('==')[0]!]),
    ...ROCM_TORCH_PINS.map((pin) => `${pin}${tag}`),
    '--extra-index-url',
    index,
    '--index-strategy',
    'unsafe-best-match',
  ];
}
/** Exits non-zero unless the installed torch is a ROCm (HIP) build. */
export const ROCM_TORCH_PROBE =
  "import torch, sys; sys.exit(0 if getattr(torch.version, 'hip', None) else 1)";
export const ROCM_TORCH_PROBE_FAILURE =
  'The ROCm setup installed a PyTorch build without ROCm support. Check that ' +
  'OMNIVOICE_TORCH_INDEX points at a ROCm wheel index, then run setup again.';
/**
 * Linux uses PyTorch's ROCm index; Windows x64 uses the shared AMD recipe.
 */
export function rocmTorchApplies(platform: NodeJS.Platform = process.platform): boolean {
  return platform !== 'darwin';
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
/**
 * The Visual C++ runtime the Windows PyTorch wheels link against.
 *
 * `torchaudio/lib/libtorchaudio.pyd` imports `VCOMP140.DLL` (the MSVC OpenMP
 * runtime) and nothing else in the torch stack does: `torch_cpu.dll`,
 * `torch_cuda.dll` and `c10_cuda.dll` need only `MSVCP140.dll`,
 * `VCRUNTIME140.dll` and `VCRUNTIME140_1.dll`. A Windows install without the
 * OpenMP part of the "Microsoft Visual C++ 2015-2022 Redistributable (x64)"
 * therefore imports torch fine and then dies on torchaudio with
 * `FileNotFoundError: Could not find module '…libtorchaudio.pyd' (or one of
 * its dependencies)` (#3058).
 *
 * Reinstalling the wheels cannot supply a system library, so this class has
 * to be recognised instead of downloading gigabytes that cannot fix it.
 */
export const WINDOWS_TORCH_SYSTEM_LIBRARIES = [
  'msvcp140.dll',
  'vcruntime140.dll',
  'vcruntime140_1.dll',
  'vcomp140.dll',
] as const;
/** Marker the native diagnostic prints on stdout. */
export const NATIVE_DIAGNOSTIC_PREFIX = 'VOICESTUDIO_NATIVE_DIAGNOSTIC=';
/** Error code for "a system library the native extensions need is missing". */
export const MISSING_SYSTEM_LIBRARY_CODE = 'MISSING_SYSTEM_LIBRARY';
/** Error code for "the Visual C++ runtime this host loads is too old for the wheels". */
export const OUTDATED_SYSTEM_LIBRARY_CODE = 'OUTDATED_SYSTEM_LIBRARY';
/**
 * The oldest `MSVCP140.dll` that wheels built with Visual Studio 2022 17.10 or
 * later can run against. 17.10 made `std::mutex`'s constructor `constexpr`, so
 * a module built with it locks a mutex an older `MSVCP140.dll` never set up and
 * dies with an access violation (0xC0000005) instead of an ImportError.
 * sentencepiece 0.2.2's Windows wheel is built with MSVC 14.51 and imports
 * `_Mtx_lock` from `MSVCP140.dll`, so a Windows install whose system Visual C++
 * runtime predates 14.40 crashes on `import sentencepiece` with no traceback
 * (#3293, #2942). No Python wheel can update a system DLL.
 */
export const MIN_MSVCP140_VERSION = [14, 40] as const;
/** Windows exit status for an access violation, unsigned and signed. */
const ACCESS_VIOLATION_EXIT_CODES = new Set<unknown>([3221225477, -1073741819]);
const RUNTIME_IMPORT_MODULES = [
  'fastapi',
  'uvicorn',
  'omnivoice',
  'faster_whisper',
  'sentencepiece',
  'torch',
  'torchaudio',
  'torchvision',
] as const;
const IMPORT_MARKER = 'VOICESTUDIO_IMPORT=';
/**
 * Imports every runtime module, announcing each one first. A native import that
 * dies with an access violation (exit code 3221225477) leaves no traceback, so
 * the last announcement is the only evidence of which module crashed (#3293).
 */
export const RUNTIME_IMPORT_PROBE = RUNTIME_IMPORT_MODULES.map(
  (name) => `print('${IMPORT_MARKER}${name}', flush=True)\nimport ${name}`,
).join('\n');
/** The module an import probe was loading when its output stopped, if it said. */
export function lastImportAttempted(output: unknown): string | null {
  const marked = String(output ?? '')
    .split(/\r?\n/)
    .filter((line) => line.startsWith(IMPORT_MARKER))
    .at(-1);
  return marked ? marked.slice(IMPORT_MARKER.length).trim() || null : null;
}
function importFailure(error: unknown): Error {
  const failure = error as { code?: unknown; message?: unknown; stdout?: unknown } | null;
  const module = lastImportAttempted(failure?.stdout);
  if (!module) return error instanceof Error ? error : new Error(String(error));
  return Object.assign(
    new Error(`${String(failure?.message)}; the import check stopped while importing "${module}"`),
    { code: failure?.code, stdout: failure?.stdout },
  );
}
const RUNTIME_SCHEMA = 'electron-runtime-v2-cudnn8';
const CUDNN8_PROBE_PREFIX = 'VOICESTUDIO_CUDNN8_PROBE=';
const REQUIRED_ENV_BYTES = 9 * 1024 ** 3;
// No CUDA wheels: torch itself is ~0.2 GB; the rest is ordinary dependencies plus
// the uv cache copy that cannot hardlink across volumes. macOS wheels never carry
// CUDA either (an Apple Silicon runtime measured 1.9 GB plus a 1.8 GB uv cache).
const REQUIRED_CPU_ENV_BYTES = 5 * 1024 ** 3;
export type RuntimePhase = 'checking' | 'downloading_uv' | 'installing_deps' | 'verifying';
export type RuntimeRegion = 'auto' | 'global' | 'china' | 'russia' | 'restricted';
export type RuntimeRunner = (
  command: string,
  args: string[],
  cwd: string,
  env?: NodeJS.ProcessEnv,
  /** `quiet`: capture stdout and stderr together and keep them out of the setup log. */
  options?: { timeoutMs: number; quiet?: boolean },
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

function runtimeDownloadEnv(effective: RuntimeRegion): NodeJS.ProcessEnv {
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
  windowsRocmSelected(raw, platform, arch);
  if (raw === 'rocm' && rocmTorchApplies(platform)) return { variant: 'rocm', explicit: true };
  if (raw === 'cuda' || raw === 'default') return { variant: 'default', explicit: true };
  if (raw === 'cpu') {
    return { variant: cpuTorchApplies(platform, arch) ? 'cpu' : 'default', explicit: true };
  }
  if (!cpuTorchApplies(platform, arch)) return { variant: 'default', explicit: false };
  if (platform === 'win32' && arch === 'arm64') return { variant: 'cpu', explicit: false };
  return { variant: hasNvidia() ? 'default' : 'cpu', explicit: false };
}

/** uv's download cache: beside the replaceable project, so retries reuse finished wheels. */
export function runtimeUvCache(project: string): string {
  return join(project, '..', '.uv-cache');
}

/** Package to the host its wheel downloads from in this install, for the setup screen (#3411). */
export async function runtimeDownloadHosts(
  bundle: string,
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
  /** Host serving the PyPI-hosted wheels: a regional mirror (#3444) or the origin. */
  pypiHost: string = new URL(PYPI_FILES_ORIGIN).host,
): Promise<Map<string, string>> {
  const lock = await readFile(join(bundle, 'uv.lock'), 'utf8');
  const rocm = resolveTorchVariant(process.env, platform, arch).variant === 'rocm';
  // Windows selects one of two AMD sources after inspecting the GPU; both
  // are possible here because the setup progress map is built before that probe.
  const rocmHost =
    platform === 'win32'
      ? 'repo.radeon.com / stable.repo.amd.com'
      : new URL(process.env.OMNIVOICE_TORCH_INDEX || ROCM_TORCH_INDEX).host;
  const hosts = setupDownloadHosts(lock, {
    pytorchIndex: cpuTorchApplies(platform, arch),
    overrides: rocm
      ? Object.fromEntries(ROCM_TORCH_PINS.map((pin) => [pin.split(/[=<>!~]/)[0]!, rocmHost]))
      : undefined,
  });
  // A regional mirror serves the PyPI-hosted wheels (#3444); other hosts stay.
  const origin = new URL(PYPI_FILES_ORIGIN).host;
  if (pypiHost !== origin) {
    for (const [name, host] of hosts) if (host === origin) hosts.set(name, pypiHost);
  }
  return hosts;
}

/** Python request for a fresh venv; Windows on ARM needs the emulated x64 build. */
export function managedPythonRequest(
  platform: NodeJS.Platform = process.platform,
  arch: string = process.arch,
): string {
  if (windowsRocmSelected(undefined, platform, arch)) return WINDOWS_ROCM_PYTHON_REQUEST;
  return platform === 'win32' && arch === 'arm64' ? WIN_ARM64_PYTHON_REQUEST : '3.11';
}

async function dependencyStamp(
  bundle: string,
  variant: TorchVariant = resolveTorchVariant().variant,
): Promise<string> {
  const hash = createHash('sha256');
  hash.update(RUNTIME_SCHEMA);
  if (variant === 'rocm') hash.update(':torch=rocm');
  if (variant === 'cpu') hash.update(':torch=cpu');
  if (windowsRocmSelected(variant)) hash.update(await windowsRocmStamp(bundle));
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
async function acceptedStamps(bundle: string): Promise<string[]> {
  const choice = resolveTorchVariant();
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

async function ensureCudnn8Compat(
  uv: string,
  project: string,
  run: RuntimeRunner,
  env: NodeJS.ProcessEnv,
  signal: AbortSignal,
  variant: TorchVariant,
): Promise<void> {
  if (process.platform === 'darwin') return;
  const python = runtimePython(project);
  const script = [
    'import json, sysconfig',
    // CPU and ROCm installs cannot need CUDA compatibility libraries. Avoid
    // importing native Torch just to rediscover that fact (#2942), while still
    // finding site-packages for the Linux executable-stack repair below.
    ...(variant === 'default'
      ? [
          "device = 'unknown'",
          'try:',
          '    import torch',
          "    device = 'hip' if getattr(torch.version, 'hip', None) else ('cuda' if torch.cuda.is_available() else 'none')",
          'except Exception:',
          '    pass',
        ]
      : [`device = '${variant === 'rocm' ? 'hip' : 'none'}'`]),
    `print('${CUDNN8_PROBE_PREFIX}' + json.dumps({'device': device, 'sitePackages': sysconfig.get_paths()['purelib']}), flush=True)`,
    // Skip interpreter teardown: unloading native Torch/CUDA DLLs at exit is
    // where Windows access violations (0xC0000005) surface after the result
    // has already been printed (#2942).
    'import os',
    'os._exit(0)',
  ].join('\n');
  // The probe is advisory. A native crash inside it must never abort runtime
  // setup: fall back to "no CUDA compat libraries needed" and let the real
  // import verification below decide whether the runtime works.
  let probeOutput: string | void;
  try {
    probeOutput = await run(python, ['-c', script], project, env);
  } catch (error) {
    signal.throwIfAborted();
    probeOutput = (error as { stdout?: string }).stdout;
    if (!parseCudnn8Probe(probeOutput)) return;
  }
  const probe = parseCudnn8Probe(probeOutput);
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

export const RUNTIME_INSTALLING_MARKER = '.runtime-installing';
export const RUNTIME_READY_MARKER = '.runtime-ready';

/**
 * What the install marker says about the project.
 *
 * `installRuntime` writes `.runtime-installing` first and `.runtime-ready` only
 * after the final import verification, so a ready marker written AFTER the
 * install marker proves that install finished. A marker that survived anyway
 * (a removal Windows refused while a scanner held the file, a quit between the
 * last two writes) is stale: trusting it sent a finished 16 GB runtime back to
 * "Resume interrupted download" (#3475). A stale marker is removed best-effort.
 */
async function installMarkerState(project: string): Promise<'none' | 'active' | 'unknown'> {
  const markerPath = join(project, RUNTIME_INSTALLING_MARKER);
  let marker;
  try {
    marker = await stat(markerPath);
  } catch (error) {
    return (error as NodeJS.ErrnoException).code === 'ENOENT' ? 'none' : 'unknown';
  }
  const ready = await stat(join(project, RUNTIME_READY_MARKER)).catch(() => null);
  if (ready?.isFile() && ready.mtimeMs >= marker.mtimeMs) {
    await rm(markerPath, { force: true }).catch(() => undefined);
    return 'none';
  }
  return marker.isFile() ? 'active' : 'unknown';
}

async function runtimeIncomplete(project: string): Promise<boolean> {
  return (await installMarkerState(project)) !== 'none';
}

/** True only when a prior explicit runtime install stopped before it finished. */
export async function runtimeInstallInterrupted(project: string): Promise<boolean> {
  return (await installMarkerState(project)) === 'active';
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
/**
 * The line that says WHY the import probe failed: Python's final exception
 * line (`ModuleNotFoundError: No module named 'sentencepiece'`, `ImportError:
 * DLL load failed ...`), or the launch error when the interpreter never ran.
 * Without it a source checkout only learned "missing or incomplete" and could
 * not tell an unfinished setup from one import that keeps failing (#2555).
 */
export function probeFailureDetail(error: unknown, stderr: unknown): string {
  const lines = String(stderr ?? '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean);
  const failure = error as { code?: unknown; message?: unknown } | null;
  const detail =
    lines.at(-1) ??
    (typeof failure?.code === 'string'
      ? `the interpreter could not be started (${failure.code})`
      : String(failure?.message ?? 'unknown error').split('\n')[0]);
  return scrubText(detail).slice(0, 300);
}

export async function runtimeDependenciesReady(
  project: string,
  onFailure?: (detail: string) => void,
): Promise<boolean> {
  let windowsRocm: boolean;
  try {
    windowsRocm = windowsRocmSelected();
  } catch (error) {
    // ROCm requested on a host that cannot run it (Windows ARM64): not ready,
    // with the reason, instead of a rejected readiness check.
    onFailure?.(error instanceof Error ? error.message : String(error));
    return false;
  }
  if (windowsRocm && !(await windowsRocmReady(project, onFailure))) return false;
  const env: NodeJS.ProcessEnv = { ...process.env };
  delete env.PYTHONHOME;
  delete env.PYTHONPATH;
  env.HF_HUB_OFFLINE = '1';
  env.TRANSFORMERS_OFFLINE = '1';
  env.PYTHONNOUSERSITE = '1';
  return new Promise((resolve, reject) => {
    const python = runtimePython(project);
    const complete = (error: unknown, stdout = '', stderr = '') => {
      const refusal = runtimeLaunchRefusal(python, error);
      if (refusal) {
        reject(refusal);
        return;
      }
      const ready = !error || (!windowsRocm && probeTimedOut(error));
      if (!ready) {
        const module = lastImportAttempted(stdout);
        const detail = probeFailureDetail(error, stderr);
        onFailure?.(
          module && !stderr.trim() ? `${detail} (importing ${module})` : detail,
        );
      }
      resolve(ready);
    };
    try {
      execFile(
        python,
        ['-c', (windowsRocm ? WINDOWS_ROCM_IMPORT_PREFIX : '') + RUNTIME_IMPORT_PROBE],
        {
          cwd: project,
          windowsHide: true,
          timeout: RUNTIME_PROBE_TIMEOUT_MS,
          maxBuffer: 256 * 1024,
          env,
        },
        complete,
      );
    } catch (error) {
      const refusal = runtimeLaunchRefusal(python, error);
      if (refusal) reject(refusal);
      else if ((error as NodeJS.ErrnoException)?.code === 'ENOENT') complete(error);
      else reject(error);
    }
  });
}

/**
 * What the failed native import was actually caused by.
 *
 * `extensionPresent` tells a missing wheel apart from a wheel that is there
 * but cannot be loaded; `missingSystemLibraries` names the system libraries
 * the extension links against that this host does not have.
 */
export interface NativeRuntimeDiagnosis {
  /** System libraries the torch extensions need that this host cannot load. */
  missingSystemLibraries: string[];
  /** The torchaudio native extension exists in the environment. */
  extensionPresent: boolean;
  /** The `MSVCP140.dll` the interpreter loads, when Windows could report it. */
  msvcp140?: { path: string; version: string } | null;
}

const EMPTY_DIAGNOSIS: NativeRuntimeDiagnosis = {
  missingSystemLibraries: [],
  extensionPresent: true,
  msvcp140: null,
};

/**
 * Report what a failed `import torchaudio` was caused by.
 *
 * The check runs in the environment's own interpreter, so it sees the same
 * DLL search path the backend will use. Windows-only questions (the Visual
 * C++ runtime) are answered there; every other platform reports nothing
 * missing and keeps the reinstall path.
 */
export const RUNTIME_NATIVE_DIAGNOSTIC_PROBE = [
  'import ctypes, json, os, sys, sysconfig',
  `libraries = ${JSON.stringify([...WINDOWS_TORCH_SYSTEM_LIBRARIES])}`,
  'missing = []',
  'msvcp = None',
  "if sys.platform == 'win32':",
  '    for name in libraries:',
  '        try:',
  '            ctypes.CDLL(name)',
  '        except OSError:',
  '            missing.append(name)',
  '    purelib = sysconfig.get_paths()["purelib"]',
  '    extension = os.path.join(purelib, "torchaudio", "lib", "libtorchaudio.pyd")',
  '    present = os.path.isfile(extension)',
  // Which MSVCP140.dll this interpreter actually loads, and its file version:
  // an outdated system copy crashes newer C++ wheels without a traceback (#3293).
  '    try:',
  '        from ctypes import wintypes',
  '        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)',
  '        kernel32.GetModuleFileNameW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p, wintypes.DWORD]',
  '        path_buffer = ctypes.create_unicode_buffer(32768)',
  '        if kernel32.GetModuleFileNameW(ctypes.WinDLL("msvcp140.dll")._handle, path_buffer, 32768):',
  '            path = path_buffer.value',
  '            version_dll = ctypes.WinDLL("version")',
  '            size = version_dll.GetFileVersionInfoSizeW(path, None)',
  '            data = ctypes.create_string_buffer(size)',
  '            block = ctypes.c_void_p()',
  '            length = wintypes.UINT()',
  '            if size and version_dll.GetFileVersionInfoW(path, 0, size, data) and version_dll.VerQueryValueW(data, "\\\\", ctypes.byref(block), ctypes.byref(length)):',
  '                fixed = ctypes.cast(block, ctypes.POINTER(wintypes.DWORD * 4)).contents',
  '                msvcp = {"path": path, "version": "%d.%d.%d.%d" % (fixed[2] >> 16, fixed[2] & 0xFFFF, fixed[3] >> 16, fixed[3] & 0xFFFF)}',
  '    except Exception:',
  '        msvcp = None',
  'else:',
  '    present = True',
  `print(${JSON.stringify(NATIVE_DIAGNOSTIC_PREFIX)} + json.dumps({"missing_system_libraries": missing, "extension_present": present, "msvcp140": msvcp}))`,
].join('\n');

/** Read the diagnostic marker out of the probe's output. */
export function parseNativeDiagnosis(output: string | void): NativeRuntimeDiagnosis | null {
  if (!output) return null;
  const line = output
    .split(/\r?\n/)
    .findLast((candidate) => candidate.startsWith(NATIVE_DIAGNOSTIC_PREFIX));
  if (!line) return null;
  try {
    const parsed = JSON.parse(line.slice(NATIVE_DIAGNOSTIC_PREFIX.length)) as {
      missing_system_libraries?: unknown;
      extension_present?: unknown;
      msvcp140?: { path?: unknown; version?: unknown } | null;
    };
    const msvcp = parsed.msvcp140;
    return {
      missingSystemLibraries: Array.isArray(parsed.missing_system_libraries)
        ? parsed.missing_system_libraries.filter(
            (name): name is string => typeof name === 'string' && name.length > 0,
          )
        : [],
      extensionPresent: parsed.extension_present !== false,
      msvcp140:
        msvcp && typeof msvcp.path === 'string' && typeof msvcp.version === 'string'
          ? { path: msvcp.path, version: msvcp.version }
          : null,
    };
  } catch {
    return null;
  }
}

/**
 * Ask the environment why its native extensions would not load.
 *
 * A diagnostic that cannot run is not evidence of anything: the caller then
 * keeps the reinstall path, which is the safe default.
 */
export async function diagnoseNativeRuntime(
  project: string,
  run: RuntimeRunner,
): Promise<NativeRuntimeDiagnosis> {
  let output: string | void;
  try {
    output = await run(runtimePython(project), ['-c', RUNTIME_NATIVE_DIAGNOSTIC_PROBE], project);
  } catch {
    return EMPTY_DIAGNOSIS;
  }
  return parseNativeDiagnosis(output) ?? EMPTY_DIAGNOSIS;
}

/** The error a missing system library produces: nothing to reinstall, a fix to apply. */
export function missingSystemLibraryError(libraries: readonly string[]): Error {
  return Object.assign(
    new Error(
      `This Windows system is missing ${libraries.join(', ')}, which the PyTorch audio extension links ` +
        'against. Install the Microsoft Visual C++ 2015-2022 Redistributable (x64) and restart ' +
        'VoiceStudio; reinstalling the Python packages cannot supply a system library.',
    ),
    { code: MISSING_SYSTEM_LIBRARY_CODE, missingSystemLibraries: [...libraries] },
  );
}

/** True when a reported `MSVCP140.dll` version predates {@link MIN_MSVCP140_VERSION}. */
export function msvcp140Outdated(version: string | undefined | null): boolean {
  const [major, minor] = String(version ?? '')
    .split('.')
    .map((part) => Number.parseInt(part, 10));
  if (!Number.isFinite(major) || !Number.isFinite(minor)) return false;
  const [minMajor, minMinor] = MIN_MSVCP140_VERSION;
  return major < minMajor || (major === minMajor && minor < minMinor);
}

/**
 * The outdated Visual C++ runtime behind a native crash, if that is what it was.
 *
 * Only an access violation is attributed to it: an ordinary ImportError on a
 * host with an old runtime has some other cause, and blaming the runtime would
 * send the user to the wrong fix.
 */
export function outdatedSystemLibraryError(
  failure: unknown,
  diagnosis: NativeRuntimeDiagnosis,
): Error | null {
  const code = (failure as { code?: unknown } | null)?.code;
  if (!ACCESS_VIOLATION_EXIT_CODES.has(code)) return null;
  const msvcp = diagnosis.msvcp140;
  if (!msvcp || !msvcp140Outdated(msvcp.version)) return null;
  const module = lastImportAttempted((failure as { stdout?: unknown }).stdout);
  return Object.assign(
    new Error(
      `${module ? `Importing "${module}" crashed` : 'A native import crashed'} because this Windows ` +
        `system's Visual C++ runtime is outdated (${scrubText(msvcp.path)} is version ${msvcp.version}; ` +
        `${MIN_MSVCP140_VERSION.join('.')} or newer is needed). Install the latest Microsoft Visual C++ ` +
        'Redistributable (x64) and press Retry; reinstalling the Python packages cannot update a system library.',
    ),
    { code: OUTDATED_SYSTEM_LIBRARY_CODE, stdout: (failure as { stdout?: unknown }).stdout },
  );
}

export async function runtimeReady(bundle: string, project: string): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  try {
    return (
      (await stat(runtimePython(project))).isFile() &&
      (await stat(join(project, '.venv', 'pyvenv.cfg'))).isFile() &&
      (await acceptedStamps(bundle)).includes(
        await readFile(join(project, RUNTIME_READY_MARKER), 'utf8'),
      ) &&
      (!windowsRocmSelected() ||
        ((await windowsRocmRecipeMatches(bundle, project)) && (await windowsRocmReady(project))))
    );
  } catch (error) {
    if (error instanceof RuntimeLaunchRefusedError) throw error;
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
        readFile(join(project, RUNTIME_READY_MARKER), 'utf8').catch(
          (error: NodeJS.ErrnoException) => {
            if (error.code === 'ENOENT') return null;
            throw error;
          },
        ),
      ]);
    return (
      python.isFile() &&
      config.isFile() &&
      bundledProject.equals(installedProject) &&
      // The install may have staged the lock with a regional PyPI mirror (#3444):
      // the same pinned files, so compare with hosts normalized.
      (bundledLock.equals(installedLock) ||
        normalizeLockHosts(bundledLock.toString('utf8')) ===
          normalizeLockHosts(installedLock.toString('utf8'))) &&
      (marker === null
        ? // A Tauri environment carries the lock's default (CUDA) torch: fine for
          // inferred CPU hosts, wrong for an explicit ROCm or CPU request.
          !resolveTorchVariant().explicit || resolveTorchVariant().variant === 'default'
        : (await acceptedStamps(bundle)).includes(marker)) &&
      (!windowsRocmSelected() ||
        ((await windowsRocmRecipeMatches(bundle, project)) && (await windowsRocmReady(project))))
    );
  } catch (error) {
    if (error instanceof RuntimeLaunchRefusedError) throw error;
    return false;
  }
}

const LOCKED = new Set(['EPERM', 'EACCES', 'EBUSY']);
const isLocked = (error: unknown) =>
  LOCKED.has((error as NodeJS.ErrnoException | null)?.code ?? '');
/** Error code for "a runtime folder stayed locked by another program through every retry". */
export const RUNTIME_FILES_LOCKED_CODE = 'RUNTIME_FILES_LOCKED';

/** The filesystem calls staging needs; injectable so lock handling is testable. */
export interface StageFileSystem {
  cp: typeof cp;
  rm: typeof rm;
}
export interface StageOptions {
  fs?: StageFileSystem;
  /** Retry schedule for locked copy/remove steps (renames use `renameDirectory`'s own). */
  retry?: RenameRetryOptions;
}

/** Mirror `source` onto `target` file by file, for a folder Windows will not rename
 *  (a scanner, OneDrive or a lingering backend holds a handle inside it, #3010, #2669).
 *  Files can be overwritten where a folder rename cannot; files the new bundle no
 *  longer ships are pruned best-effort. */
async function mirrorInPlace(source: string, target: string, fs: StageFileSystem): Promise<void> {
  await fs.cp(source, target, { recursive: true, force: true });
  const walk = async (rel: string): Promise<void> => {
    for (const entry of await readdir(join(target, rel), { withFileTypes: true })) {
      const next = join(rel, entry.name);
      const kept = await stat(join(source, next)).then(
        () => true,
        () => false,
      );
      if (!kept) await fs.rm(join(target, next), { recursive: true, force: true }).catch(() => {});
      else if (entry.isDirectory()) await walk(next);
    }
  };
  await walk('').catch(() => {});
}

/** A lock that outlasted every retry and the in-place fallback: say what holds it and what to do. */
function runtimeFilesLockedError(target: string, cause: unknown): Error {
  return Object.assign(
    new Error(
      `Windows would not let VoiceStudio update ${target} because another program has a file in it ` +
        'open (OneDrive or another sync app, antivirus, or a VoiceStudio backend that is still ' +
        'running). Close that program or pause syncing, then press Retry. ' +
        `(${String((cause as Error | null)?.message ?? cause)})`,
    ),
    { code: RUNTIME_FILES_LOCKED_CODE, cause },
  );
}

/**
 * Replace only bundled code. The interpreter, models and user data are never removed.
 *
 * Every step a Windows file lock can block is retried with backoff, and a folder
 * that still cannot be swapped is updated in place file by file (#3409, #3010).
 */
export async function stageRuntimeSources(
  bundle: string,
  project: string,
  renameDirectory: typeof renameWithRetry = renameWithRetry,
  { fs = { cp, rm }, retry = DIRECTORY_RENAME }: StageOptions = {},
): Promise<void> {
  const persist = <T>(action: () => Promise<T>) => retryTransient(action, retry);
  const discard = (path: string) =>
    persist(() => fs.rm(path, { recursive: true, force: true })).then(
      () => true,
      (error: unknown) => {
        if (!isLocked(error)) throw error;
        return false;
      },
    );
  await mkdir(project, { recursive: true });
  for (const name of SOURCES) {
    const target = join(project, name);
    await mkdir(dirname(target), { recursive: true });
    const token = name.replaceAll('/', '-');
    // A locked leftover from an earlier attempt is skipped, never merged into.
    let pending = join(project, `.incoming-${token}`);
    if (!(await discard(pending))) pending = `${pending}-${randomUUID().slice(0, 8)}`;
    const previous = join(project, `.previous-${token}`);
    try {
      await persist(() =>
        fs.cp(join(bundle, name), pending, {
          recursive: true,
          force: true,
          filter: (source) => !source.split(/[\\/]/).includes('__pycache__'),
        }),
      );
      let swapped = false;
      // A locked `.previous-*` cannot receive the old folder: update in place instead.
      if (await discard(previous)) {
        try {
          let moved = false;
          try {
            await renameDirectory(target, previous, DIRECTORY_RENAME);
            moved = true;
          } catch (error) {
            if ((error as NodeJS.ErrnoException).code !== 'ENOENT') throw error;
          }
          try {
            await renameDirectory(pending, target, DIRECTORY_RENAME);
          } catch (error) {
            if (moved) await renameDirectory(previous, target, DIRECTORY_RENAME);
            throw error;
          }
          swapped = true;
        } catch (error) {
          if (!isLocked(error)) throw error;
        }
      }
      if (!swapped) await persist(() => mirrorInPlace(pending, target, fs));
    } catch (error) {
      // Only Windows file locks get the OneDrive/antivirus guidance; elsewhere
      // EPERM/EACCES is a real permission problem with its own guidance.
      if (isLocked(error) && (retry.platform ?? process.platform) === 'win32') {
        throw runtimeFilesLockedError(target, error);
      }
      throw error;
    } finally {
      await fs.rm(pending, { recursive: true, force: true }).catch(() => {});
      await fs.rm(previous, { recursive: true, force: true }).catch(() => {});
    }
  }
}

/**
 * Run the frozen sync with the staged lock's PyPI artifact URLs pointed at a
 * regional mirror (#3444). The mirror serves the same files, and uv checks each
 * one against the pinned hash. Every outcome restores the bundled lock, and a
 * failed mirrored sync is retried once from the original URLs: finished wheels
 * stay in uv's cache, so the retry only fetches what the mirror could not.
 */
export interface MirrorReporter {
  /** A setup log line. */
  log?: (line: string) => void;
  /** The host PyPI-hosted wheels download from changed (fallback to the origin). */
  pypiHost?: (host: string) => void;
  /** Byte progress of a wheel the shell downloads itself (#3411). */
  transfer?: (name: string, received: number, total: number) => void;
}

export async function syncLockedWithMirror(
  project: string,
  mirror: string | null,
  sync: () => Promise<unknown>,
  signal: AbortSignal,
  { log = () => {}, pypiHost = () => {} }: MirrorReporter = {},
  /** Locked artifact URL to the `file://` copy already downloaded and verified (#3411). */
  localUrls: ReadonlyMap<string, string> = new Map(),
): Promise<void> {
  const lockPath = join(project, 'uv.lock');
  if (!mirror && !localUrls.size) {
    await sync();
    return;
  }
  const original = await readFile(lockPath, 'utf8');
  const local = localizeLockUrls(original, localUrls);
  const mirrored = mirror ? mirrorLockUrls(local, mirror) : local;
  if (mirrored === original) {
    await sync();
    return;
  }
  const origin = new URL(PYPI_FILES_ORIGIN).host;
  try {
    await writeFile(lockPath, mirrored);
    if (mirror && mirrored !== local) {
      log(
        `PyPI downloads: ${new URL(mirror).host} (regional mirror of ${origin}). ` +
          'PyTorch wheels still download from their own index; it has no mirror.',
      );
      pypiHost(new URL(mirror).host);
      try {
        await sync();
        return;
      } catch (error) {
        if (signal.aborted) throw error;
        log(`Mirror download failed (${errorSummary(error)}); retrying from ${origin}.`);
        pypiHost(origin);
      }
      await writeFile(lockPath, local);
    }
    try {
      await sync();
    } catch (error) {
      // uv could not use a prefetched copy (removed or locked by an antivirus
      // after it was verified): let uv download everything itself (#3411).
      if (signal.aborted || !localUrls.size) throw error;
      log(
        `Install from the pre-downloaded wheels failed (${errorSummary(error)}); letting uv download them.`,
      );
      await writeFile(lockPath, original);
      await sync();
    }
  } finally {
    await writeFile(lockPath, original);
  }
}

/** Where verified wheels wait for uv between attempts; outside the replaceable project. */
export function runtimeWheelPrefetchDir(project: string): string {
  return join(project, '..', '.wheel-prefetch');
}

/**
 * Download the large wheels this sync needs over several connections first
 * (#3411). uv's own dry run names the wheels it would download (cache hits
 * and installed packages excluded); anything unexpected returns an empty map
 * and leaves every download to uv.
 */
async function prefetchLockedWheels(
  uv: string,
  project: string,
  syncArgs: readonly string[],
  env: NodeJS.ProcessEnv,
  run: RuntimeRunner,
  signal: AbortSignal,
  mirror: string | null,
  reserveBytes: number,
  { log = () => {}, transfer }: MirrorReporter,
): Promise<Map<string, string>> {
  const connections = prefetchConnections();
  if (connections < 2) return new Map();
  let output: string | void;
  try {
    output = await run(uv, ['-v', ...syncArgs, '--dry-run'], project, env, {
      timeoutMs: 300_000,
      quiet: true,
    });
  } catch {
    signal.throwIfAborted();
    return new Map();
  }
  signal.throwIfAborted();
  const managedMinor = /3\.(\d+)/.exec(managedPythonRequest())?.[1];
  const plan = planWheelPrefetch(
    await readFile(join(project, 'uv.lock'), 'utf8'),
    String(output ?? ''),
    {
      platform: process.platform,
      arch: process.arch,
      pythonMinor: managedMinor ? Number(managedMinor) : undefined,
    },
  );
  if (!plan.length) return new Map();
  const { transport, probeSize, close } = httpsRangeTransport(env);
  try {
    return await prefetchWheels(plan, {
      dir: runtimeWheelPrefetchDir(project),
      connections,
      transport,
      probeSize,
      signal,
      mirror,
      reserveBytes,
      log,
      transfer,
    });
  } finally {
    close();
  }
}

/**
 * "Auto" picks a regional PyPI mirror only when a one-wheel speed check finds
 * it clearly faster than files.pythonhosted.org (#3444); otherwise PyPI.
 */
async function autoPypiMirror(
  project: string,
  signal: AbortSignal,
  { log = () => {} }: MirrorReporter,
): Promise<string | null> {
  const lock = await readFile(join(project, 'uv.lock'), 'utf8').catch(() => '');
  const race = await raceAutoPypiMirror(lock, signal);
  signal.throwIfAborted();
  const ms = (value: number | null) => (value === null ? 'no answer' : `${Math.round(value)} ms`);
  if (race.originMs !== null || race.mirrorMs !== null) {
    log(
      `PyPI speed check: ${new URL(PYPI_FILES_ORIGIN).host} ${ms(race.originMs)}, ` +
        `${new URL(AUTO_PYPI_MIRROR).host} ${ms(race.mirrorMs)}; ` +
        `using ${new URL(race.mirror ?? PYPI_FILES_ORIGIN).host}.`,
    );
  }
  return race.mirror;
}

function errorSummary(error: unknown): string {
  const message = error instanceof Error ? error.message : String(error);
  return message.split(/\r?\n/)[0]!.slice(0, 200);
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
  downloads: MirrorReporter = {},
): Promise<void> {
  signal.throwIfAborted();
  phase('checking');
  if (process.platform === 'darwin' && process.arch === 'x64') {
    throw Object.assign(new Error('INTEL_MAC_UNSUPPORTED'), { code: 'INTEL_MAC_UNSUPPORTED' });
  }
  await mkdir(project, { recursive: true });
  // A cancelled attempt leaves its unfinished wheels behind; uv cannot resume them.
  // Drop them before measuring free space, or they can block the retry that would free it.
  await pruneInterruptedUvDownloads(runtimeUvCache(project));
  const torch = resolveTorchVariant();
  const windowsRocm = windowsRocmSelected(torch.variant);
  const requiredBytes =
    torch.variant === 'cpu' || process.platform === 'darwin'
      ? REQUIRED_CPU_ENV_BYTES
      : REQUIRED_ENV_BYTES;
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
  // Read before this attempt rewrites the marker: a resume must reuse what the
  // interrupted attempt already downloaded (#3475).
  const resuming = await runtimeInstallInterrupted(project);
  // Non-empty content so every attempt moves the marker's mtime, which is what
  // tells a finished install (ready marker newer) from an interrupted one.
  await writeFile(join(project, RUNTIME_INSTALLING_MARKER), new Date().toISOString());
  await rm(join(project, RUNTIME_READY_MARKER), { force: true });
  await stageRuntimeSources(bundle, project);
  await promoteLegacyRuntimeCaches(project);
  // A promoted legacy cache can carry interrupted downloads of its own.
  await pruneInterruptedUvDownloads(runtimeUvCache(project));
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
  // #2971: uv and pip unpack wheels into the temp dir. Left at the system default
  // it fills the system drive even when the runtime was placed on another one.
  const downloadRegion = await effectiveRegion(region, signal);
  const scratch = join(project, '..', '.tmp');
  await mkdir(scratch, { recursive: true });
  const env: NodeJS.ProcessEnv = {
    ...downloadProxyEnv(),
    ...runtimeDownloadEnv(downloadRegion),
    UV_PROJECT_ENVIRONMENT: join(project, '.venv'),
    // Keep immutable downloads beside the replaceable project. Clean & Retry can
    // rebuild a broken venv without paying the multi-gigabyte transfer twice.
    UV_CACHE_DIR: runtimeUvCache(project),
    UV_PYTHON_INSTALL_DIR: join(project, '..', '.python'),
    TEMP: scratch,
    TMP: scratch,
    TMPDIR: scratch,
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
  const { existingPython, repairPackages, missingSystemLibraries } = interpreterExists
    ? await inspectRuntimeInterpreter(
        runtimePython(project),
        project,
        run,
        signal,
        windowsRocm,
        managedPythonRequest() === WIN_ARM64_PYTHON_REQUEST,
        RUNTIME_IMPORT_PROBE,
        RUNTIME_NATIVE_IMPORT_PROBE,
        RUNTIME_REPAIR_PACKAGES,
        () => diagnoseNativeRuntime(project, run),
      )
    : {
        existingPython: false,
        repairPackages: [] as string[],
        missingSystemLibraries: [] as string[],
      };
  if (missingSystemLibraries.length) {
    // The wheels are fine; this Windows system is missing a library they
    // link against. Say so instead of downloading gigabytes that cannot
    // supply a system file (#3058).
    throw missingSystemLibraryError(missingSystemLibraries);
  }
  const pythonArgs = existingPython
    ? ['--python', runtimePython(project)]
    : ['--managed-python', '--python', managedPythonRequest()];
  const cpuTorch = torch.variant === 'cpu';
  // A failed native import may leave distribution metadata intact, so uv's
  // ordinary sync would otherwise consider the broken wheel already satisfied.
  const torchPackages: readonly string[] = RUNTIME_REPAIR_PACKAGES;
  // A CPU install never syncs the lock's CUDA torch, so its repair goes through
  // the CPU pip install below instead of `uv sync --reinstall-package`.
  const repairArgs = repairPackages
    .filter((name) => !cpuTorch || !torchPackages.includes(name))
    .flatMap((name) => ['--reinstall-package', name]);
  signal.throwIfAborted();
  if (repairPackages.length && !resuming) {
    // uv may hardlink installed files to its unpacked wheel cache. A corrupted
    // native file can therefore poison the cached copy too; evict only this
    // package from the app-private cache before reinstalling its locked wheel.
    // Not on a resume: there the import fails because the interrupted attempt
    // never finished installing, and uv only adds finished wheels to its cache,
    // so evicting would throw away gigabytes already downloaded (#3475).
    await run(uv, ['cache', 'clean', ...repairPackages], project, env);
    signal.throwIfAborted();
  }
  // The lock resolves torch to the CUDA build (and, on Linux, ~3 GB of nvidia-*
  // runtime wheels) for every Linux/Windows x64 host. Keep those out of a CPU
  // install; the CPU wheels are laid down right after the frozen sync.
  const skipArgs = windowsRocm
    ? windowsRocmSyncArgs(await readFile(join(project, 'uv.lock'), 'utf8'))
    : cpuTorch
      ? [
          // Exact sync uninstalls every skipped package, so each Resume or
          // Repair removed the CPU torch it then had to lay down again (#3475).
          // Only keep what a CPU install laid down: switching from a CUDA
          // runtime must still remove its multi-GB CUDA wheels.
          ...((await readFile(join(project, RUNTIME_TORCH_MARKER), 'utf8').catch(() => '')) ===
          'cpu'
            ? ['--inexact']
            : []),
          ...[
            ...torchPackages,
            ...cudaOnlyPackages(await readFile(join(project, 'uv.lock'), 'utf8')),
          ].flatMap((name) => ['--no-install-package', name]),
        ]
      : [];
  const syncArgs = ['sync', '--frozen', '--no-dev', ...pythonArgs, ...repairArgs, ...skipArgs];
  const mirror =
    region === 'auto'
      ? await autoPypiMirror(project, signal, downloads)
      : pypiFilesMirror(downloadRegion);
  const localUrls = await prefetchLockedWheels(
    uv,
    project,
    syncArgs,
    env,
    run,
    signal,
    mirror,
    requiredBytes,
    downloads,
  );
  await syncLockedWithMirror(
    project,
    mirror,
    () => run(uv!, syncArgs, project, env),
    signal,
    downloads,
    localUrls,
  );
  signal.throwIfAborted();
  // The sync succeeded, so uv has every wheel now: prefetched copies and any
  // resume parts left by a failed segmented fetch only take space.
  await clearPrefetchedWheels(runtimeWheelPrefetchDir(project));
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
  if (windowsRocm) {
    await run(runtimePython(project), ['-m', WINDOWS_ROCM_MODULE, '--uv', uv], project, env);
    signal.throwIfAborted();
  } else if (torch.variant === 'rocm') {
    await run(
      uv,
      [
        'pip',
        'install',
        '--python',
        runtimePython(project),
        ...rocmTorchInstallArgs(process.env.OMNIVOICE_TORCH_INDEX || ROCM_TORCH_INDEX),
      ],
      project,
      env,
    );
    signal.throwIfAborted();
  }
  await ensureCudnn8Compat(uv, project, run, env, signal, torch.variant);
  signal.throwIfAborted();
  // A non-English profile path in uv's editable .pth crashes Python 3.11 at
  // startup on a non-UTF-8 Windows code page (#1783).
  await asciiSafePthFiles(join(project, '.venv')).catch(() => []);
  phase('verifying');
  if (windowsRocm) {
    await run(
      runtimePython(project),
      ['-c', WINDOWS_ROCM_IMPORT_PREFIX + RUNTIME_IMPORT_PROBE],
      project,
      env,
      { timeoutMs: WINDOWS_ROCM_IMPORT_TIMEOUT_MS },
    );
    await run(runtimePython(project), ['-m', WINDOWS_ROCM_MODULE, '--verify-only'], project, env);
  } else {
    try {
      await run(runtimePython(project), ['-c', RUNTIME_IMPORT_PROBE], project);
    } catch (error) {
      // A fresh install on a Windows system without the Visual C++ runtime
      // cannot verify either, and no wheel can supply the missing file (#3058).
      const diagnosis = await diagnoseNativeRuntime(project, run);
      if (diagnosis.missingSystemLibraries.length) {
        throw missingSystemLibraryError(diagnosis.missingSystemLibraries);
      }
      // An outdated system Visual C++ runtime crashes newer C++ wheels such as
      // sentencepiece 0.2.2 with an access violation; say so and how to fix it (#3293).
      throw outdatedSystemLibraryError(error, diagnosis) ?? importFailure(error);
    }
    if (torch.variant === 'rocm') {
      // With an untagged OMNIVOICE_TORCH_INDEX mirror the bare pins can resolve
      // to ordinary wheels; never mark such a runtime ROCm-ready.
      try {
        await run(runtimePython(project), ['-c', ROCM_TORCH_PROBE], project);
      } catch {
        throw new Error(ROCM_TORCH_PROBE_FAILURE);
      }
    }
  }
  signal.throwIfAborted();
  await writeFile(
    join(project, RUNTIME_TORCH_MARKER),
    windowsRocm ? 'windows-rocm' : torch.variant,
  );
  // The ready marker is written last and is authoritative: it supersedes the
  // install marker even if removing that marker below fails (#3475).
  await writeFile(
    join(project, RUNTIME_READY_MARKER),
    await dependencyStamp(bundle, torch.variant),
  );
  // Windows briefly locks a freshly written file while antivirus or the indexer
  // scans it; retry instead of failing a finished install on that lock.
  await rm(join(project, RUNTIME_INSTALLING_MARKER), {
    force: true,
    maxRetries: 5,
    retryDelay: 200,
  }).catch(() => undefined);
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
    await renameWithRetry(legacy, shared, DIRECTORY_RENAME);
  }
}
