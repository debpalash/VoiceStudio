import { execFile } from 'node:child_process';
import { cpus } from 'node:os';
import { resolveTorchVariant } from './runtime-project';

export interface SetupHardware {
  gpu?: string;
  build: 'metal' | 'cuda' | 'rocm' | 'cpu';
  reason?: 'no_nvidia' | 'windows_arm' | 'explicit';
}

export interface SetupHardwareProbe {
  env?: NodeJS.ProcessEnv;
  platform?: NodeJS.Platform;
  arch?: string;
  hasNvidia?: () => boolean;
  cpuModel?: () => string | undefined;
  /** Resolves the raw `nvidia-smi` name output, or rejects. */
  nvidiaSmi?: () => Promise<string>;
}

const NAME_MAX = 60;

export function sanitizeGpuName(raw: string | undefined): string | undefined {
  // eslint-disable-next-line no-control-regex
  const first = raw?.split(/\r?\n/).find((line) => line.trim())?.replace(/[\u0000-\u001f]/g, '');
  const name = first?.trim().replace(/\s+/g, ' ');
  return name ? name.slice(0, NAME_MAX) : undefined;
}

function queryNvidiaSmi(): Promise<string> {
  return new Promise((resolve, reject) => {
    execFile(
      'nvidia-smi',
      ['--query-gpu=name', '--format=csv,noheader'],
      { timeout: 3000, windowsHide: true },
      (error, stdout) => (error ? reject(error) : resolve(String(stdout))),
    );
  });
}

/** What the runtime install will pick for this host, for the setup screen. Never throws. */
export async function detectSetupHardware(
  probe: SetupHardwareProbe = {},
): Promise<SetupHardware | undefined> {
  try {
    const env = probe.env ?? process.env;
    const platform = probe.platform ?? process.platform;
    const arch = probe.arch ?? process.arch;
    if (platform === 'darwin') {
      if (arch !== 'arm64') return undefined;
      const gpu = sanitizeGpuName((probe.cpuModel ?? (() => cpus()[0]?.model))());
      return { ...(gpu ? { gpu } : {}), build: 'metal' };
    }
    if (platform !== 'linux' && platform !== 'win32') return undefined;
    const choice = resolveTorchVariant(env, platform, arch, probe.hasNvidia);
    if (choice.variant === 'rocm') return { build: 'rocm' };
    if (choice.variant === 'cpu') {
      const reason = choice.explicit
        ? 'explicit'
        : platform === 'win32' && arch === 'arm64'
          ? 'windows_arm'
          : 'no_nvidia';
      return { build: 'cpu', reason };
    }
    if (platform === 'linux' && arch !== 'x64') return undefined;
    let gpu: string | undefined;
    try {
      gpu = sanitizeGpuName(await (probe.nvidiaSmi ?? queryNvidiaSmi)());
    } catch {
      gpu = undefined;
    }
    return { ...(gpu ? { gpu } : {}), build: 'cuda' };
  } catch {
    return undefined;
  }
}
