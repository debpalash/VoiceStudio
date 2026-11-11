import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { describe, expect, it } from 'vitest';
import { ERROR_CLASS_KEYS } from './errorDocsMap';
import {
  classifyInstallFailure,
  collapseRepeatedLines,
  failureDocsAnchor,
  failureGuidance,
  failureMarker,
  FAILURE_GUIDANCE,
  formatFailureFacts,
  INSTALL_FAILURE_CODES,
  isGpuDriverModule,
  MAX_ENCODED_FACTS,
  refusedProgram,
} from './installFailure';
import { composeBugReportUrl } from './bugReportDocument';

const repo = resolve(__dirname, '../../../..');
const docs = readFileSync(resolve(repo, 'docs/install/troubleshooting.md'), 'utf8');
const english = JSON.parse(
  readFileSync(resolve(repo, 'electron/src/renderer/src/i18n/locales/en.json'), 'utf8'),
) as { backend: Record<string, string> };

// Adding a code without its fix, docs and mirror entry must fail here.
describe('every failure code is complete', () => {
  const coded = INSTALL_FAILURE_CODES.filter((code) => code !== 'UNCLASSIFIED');

  it.each(coded)('%s has a docs section', (code) => {
    expect(docs).toContain(`### \`${code}\``);
    expect(failureDocsAnchor(code)).toBe(code.toLowerCase());
  });

  it.each(coded)('%s is in the error docs mirror', (code) => {
    expect(ERROR_CLASS_KEYS as readonly string[]).toContain(code);
  });

  it.each([...INSTALL_FAILURE_CODES])('%s has catalogued guidance', (code) => {
    const { cause, hints } = FAILURE_GUIDANCE[code];
    if (cause) expect(english.backend[`cause_${cause}`]).toBeTruthy();
    for (const hint of hints) expect(english.backend[hint.replace('backend.', '')]).toBeTruthy();
  });
});

describe('classifyInstallFailure', () => {
  const refused = 'Could not start C:\\runtime\\python.exe: spawn UNKNOWN';

  it('does not name a blocker without evidence', () => {
    expect(classifyInstallFailure({ message: refused })).toBe('EXEC_REFUSED');
    expect(classifyInstallFailure({ message: refused, smartAppControl: 'off' })).toBe(
      'EXEC_REFUSED',
    );
    expect(classifyInstallFailure({ message: refused, smartAppControl: 'unknown' })).toBe(
      'EXEC_REFUSED',
    );
  });

  it('upgrades a refusal once the OS provides evidence', () => {
    for (const evidence of [
      { smartAppControl: 'on' as const },
      { smartAppControl: 'evaluation' as const },
      { blockedFiles: ['python.exe'] },
    ])
      expect(classifyInstallFailure({ message: refused, ...evidence })).toBe(
        'WINDOWS_APP_CONTROL_BLOCKED',
      );
    expect(classifyInstallFailure({ message: refused, quarantined: true })).toBe(
      'GATEKEEPER_QUARANTINE',
    );
    expect(classifyInstallFailure({ message: refused, noexecMount: true })).toBe(
      'EXEC_NOEXEC_MOUNT',
    );
  });

  it('leaves an unknown failure unclassified', () => {
    expect(classifyInstallFailure({ message: 'Something new happened' })).toBe('UNCLASSIFIED');
    expect(classifyInstallFailure({})).toBe('UNCLASSIFIED');
  });

  it('does not read a running engine log as a download failure', () => {
    expect(
      classifyInstallFailure({
        stage: 'crashed',
        message: 'Backend exited unexpectedly (exit code 1).',
        logLines: ['Failed to fetch model: connection reset'],
      }),
    ).toBe('UNCLASSIFIED');
  });

  it('prefers a recorded exit code over the message', () => {
    expect(classifyInstallFailure({ stage: 'crashed', exitCode: 3221225501 })).toBe(
      'NATIVE_ILLEGAL_INSTRUCTION',
    );
    expect(classifyInstallFailure({ stage: 'crashed', signal: 'SIGSEGV' })).toBe(
      'NATIVE_ACCESS_VIOLATION',
    );
    expect(classifyInstallFailure({ stage: 'crashed', exitCode: 0xc0000409 })).toBe('NATIVE_CRASH');
    // SIGABRT is not evidence of a native fault.
    expect(classifyInstallFailure({ stage: 'crashed', signal: 'SIGABRT' })).toBe('UNCLASSIFIED');
  });
});

