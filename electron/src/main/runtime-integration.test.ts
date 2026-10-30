import { fixture, interpreter, report } from './runtime-windows-rocm.fixture';
import { execFile } from 'node:child_process';
import { readFile } from 'node:fs/promises';
import { join } from 'node:path';
import { expect, it, vi } from 'vitest';
import {
  installRuntime,
  MISSING_SYSTEM_LIBRARY_CODE,
  NATIVE_DIAGNOSTIC_PREFIX,
  RUNTIME_IMPORT_PROBE,
  RUNTIME_NATIVE_DIAGNOSTIC_PROBE,
  runtimeDependenciesReady,
} from './runtime-project';
import type { RuntimeRunner } from './runtime-project';
import {
  WINDOWS_ROCM_ABI_PROBE,
  WINDOWS_ROCM_IMPORT_PREFIX,
  WINDOWS_ROCM_MODULE,
} from './runtime-windows-rocm';
import { WINDOWS_ROCM_IMPORT_TIMEOUT_MS } from './runtime-probe';

const hipImportProbe =
  WINDOWS_ROCM_IMPORT_PREFIX +
  "print('VOICESTUDIO_IMPORT=torch', flush=True)\nimport torch\n" +
  RUNTIME_IMPORT_PROBE;

it('initializes DLL search and announces Torch before importing HIP application dependencies', async () => {
  const { bundle, project } = await fixture();
  const run = vi.fn<RuntimeRunner>(async (_command, args) => {
    if (args[0] === 'sync') await interpreter(project);
    return report;
  });
  await installRuntime(
    bundle,
    project,
    'uv',
    run,
    new AbortController().signal,
    undefined,
    'global',
  );
  const imports = run.mock.calls.find(([, args]) => args[1]?.includes('import fastapi'))?.[1][1];
  expect(imports).toBeDefined();
  const probe = imports!;
  const firstTorchImport = probe.indexOf('import torch');
  expect(probe.indexOf('initialize_dll_search()')).toBeLessThan(firstTorchImport);
  expect(probe.indexOf("print('VOICESTUDIO_IMPORT=torch', flush=True)")).toBeLessThan(firstTorchImport);
  expect(firstTorchImport).toBeLessThan(probe.indexOf('import fastapi'));
  expect(firstTorchImport).toBeLessThan(probe.indexOf('import faster_whisper'));
});

it.each(['fresh', 'existing'] as const)(
  'diagnoses a missing system DLL after HIP provisioning on a %s environment',
  async (state) => {
    const { bundle, project } = await fixture();
    if (state === 'existing') await interpreter(project);
    const run = vi.fn<RuntimeRunner>(async (_command, args) => {
      if (args[0] === 'sync') await interpreter(project);
      if (args[0] === '-c' && args[1]?.includes('import fastapi')) {
        throw new Error('Could not load libtorchaudio.pyd');
      }
      if (args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE) {
        return `${NATIVE_DIAGNOSTIC_PREFIX}${JSON.stringify({
          missing_system_libraries: ['vcomp140.dll'],
          extension_present: true,
        })}`;
      }
      return report;
    });
    await expect(
      installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
    ).rejects.toMatchObject({
      code: MISSING_SYSTEM_LIBRARY_CODE,
      missingSystemLibraries: ['vcomp140.dll'],
    });
    const calls = run.mock.calls;
    const overlayIndex = calls.findIndex(([, args]) => args.includes('--uv'));
    const diagnosisIndex = calls.findIndex(
      ([, args]) => args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE,
    );
    expect(overlayIndex).toBeGreaterThan(0);
    expect(diagnosisIndex).toBeGreaterThan(overlayIndex);
    expect(calls.filter(([, args]) => args[0] === 'sync')).toHaveLength(1);
    expect(calls.some(([, args]) => args[0] === 'cache' || args[0] === 'pip')).toBe(false);
    expect(calls.some(([, args]) => args.includes('--verify-only'))).toBe(false);
    expect(
      calls
        .slice(0, overlayIndex)
        .some(([, args]) => /import (torch|ctranslate2)/.test(args[1] ?? '')),
    ).toBe(false);
    expect(calls.some(([, args]) => args[1] === WINDOWS_ROCM_ABI_PROBE)).toBe(
      state === 'existing',
    );
    await expect(readFile(join(project, '.runtime-ready'))).rejects.toThrow();
    await expect(readFile(join(project, '.runtime-installing'))).resolves.toBeDefined();
  },
);

