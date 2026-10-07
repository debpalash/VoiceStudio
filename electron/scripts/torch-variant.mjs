import { execFile } from 'node:child_process';
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
  if (platform === 'darwin') return 'native';
  // ROCm is Linux-only; match the packaged install's Windows fallback.
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
