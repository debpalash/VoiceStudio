import { describe, expect, it } from 'vitest';
import { backendStartupFailure, isBrokenEnvironmentOutput, windowsLoadBlock } from './backendOutput';

const TRACE = [
  'Traceback (most recent call last):',
  '  File "backend/main.py", line 671, in _phase_a_build_inner',
  '    import torchaudio',
  '  File "site-packages/torchaudio/_extension/utils.py", line 38, in _load_lib',
  '    torch.ops.load_library(dll_path)',
  'FileNotFoundError: Could not find module',
  "'C:\\\\Users\\\\me\\\\AppData\\\\Roaming\\\\VoiceStudio\\\\runtime\\\\project\\\\.venv\\\\Lib\\\\site-packages\\\\torchaudio\\\\lib\\\\libtorchaudio.pyd' (or one of its dependencies).",
];

describe('backendStartupFailure', () => {
  it('reads the step and the reason out of the backend verdict', () => {
    expect(
      backendStartupFailure([
        "FATAL: backend startup failed during 'ml_imports': Could not find module 'x.pyd'",
      ]),
    ).toEqual({ step: 'ml_imports', reason: "Could not find module 'x.pyd'" });
  });

  it('reports nothing when the backend never printed a verdict', () => {
    expect(backendStartupFailure(['INFO: 127.0.0.1:50081 - "GET /health HTTP/1.1" 503'])).toBe(
      undefined,
    );
  });

  it('reports the most recent verdict when the backend failed twice', () => {
    expect(
      backendStartupFailure([
        "FATAL: backend startup failed during 'ml_imports': first",
        'INFO: restarting',
        "FATAL: backend startup failed during 'api_routes': second",
      ]),
    ).toEqual({ step: 'api_routes', reason: 'second' });
  });
});

describe('isBrokenEnvironmentOutput', () => {
  it('recognises the Windows native extension failure of #3058', () => {
    expect(isBrokenEnvironmentOutput(TRACE)).toBe(true);
  });

  it('recognises the same failure reported as WinError 126', () => {
    expect(
      isBrokenEnvironmentOutput([
        'OSError: [WinError 126] The specified module could not be found. Error loading',
        '"...\\torch\\lib\\shm.dll" or one of its dependencies.',
      ]),
    ).toBe(true);
  });

  it('does not treat an ordinary crash as a broken environment', () => {
    expect(isBrokenEnvironmentOutput(['RuntimeError: CUDA out of memory'])).toBe(false);
  });

  it('does not treat an import error about something else as a broken environment', () => {
    expect(
      isBrokenEnvironmentOutput([
        'ModuleNotFoundError: No module named "some_user_package"',
      ]),
    ).toBe(false);
  });

  it('ignores a traceback stranded above the current run', () => {
    const stale = [...TRACE, ...Array.from({ length: 40 }, () => 'INFO: heartbeat')];
    expect(isBrokenEnvironmentOutput(stale)).toBe(false);
  });

  it('reports nothing for empty output', () => {
    expect(isBrokenEnvironmentOutput([])).toBe(false);
  });
});

describe('windowsLoadBlock (#3209)', () => {
  const blocked = (reason: string) => [
    'Traceback (most recent call last):',
    '    import torch',
    `ImportError: DLL load failed while importing _C: ${reason}`,
  ];
  it('names an application-control block and keeps it out of the repair path', () => {
    const lines = blocked('An Application Control policy has blocked this file.');
    expect(windowsLoadBlock(lines)).toBe('application_control');
    expect(isBrokenEnvironmentOutput(lines)).toBe(false);
    expect(windowsLoadBlock(blocked('This program is blocked by group policy.'))).toBe(
      'group_policy',
    );
  });
  it('names an antivirus that refused the load', () => {
    expect(
      windowsLoadBlock(
        blocked(
          'Operation did not complete successfully because the file contains a virus or potentially unwanted software.',
        ),
      ),
    ).toBe('antivirus');
  });
  it('leaves an ordinary missing DLL to the broken-environment check', () => {
    const lines = blocked('The specified module could not be found.');
    expect(windowsLoadBlock(lines)).toBeNull();
    expect(isBrokenEnvironmentOutput(lines)).toBe(true);
  });
});
