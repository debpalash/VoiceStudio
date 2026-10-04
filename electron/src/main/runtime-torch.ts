import { release } from 'node:os';

export type RuntimeTorchPreference = 'auto' | 'default' | 'rocm';

export interface WindowsGpuDevice {
  vendorId: number;
  deviceId?: number;
  deviceString?: string;
  driverVersion?: string;
}

const SUPPORTED_WINDOWS_ROCM_GPUS = new Set([
  'radeon rx 9070',
  'radeon rx 9070 xt',
  'radeon ai pro r9700',
  'radeon rx 9060 xt',
  'radeon rx 7900 xtx',
  'radeon pro w7900',
  'radeon pro w7900 dual slot',
  'radeon rx 7700',
]);

export function supportedWindowsRocmGpu(
  devices: readonly WindowsGpuDevice[],
  windowsRelease = release(),
): WindowsGpuDevice | null {
  const build = Number(windowsRelease.split('.')[2]);
  if (!Number.isInteger(build) || build < 22000) return null;
  if (devices.some((device) => device.vendorId === 0x10de)) return null;
  return (
    devices.find((device) => {
      if (device.vendorId !== 0x1002) return false;
      if (device.deviceId === 0x7550) return true;
      const name = (device.deviceString || '')
        .toLowerCase()
        .replace(/^amd\s+/, '')
        .replace(/\s+/g, ' ')
        .trim();
      return SUPPORTED_WINDOWS_ROCM_GPUS.has(name);
    }) || null
  );
}