describe('failureGuidance', () => {
  it('never sends a work PC policy to the Smart App Control toggle', () => {
    const policy = failureGuidance('WINDOWS_APP_CONTROL_BLOCKED', { smartAppControl: 'off' });
    expect(policy.cause).toBe('app_control_policy');
    expect(policy.hints).not.toContain('backend.hint_smart_app_control');
  });

  it('gives Smart App Control its options and no antivirus advice', () => {
    const sac = failureGuidance('WINDOWS_APP_CONTROL_BLOCKED', { smartAppControl: 'on' });
    expect(sac.cause).toBe('smart_app_control');
    expect(sac.hints).toEqual([
      'backend.hint_smart_app_control',
      'backend.hint_smart_app_control_local',
    ]);
    expect(sac.hints).not.toContain('backend.hint_spawn_blocked');
  });

  it('names an external kill (exit -1 / 4294967295) for the failure screen', () => {
    for (const exitCode of [-1, 4294967295]) {
      const g = failureGuidance('UNCLASSIFIED', { message: 'Backend stopped', exitCode });
      expect(g.cause).toBe('external_kill');
      expect(g.hints).toEqual(['backend.hint_external_kill']);
    }
  });

  it('does not claim an external kill over a traceback or another exit code', () => {
    const tb = 'Traceback (most recent call last):\n  File "x.py"\nImportError: boom';
    expect(failureGuidance('UNCLASSIFIED', { message: tb, exitCode: -1 }).cause).toBeNull();
    expect(
      failureGuidance('UNCLASSIFIED', { message: 'x', exitCode: -1, logLines: [tb] }).cause,
    ).toBeNull();
    expect(failureGuidance('UNCLASSIFIED', { message: 'x', exitCode: 1 }).cause).toBeNull();
  });
});

