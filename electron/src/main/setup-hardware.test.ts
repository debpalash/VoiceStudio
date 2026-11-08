import { describe, expect, it } from 'vitest';
import { detectSetupHardware, sanitizeGpuName } from './setup-hardware';

const smi = (out: string) => async () => out;
const fail = async () => {
  throw new Error('missing');
};

describe('detectSetupHardware', () => {
  it('names Apple silicon with Metal', async () => {
    expect(
      await detectSetupHardware({
        platform: 'darwin',
        arch: 'arm64',
        env: {},
        cpuModel: () => 'Apple M3 Pro',
      }),
    ).toEqual({ gpu: 'Apple M3 Pro', build: 'metal' });
  });
  it('shows nothing on Intel Macs', async () => {
    expect(await detectSetupHardware({ platform: 'darwin', arch: 'x64', env: {} })).toBeUndefined();
  });
  it('names the NVIDIA GPU', async () => {
    expect(
      await detectSetupHardware({
        platform: 'win32',
        arch: 'x64',
        env: {},
        hasNvidia: () => true,
        nvidiaSmi: smi('NVIDIA GeForce RTX 4070\nNVIDIA GeForce RTX 3060\n'),
      }),
    ).toEqual({ gpu: 'NVIDIA GeForce RTX 4070', build: 'cuda' });
  });
  it('keeps CUDA without a name when nvidia-smi fails', async () => {
    expect(
      await detectSetupHardware({
        platform: 'linux',
        arch: 'x64',
        env: {},
        hasNvidia: () => true,
        nvidiaSmi: fail,
      }),
    ).toEqual({ build: 'cuda' });
  });
  it('falls back to CPU without NVIDIA', async () => {
    expect(
      await detectSetupHardware({ platform: 'linux', arch: 'x64', env: {}, hasNvidia: () => false }),
    ).toEqual({ build: 'cpu', reason: 'no_nvidia' });
  });
  it('reports Windows on Arm', async () => {
    expect(await detectSetupHardware({ platform: 'win32', arch: 'arm64', env: {} })).toEqual({
      build: 'cpu',
      reason: 'windows_arm',
    });
  });
  it('reports a pinned CPU build', async () => {
    expect(
      await detectSetupHardware({
        platform: 'linux',
        arch: 'x64',
        env: { OMNIVOICE_TORCH_VARIANT: 'cpu' },
        hasNvidia: () => true,
      }),
    ).toEqual({ build: 'cpu', reason: 'explicit' });
  });
  it('reports ROCm without probing', async () => {
    expect(
      await detectSetupHardware({
        platform: 'linux',
        arch: 'x64',
        env: { OMNIVOICE_TORCH_VARIANT: 'rocm' },
        nvidiaSmi: fail,
      }),
    ).toEqual({ build: 'rocm' });
  });
});

describe('sanitizeGpuName', () => {
  it('trims, collapses and caps', () => {
    expect(sanitizeGpuName('  A   B \n')).toBe('A B');
    expect(sanitizeGpuName('x'.repeat(100))).toHaveLength(60);
    expect(sanitizeGpuName('')).toBeUndefined();
  });
});
