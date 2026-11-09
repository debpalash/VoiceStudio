// @vitest-environment node
import { mkdtemp, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { expect, it } from 'vitest';
import {
  RUNTIME_TORCH_MARKER,
  windowsRocmInstalled,
  windowsRocmRequested,
} from './runtime-windows-rocm';

it('never throws when ROCm is requested on a host that cannot run it', () => {
  expect(windowsRocmRequested('rocm', 'win32', 'arm64')).toBe(false);
  expect(windowsRocmRequested('rocm', 'win32', 'x64')).toBe(true);
  expect(windowsRocmRequested('rocm', 'linux', 'x64')).toBe(false);
  expect(windowsRocmRequested(undefined, 'win32', 'x64')).toBe(false);
});

it('treats a runtime as ROCm-installed only after an install recorded it', async () => {
  const project = await mkdtemp(join(tmpdir(), 'rocm-marker-'));
  expect(await windowsRocmInstalled(project)).toBe(false);
  await writeFile(join(project, RUNTIME_TORCH_MARKER), 'default');
  expect(await windowsRocmInstalled(project)).toBe(false);
  await writeFile(join(project, RUNTIME_TORCH_MARKER), 'windows-rocm');
  expect(await windowsRocmInstalled(project)).toBe(true);
});

it('shows the Windows ROCm torch source as AMD, not the Linux PyTorch index', async () => {
  const { runtimeDownloadHosts } = await import('./runtime-project');
  const previous = process.env.OMNIVOICE_TORCH_VARIANT;
  process.env.OMNIVOICE_TORCH_VARIANT = 'rocm';
  try {
    const hosts = await runtimeDownloadHosts(join(__dirname, '..', '..', '..'), 'win32', 'x64');
    expect(hosts.get('torch')).toBe('repo.radeon.com');
    const linux = await runtimeDownloadHosts(join(__dirname, '..', '..', '..'), 'linux', 'x64');
    expect(linux.get('torch')).toBe('download.pytorch.org');
  } finally {
    if (previous === undefined) delete process.env.OMNIVOICE_TORCH_VARIANT;
    else process.env.OMNIVOICE_TORCH_VARIANT = previous;
  }
});

it('reports ROCm on Windows ARM64 as not ready instead of rejecting', async () => {
  const { runtimeDependenciesReady } = await import('./runtime-project');
  const platform = Object.getOwnPropertyDescriptor(process, 'platform')!;
  const arch = Object.getOwnPropertyDescriptor(process, 'arch')!;
  const previous = process.env.OMNIVOICE_TORCH_VARIANT;
  Object.defineProperty(process, 'platform', { value: 'win32', configurable: true });
  Object.defineProperty(process, 'arch', { value: 'arm64', configurable: true });
  process.env.OMNIVOICE_TORCH_VARIANT = 'rocm';
  try {
    const reasons: string[] = [];
    await expect(
      runtimeDependenciesReady(tmpdir(), (reason) => reasons.push(reason)),
    ).resolves.toBe(false);
    expect(reasons.join('\n')).toContain('x64');
  } finally {
    Object.defineProperty(process, 'platform', platform);
    Object.defineProperty(process, 'arch', arch);
    if (previous === undefined) delete process.env.OMNIVOICE_TORCH_VARIANT;
    else process.env.OMNIVOICE_TORCH_VARIANT = previous;
  }
});