describe('native crash guidance', () => {
  it('names a disk read failure for an in-page error, from the message alone too', () => {
    const message = 'Backend exited unexpectedly (exit code 3221225478).';
    expect(classifyInstallFailure({ stage: 'crashed', message })).toBe('NATIVE_IN_PAGE_ERROR');
    expect(classifyInstallFailure({ stage: 'crashed', message: 'x', exitCode: -1073741818 })).toBe(
      'NATIVE_IN_PAGE_ERROR',
    );
    expect(failureGuidance('NATIVE_IN_PAGE_ERROR')).toEqual({
      cause: 'disk_read',
      hints: ['backend.hint_in_page_error'],
    });
  });

  it('points a crash inside the graphics driver at the driver, not the CPU', () => {
    for (const code of ['NATIVE_ACCESS_VIOLATION', 'NATIVE_ILLEGAL_INSTRUCTION', 'NATIVE_CRASH'] as const)
      for (const faultingModule of ['nvcuda64.dll', 'cublasLt64_12.dll', 'igc64.dll', 'amdhip64_6.dll'])
        expect(failureGuidance(code, { faultingModule })).toEqual({
          cause: 'native_crash',
          hints: ['backend.hint_gpu_driver_fault'],
        });
  });

  it('keeps the default guidance for other modules or none', () => {
    for (const faultingModule of [undefined, 'torch_cpu.dll', 'ntdll.dll', 'python311.dll'])
      expect(failureGuidance('NATIVE_ILLEGAL_INSTRUCTION', { faultingModule })).toEqual(
        FAILURE_GUIDANCE.NATIVE_ILLEGAL_INSTRUCTION,
      );
    expect(isGpuDriverModule('C:\\Windows\\nvcuda64.dll')).toBe(false);
  });

  it('does not blame the driver for CPU runtimes that share a vendor prefix', () => {
    for (const module of [
      'libiomp5md.dll',
      'mkl_core.2.dll',
      'mkl_intel_thread.2.dll',
      'mkl_avx512.2.dll',
      'intel_openmp.dll',
      'ur_loader.dll',
      'ze_tracing_layer.dll',
      'torch_cpu.dll',
      'ctranslate2.dll',
      'onnxruntime.dll',
    ]) {
      expect(isGpuDriverModule(module)).toBe(false);
      expect(
        failureGuidance('NATIVE_ILLEGAL_INSTRUCTION', { faultingModule: module }),
      ).toEqual(FAILURE_GUIDANCE.NATIVE_ILLEGAL_INSTRUCTION);
    }
    for (const module of [
      'nvcuda64.dll',
      'cudnn_ops64_9.dll',
      'igc64.dll',
      'igd10iumd64.dll',
      'ze_intel_gpu64.dll',
      'ze_loader.dll',
      'ur_adapter_level_zero.dll',
      'amdhip64_6.dll',
      'atio6axx.dll',
    ])
      expect(isGpuDriverModule(module)).toBe(true);
    expect(
      failureGuidance('EXEC_REFUSED', { faultingModule: 'nvcuda64.dll' }).hints,
    ).not.toContain('backend.hint_gpu_driver_fault');
  });

  it('puts the faulting module in the report facts', () => {
    const lines = formatFailureFacts({
      code: 'NATIVE_ACCESS_VIOLATION',
      stage: 'crashed',
      exitCode: 3221225477,
      faultingModule: 'torch_cpu.dll',
      faultingModuleVersion: '2.8.0',
      faultOffset: '0x1e3f4',
    });
    expect(lines).toContain('- Faulting module: torch_cpu.dll 2.8.0 at 0x1e3f4');
  });
});

describe('reports', () => {
  const big = {
    code: 'EXEC_REFUSED' as const,
    stage: 'failed',
    program: 'python.exe',
    antivirus: Array.from({ length: 200 }, (_, i) => `Antivirus Product ${i}`),
    blockedFiles: Array.from({ length: 200 }, (_, i) => `very-long-file-name-${i}.dll`),
  };

  it('keeps the facts block within budget and leads with the code', () => {
    const lines = formatFailureFacts(big);
    expect(encodeURIComponent(lines.join('\n')).length).toBeLessThanOrEqual(MAX_ENCODED_FACTS);
    expect(lines).toContain('- Code: EXEC_REFUSED');
    expect(lines).toContain(failureMarker('EXEC_REFUSED'));
  });

  it('survives maximum-size logs: logs are what get cut', () => {
    const url = composeBugReportUrl({
      title: '[Install] EXEC_REFUSED: spawn UNKNOWN',
      labels: 'bug,install',
      factsSection: formatFailureFacts({ code: 'EXEC_REFUSED', stage: 'failed', program: 'python.exe' }),
      error: new Error('x'.repeat(5000)),
      ctx: 'y'.repeat(9000),
      crashSection: ['```', 'z'.repeat(20000), '```'],
    });
    const parsed = new URL(url);
    const body = parsed.searchParams.get('body') ?? '';
    expect(parsed.searchParams.get('labels')).toBe('bug,install');
    expect(body).toContain(failureMarker('EXEC_REFUSED'));
    expect(body).toContain('- Program: python.exe');
    expect(body).toContain('(truncated)');
  });

  it('collapses repeated log lines', () => {
    expect(collapseRepeatedLines(['a', 'b', 'b', 'b', 'c', 'c'])).toEqual(['a', 'b (×3)', 'c (×2)']);
    expect(collapseRepeatedLines([])).toEqual([]);
  });

  it('reads the refused program from the message', () => {
    expect(refusedProgram('Could not start C:\\Program Files\\x\\python.exe: spawn UNKNOWN')).toBe(
      'C:\\Program Files\\x\\python.exe',
    );
    expect(refusedProgram('spawn UNKNOWN')).toBeUndefined();
  });
});
