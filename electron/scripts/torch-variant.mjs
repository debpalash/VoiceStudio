import { execFile, execFileSync } from 'node:child_process';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

/**
 * Whether an NVIDIA driver is installed. File checks only (no subprocess), and
 * deliberately generous: a false positive costs one avoidable GPU-wheel
 * download, a false negative would silently strand a GPU host on CPU torch.
 */
export function nvidiaDriverPresent(
  platform = process.platform,
  exists = existsSync,
  env = process.env,
) {
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

/** Probe the driver before installing torch; no Python or GPU libraries needed. */
export async function hasNvidiaGpu({
  env = process.env,
  platform = process.platform,
  exists = existsSync,
  run = execFile,
} = {}) {
  if (nvidiaDriverPresent(platform, exists, env)) return true;
  return new Promise((resolve) => {
    run(
      'nvidia-smi',
      ['--query-gpu=name', '--format=csv,noheader'],
      { env, timeout: 5_000, windowsHide: true, maxBuffer: 64 * 1024 },
      (error, stdout) => resolve(!error && Boolean(stdout.trim())),
    );
  });
}

/**
 * PyTorch's CUDA 12.8+ builds ship no kernels below compute capability 7.0
 * (Maxwell/Pascal), and its CUDA 12.6 builds still cover sm_50 and up. A
 * legacy card therefore needs the cu126 wheels; Kepler (3.x) is in neither.
 */
export const LEGACY_CUDA_MIN_CC = 5.0;
export const LEGACY_CUDA_MAX_CC = 7.0;
/** CUDA 12.x minor-version compatibility starts at this driver branch (525.60 / 527.41). */
export const LEGACY_CUDA_MIN_DRIVER = 525;

/**
 * Decide from `nvidia-smi --query-gpu=compute_cap,driver_version --format=csv,noheader`
 * output whether every NVIDIA card is a pre-Volta one the cu126 wheels can drive.
 * Mixed hosts keep the default build: it drives the newest card, which cu126 may not
 * (Blackwell), and the older card is no worse off than before.
 */
export function legacyNvidiaFromSmi(output) {
  const rows = String(output || '')
    .split(/\r?\n/)
    .map((line) => line.trim())
    .filter(Boolean)
    .map((line) => line.split(',').map((part) => part.trim()));
  if (!rows.length) return { legacy: false, reason: 'no NVIDIA card reported' };
  const caps = rows.map(([cc]) => Number.parseFloat(cc));
  const drivers = rows.map(([, driver]) => Number.parseFloat(driver));
  if (caps.some((cc) => !Number.isFinite(cc))) {
    return { legacy: false, reason: 'compute capability not reported' };
  }
  const highest = Math.max(...caps);
  if (highest >= LEGACY_CUDA_MAX_CC) return { legacy: false, reason: 'a card is Volta or newer' };
  if (highest < LEGACY_CUDA_MIN_CC) {
    return { legacy: false, reason: `compute capability ${highest.toFixed(1)} is below 5.0` };
  }
  const driver = Math.min(...drivers);
  if (!Number.isFinite(driver) || driver < LEGACY_CUDA_MIN_DRIVER) {
    return {
      legacy: false,
      reason: `driver ${rows[0][1] ?? 'unknown'} is older than ${LEGACY_CUDA_MIN_DRIVER}`,
    };
  }
  return { legacy: true, reason: `compute capability ${highest.toFixed(1)}, driver ${driver}` };
}

let legacyProbeCache;
let legacyProbePending;
const SMI_ARGS = ['--query-gpu=compute_cap,driver_version', '--format=csv,noheader'];
const SMI_OPTIONS = { timeout: 5_000, windowsHide: true, encoding: 'utf8', maxBuffer: 64 * 1024 };
const SMI_FAILED = {
  legacy: false,
  reason: 'nvidia-smi could not report compute capability',
};

/**
 * Non-blocking probe for the main process: fills the cache the synchronous
 * `legacyNvidiaCard` reads, so startup never spawns `nvidia-smi` on the main
 * thread. Idempotent; never rejects. Call only when an NVIDIA driver is present.
 */
export function primeLegacyNvidiaProbe({ run = execFile, cache = true } = {}) {
  if (cache && legacyProbeCache) return Promise.resolve(legacyProbeCache);
  if (cache && legacyProbePending) return legacyProbePending;
  const pending = new Promise((resolve) => {
    try {
      run('nvidia-smi', SMI_ARGS, SMI_OPTIONS, (error, stdout) =>
        resolve(error ? SMI_FAILED : legacyNvidiaFromSmi(stdout)),
      );
    } catch {
      resolve(SMI_FAILED);
    }
  }).then((result) => {
    if (cache) legacyProbeCache = result;
    return result;
  });
  if (cache) legacyProbePending = pending;
  return pending;
}

/** Test hook: forget the cached probe. */
export function resetLegacyNvidiaProbe() {
  legacyProbeCache = undefined;
  legacyProbePending = undefined;
}

/** Reads the primed cache; spawns `nvidia-smi` synchronously only when never primed (scripts, tests). */
export function legacyNvidiaCard({ run = execFileSync, cache = true } = {}) {
  if (cache && legacyProbeCache) return legacyProbeCache;
  let result;
  try {
    const out = run('nvidia-smi', SMI_ARGS, SMI_OPTIONS);
    result = legacyNvidiaFromSmi(out);
  } catch {
    // Old nvidia-smi without compute_cap, or no binary: keep the default build.
    result = SMI_FAILED;
  }
  if (cache) legacyProbeCache = result;
  return result;
}

export function requestedTorchVariant(env = process.env) {
  const raw = (env.OMNIVOICE_TORCH_VARIANT || 'auto').trim().toLowerCase() || 'auto';
  const variant = raw === 'default' ? 'cuda' : raw;
  if (!['auto', 'cpu', 'cuda', 'rocm'].includes(variant)) {
    throw new Error('OMNIVOICE_TORCH_VARIANT must be auto, cpu, cuda, or rocm.');
  }
  return variant;
}

/** Explicit wheel choices win; Apple keeps its native MPS-capable distribution. */
export async function selectTorchVariant({
  env = process.env,
  platform = process.platform,
  arch = process.arch,
  nvidiaAvailable = () => hasNvidiaGpu({ env, platform }),
} = {}) {
  const requested = requestedTorchVariant(env);
  if (requested === 'rocm' && platform === 'win32') {
    if (arch !== 'x64') throw new Error('Windows ROCm requires Windows x64 Python 3.12 (cp312).');
    return 'rocm';
  }
  if (platform === 'darwin') return 'native';
  if (requested === 'rocm' && platform === 'linux') return 'rocm';
  const cpuWheelsApply =
    (platform === 'linux' && arch === 'x64') ||
    (platform === 'win32' && (arch === 'x64' || arch === 'arm64'));
  // Linux ARM keeps the lock's PyPI wheels, including explicit CPU requests.
  if (!cpuWheelsApply) return 'native';
  if (requested === 'cpu' || requested === 'cuda') return requested;
  if (platform === 'win32' && arch === 'arm64') return 'cpu';
  if ((env.OMNIVOICE_DEVICE || '').trim().toLowerCase() === 'cpu') return 'cpu';
  return (await nvidiaAvailable()) ? 'cuda' : 'cpu';
}

/** Both variants are frozen in uv.lock; never download CUDA before swapping to CPU. */
export function torchSyncArgs(variant) {
  return variant === 'cpu' ? ['--no-group', 'cuda', '--group', 'cpu'] : [];
}

/** The lock's CUDA runtime packages, excluding the hardware metadata binding. */
export function cudaOnlyPackages(lockText) {
  return [
    ...new Set(
      [...lockText.matchAll(/^name = "(nvidia-[a-z0-9-]+)"$/gm)]
        .map((match) => match[1])
        .filter((name) => name !== 'nvidia-ml-py'),
    ),
  ].sort();
}

/** Preserve SDK packages on repair; never lay locked CUDA/CT2 over the HIP overlay. */
export function windowsRocmSyncArgs(lockText) {
  return [
    '--inexact',
    ...['torch', 'torchaudio', 'torchvision', 'ctranslate2', ...cudaOnlyPackages(lockText)].flatMap(
      (name) => ['--no-install-package', name],
    ),
  ];
}
