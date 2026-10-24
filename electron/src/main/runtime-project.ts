import { nvidiaDriverPresent } from '../../scripts/torch-variant.mjs';
export { nvidiaDriverPresent } from '../../scripts/torch-variant.mjs';
import { clearCtranslate2ExecutableStack } from '../../scripts/native-compat.mjs';
export { clearCtranslate2ExecutableStack } from '../../scripts/native-compat.mjs';
import { downloadProxyEnv } from './proxy-env';
import { DIRECTORY_RENAME, renameWithRetry } from './rename-retry';
import { downloadRuntimeInstaller } from './runtime-download';
import { asciiSafePthFiles } from './pth-ascii';
import { scrubText } from '../shared/utils/scrub';
import { execFile } from 'node:child_process';
import { createHash, randomUUID } from 'node:crypto';
import { cp, mkdir, readFile, readdir, rm, stat, statfs, writeFile } from 'node:fs/promises';
import { join } from 'node:path';

// The web UI is not staged: the backend serves it straight from the app's
// resources via OMNIVOICE_FRONTEND_DIST, so it always matches this version.
const SOURCES = [
  'backend',
  'config',
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
/**
 * Whether this OS can have the ROCm torch stack at all.
 *
 * PyTorch's ROCm index (`ROCM_TORCH_INDEX`) publishes Linux wheels only. On
 * Windows `uv pip install --index-url <rocm6.4> torch==2.8.0` finds nothing and
 * the whole runtime bootstrap fails, leaving the app unusable instead of merely
 * CPU-bound - so the opt-in is ignored there and the host resolves like any
 * other (Settings > Performance explains which engines can still use a Radeon).
 * Windows ROCm needs AMD's own wheels and a validated engine matrix; it is
 * deliberately not wired into this recipe.
 */
export function rocmTorchApplies(platform: NodeJS.Platform = process.platform): boolean {
  return platform !== 'win32' && platform !== 'darwin';
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
  // ROCm wheels exist for Linux only; elsewhere the opt-in is ignored (the
  // host is resolved like any other, never a failed bootstrap).
  if (raw === 'rocm' && rocmTorchApplies(platform)) return { variant: 'rocm', explicit: true };
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
): string {
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

async function dependencyStamp(
  bundle: string,
  variant: TorchVariant = resolveTorchVariant().variant,
): Promise<string> {
  const hash = createHash('sha256');
  hash.update(RUNTIME_SCHEMA);
  if (variant === 'rocm') hash.update(':torch=rocm');
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
      (error, _stdout, stderr) => {
        const ready = !error || probeTimedOut(error);
        if (!ready) onFailure?.(probeFailureDetail(error, stderr));
        resolve(ready);
      },
    );
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
}

const EMPTY_DIAGNOSIS: NativeRuntimeDiagnosis = {
  missingSystemLibraries: [],
  extensionPresent: true,
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
  "if sys.platform == 'win32':",
  '    for name in libraries:',
  '        try:',
  '            ctypes.CDLL(name)',
  '        except OSError:',
  '            missing.append(name)',
  '    purelib = sysconfig.get_paths()["purelib"]',
  '    extension = os.path.join(purelib, "torchaudio", "lib", "libtorchaudio.pyd")',
  '    present = os.path.isfile(extension)',
  'else:',
  '    present = True',
  `print(${JSON.stringify(NATIVE_DIAGNOSTIC_PREFIX)} + json.dumps({"missing_system_libraries": missing, "extension_present": present}))`,
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
    };
    return {
      missingSystemLibraries: Array.isArray(parsed.missing_system_libraries)
        ? parsed.missing_system_libraries.filter(
            (name): name is string => typeof name === 'string' && name.length > 0,
          )
        : [],
      extensionPresent: parsed.extension_present !== false,
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

export async function runtimeReady(bundle: string, project: string): Promise<boolean> {
  if (await runtimeIncomplete(project)) return false;
  try {
    return (
      (await stat(runtimePython(project))).isFile() &&
      (await stat(join(project, '.venv', 'pyvenv.cfg'))).isFile() &&
      (await acceptedStamps(bundle)).includes(
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
      (marker === null
        ? // A Tauri environment carries the lock's default (CUDA) torch: fine for
          // inferred CPU hosts, wrong for an explicit ROCm or CPU request.
          !resolveTorchVariant().explicit || resolveTorchVariant().variant === 'default'
        : (await acceptedStamps(bundle)).includes(marker))
    );
  } catch {
    return false;
  }
}

const LOCKED = new Set(['EPERM', 'EACCES', 'EBUSY']);

/** Mirror `source` onto `target` file by file, for a folder Windows will not rename
 *  (a scanner, OneDrive or a lingering backend holds a handle inside it, #3010, #2669).
 *  Files can be overwritten where a folder rename cannot; files the new bundle no
 *  longer ships are pruned best-effort. */
async function mirrorInPlace(source: string, target: string): Promise<void> {
  await cp(source, target, { recursive: true, force: true });
  const walk = async (rel: string): Promise<void> => {
    for (const entry of await readdir(join(target, rel), { withFileTypes: true })) {
      const next = join(rel, entry.name);
      const kept = await stat(join(source, next)).then(
        () => true,
        () => false,
      );
      if (!kept) await rm(join(target, next), { recursive: true, force: true }).catch(() => {});
      else if (entry.isDirectory()) await walk(next);
    }
  };
  await walk('').catch(() => {});
}

/** Replace only bundled code. The interpreter, models and user data are never removed. */
export async function stageRuntimeSources(
  bundle: string,
  project: string,
  renameDirectory: typeof renameWithRetry = renameWithRetry,
): Promise<void> {
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
    } catch (error) {
      if (!LOCKED.has((error as NodeJS.ErrnoException).code ?? '')) throw error;
      await mirrorInPlace(pending, target);
    }
    await rm(pending, { recursive: true, force: true }).catch(() => {});
    await rm(previous, { recursive: true, force: true }).catch(() => {});
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
  if (process.platform === 'darwin' && process.arch === 'x64') {
    throw Object.assign(new Error('INTEL_MAC_UNSUPPORTED'), { code: 'INTEL_MAC_UNSUPPORTED' });
  }
  await mkdir(project, { recursive: true });
  const torch = resolveTorchVariant();
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
  // #2971: uv and pip unpack wheels into the temp dir. Left at the system default
  // it fills the system drive even when the runtime was placed on another one.
  const scratch = join(project, '..', '.tmp');
  await mkdir(scratch, { recursive: true });
  const env: NodeJS.ProcessEnv = {
    ...downloadProxyEnv(),
    ...(await runtimeDownloadEnv(region, signal)),
    UV_PROJECT_ENVIRONMENT: join(project, '.venv'),
    // Keep immutable downloads beside the replaceable project. Clean & Retry can
    // rebuild a broken venv without paying the multi-gigabyte transfer twice.
    UV_CACHE_DIR: join(project, '..', '.uv-cache'),
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
  let existingPython = false;
  const repairPackages: string[] = [];
  // System libraries the native extensions need that this host does not have.
  // Collected here and thrown after the interpreter probe, because the outer
  // catch below deliberately swallows any failure of a suspect interpreter.
  let missingSystemLibraries: string[] = [];
  if (interpreterExists) {
    try {
      await run(
        runtimePython(project),
        [
          '-c',
          'import sys; assert sys.version_info[:2] == (3, 11)' +
            // A native ARM64 interpreter cannot install torchaudio/torchvision.
            (managedPythonRequest() === WIN_ARM64_PYTHON_REQUEST
              ? "; import platform; assert platform.machine().upper() in ('AMD64', 'X86_64')"
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
          // A missing SYSTEM library is a different class: no wheel can
          // supply it, so reinstalling gigabytes cannot fix it (#3058).
          const diagnosis = await diagnoseNativeRuntime(project, run);
          missingSystemLibraries = diagnosis.missingSystemLibraries;
          if (!missingSystemLibraries.length) {
            repairPackages.push(...RUNTIME_REPAIR_PACKAGES);
          }
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
  if (repairPackages.length) {
    // uv may hardlink installed files to its unpacked wheel cache. A corrupted
    // native file can therefore poison the cached copy too; evict only this
    // package from the app-private cache before reinstalling its locked wheel.
    await run(uv, ['cache', 'clean', ...repairPackages], project, env);
    signal.throwIfAborted();
  }
  // The lock resolves torch to the CUDA build (and, on Linux, ~3 GB of nvidia-*
  // runtime wheels) for every Linux/Windows x64 host. Keep those out of a CPU
  // install; the CPU wheels are laid down right after the frozen sync.
  const skipArgs = cpuTorch
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
    await run(
      uv,
      [
        'pip',
        'install',
        '--reinstall',
        '--python',
        runtimePython(project),
        ...ROCM_TORCH_PINS,
        '--index-url',
        process.env.OMNIVOICE_TORCH_INDEX || ROCM_TORCH_INDEX,
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
  try {
    await run(runtimePython(project), ['-c', RUNTIME_IMPORT_PROBE], project);
  } catch (error) {
    // A fresh install on a Windows system without the Visual C++ runtime
    // cannot verify either, and no wheel can supply the missing file (#3058).
    const diagnosis = await diagnoseNativeRuntime(project, run);
    if (diagnosis.missingSystemLibraries.length) {
      throw missingSystemLibraryError(diagnosis.missingSystemLibraries);
    }
    throw error;
  }
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
    await renameWithRetry(legacy, shared, DIRECTORY_RENAME);
  }
}