it('retains the failed native import module and bounded HIP DLL prefix after provisioning', async () => {
  const { bundle, project } = await fixture();
  const run = vi.fn<RuntimeRunner>(async (_command, args) => {
    if (args[0] === 'sync') await interpreter(project);
    if (args[0] === '-c' && args[1]?.includes('import fastapi')) {
      throw Object.assign(new Error('Runtime setup exited with code 3221225477'), {
        code: 3221225477,
        stdout: 'VOICESTUDIO_IMPORT=torch\nVOICESTUDIO_IMPORT=torchaudio\n',
      });
    }
    return report;
  });
  await expect(
    installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
  ).rejects.toMatchObject({
    code: 3221225477,
    stdout: expect.stringContaining('VOICESTUDIO_IMPORT=torchaudio'),
    message: expect.stringContaining('while importing "torchaudio"'),
  });
  const importCall = run.mock.calls.find(
    ([, args]) => args[1] === hipImportProbe,
  );
  expect(importCall?.[4]).toEqual({ timeoutMs: WINDOWS_ROCM_IMPORT_TIMEOUT_MS });
  expect(importCall?.[1][1].indexOf('initialize_dll_search()')).toBeLessThan(
    importCall?.[1][1].indexOf('import fastapi') ?? -1,
  );
  expect(run.mock.calls.some(([, args]) => args.includes('--verify-only'))).toBe(false);
  await expect(readFile(join(project, '.runtime-ready'))).rejects.toThrow();
});

it('requires full HIP verification after successful bounded application imports', async () => {
  const { bundle, project } = await fixture();
  const verificationFailure = Object.assign(new Error('ROCm GPU kernel verification failed'), {
    code: 'GPU_KERNEL_FAILED',
  });
  const run = vi.fn<RuntimeRunner>(async (_command, args) => {
    if (args[0] === 'sync') await interpreter(project);
    if (args.includes('--verify-only')) throw verificationFailure;
    return report;
  });
  await expect(
    installRuntime(bundle, project, 'uv', run, new AbortController().signal, undefined, 'global'),
  ).rejects.toBe(verificationFailure);
  const calls = run.mock.calls;
  const importIndex = calls.findIndex(
    ([, args]) => args[1] === hipImportProbe,
  );
  const verifyIndex = calls.findIndex(([, args]) => args.includes('--verify-only'));
  expect(importIndex).toBeGreaterThan(0);
  expect(verifyIndex).toBeGreaterThan(importIndex);
  expect(calls[verifyIndex]?.[1]).toEqual(['-m', WINDOWS_ROCM_MODULE, '--verify-only']);
  expect(calls[importIndex]?.[4]).toEqual({ timeoutMs: WINDOWS_ROCM_IMPORT_TIMEOUT_MS });
  expect(calls.some(([, args]) => args[1] === RUNTIME_NATIVE_DIAGNOSTIC_PROBE)).toBe(false);
  await expect(readFile(join(project, '.runtime-ready'))).rejects.toThrow();
  await expect(readFile(join(project, '.runtime-installing'))).resolves.toBeDefined();
});

it.each([
  [
    'timeout',
    Object.assign(new Error('Command timed out'), { killed: true, signal: 'SIGTERM' }),
    '',
    'Command timed out (importing torchaudio)',
  ],
  [
    'native crash',
    Object.assign(new Error('Access violation'), { code: 3221225477 }),
    '',
    'Access violation (importing torchaudio)',
  ],
  [
    'Python exception',
    new Error('Command failed'),
    'ImportError: a required DLL is missing\n',
    'ImportError: a required DLL is missing',
  ],
] as const)(
  'keeps HIP readiness fail-closed with useful import detail on %s',
  async (_label, failure, stderr, expectedDetail) => {
    const { project } = await fixture();
    vi.mocked(execFile).mockImplementation(((
      _command: unknown,
      args: string[],
      _options: unknown,
      callback: (error: Error | null, stdout: string, stderr: string) => void,
    ) => {
      if (args.includes('--verify-only')) callback(null, report, '');
      else {
        expect(args[1]).toBe(hipImportProbe);
        callback(failure, 'VOICESTUDIO_IMPORT=torch\nVOICESTUDIO_IMPORT=torchaudio\n', stderr);
      }
    }) as never);
    const onFailure = vi.fn();
    expect(await runtimeDependenciesReady(project, onFailure)).toBe(false);
    expect(onFailure).toHaveBeenCalledExactlyOnceWith(expectedDetail);
    expect(execFile).toHaveBeenCalledTimes(2);
  },
);
