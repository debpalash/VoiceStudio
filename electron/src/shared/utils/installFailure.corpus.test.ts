import { describe, expect, it } from 'vitest';
import { classifyInstallFailure, type FailureEvidence, type InstallFailureCode } from './installFailure';

// Text from real reports. When an issue reveals a new class, add it here.
const CORPUS: [string, FailureEvidence, InstallFailureCode][] = [
  [
    '#3635 Smart App Control confirmed by the reporter',
    { stage: 'failed', message: 'spawn UNKNOWN', smartAppControl: 'on' },
    'WINDOWS_APP_CONTROL_BLOCKED',
  ],
  ['#3801 bare refusal, nothing else known', { stage: 'failed', message: 'spawn UNKNOWN' }, 'EXEC_REFUSED'],
  [
    '#3748 torch download stuck at 12.9 MB',
    {
      stage: 'setup_required',
      message: 'Runtime setup exited with code 1',
      stalled: true,
      logLines: ['Download progress: 12.9 MB of ~3.2 GB at 3.5 MB/s from download-r2.pytorch.org'],
    },
    'RUNTIME_DOWNLOAD_STALLED',
  ],
  [
    '#3720 connection dropped during torch',
    {
      stage: 'setup_required',
      message: 'Runtime setup exited with code 1',
      logLines: [
        '× Failed to download `torch==2.8.0+cu128`',
        '╰─▶ peer closed connection without sending TLS close_notify:',
        'Runtime setup exited with code 1',
      ],
    },
    'RUNTIME_DOWNLOAD_INTERRUPTED',
  ],
  [
    '#3772 access violation',
    {
      stage: 'crashed',
      message:
        'Backend exited unexpectedly (exit code 3221225477). Last output: Windows fatal exception: access violation at …/torch/storage.py:470 in __getitem__',
    },
    'NATIVE_ACCESS_VIOLATION',
  ],
  [
    '#3684 illegal instruction',
    {
      stage: 'crashed',
      message:
        'Backend exited unexpectedly (exit code 3221225501). Last output: Windows fatal exception: code 0xc000001d at …/torch/nn/modules/linear.py:125 in forward',
    },
    'NATIVE_ILLEGAL_INSTRUCTION',
  ],
  [
    '#3713 silent startup timeout',
    {
      stage: 'failed',
      message:
        'Backend did not answer on port 3900 within 300 s (OMNIVOICE_STARTUP_BUDGET_S). It printed no output. Slow disks and antivirus scanning of the runtime folder are the usual cause.',
    },
    'STARTUP_TIMEOUT_SILENT',
  ],
  [
    '#3589 source checkout incomplete',
    {
      stage: 'failed',
      message:
        "The Python environment in C:\\PRG\\XDCODE\\PRJ\\VOZ is incomplete: ModuleNotFoundError: No module named 'torchvision'. Open a terminal in C:\\PRG\\XDCODE\\PRJ\\VOZ, run `bun run setup:api`, wait for it to finish, then restart.",
    },
    'SOURCE_ENV_INCOMPLETE',
  ],
  [
    '#3722 source checkout missing',
    {
      stage: 'failed',
      message:
        'The Python environment in D:\\VoiceStudio-main\\VoiceStudio-main is missing (no .venv\\Scripts\\python.exe). Open a terminal in D:\\VoiceStudio-main\\VoiceStudio-main, run `bun run setup:api`, wait for it to finish, then press Retry.',
    },
    'SOURCE_ENV_INCOMPLETE',
  ],
  [
    '#3704 port conflict',
    {
      stage: 'port_in_use',
      message: 'Port 3900 is already in use by another application, and VoiceStudio could not free it.',
    },
    'PORT_IN_USE',
  ],
  [
    '#3612 named startup step',
    {
      stage: 'crashed',
      message: "Backend failed to start during 'api_routes' (exit code 1): 'NoneType' object has no attribute",
    },
    'BACKEND_STARTUP_FAILED',
  ],
  [
    '#3780 ROCm missing',
    { stage: 'setup_required', message: 'AMD GPU — ROCm not configured' },
    'ROCM_NOT_CONFIGURED',
  ],
  [
    '#3209 DLL refused by policy',
    {
      stage: 'crashed',
      message: 'Backend exited (exit code 1).',
      logLines: ['ImportError: DLL load failed while importing _sqlite3: An Application Control policy has blocked this file.'],
    },
    'WINDOWS_APP_CONTROL_BLOCKED',
  ],
];

describe('real reports classify to the right code', () => {
  it.each(CORPUS)('%s', (_name, evidence, code) => {
    expect(classifyInstallFailure(evidence)).toBe(code);
  });
});
