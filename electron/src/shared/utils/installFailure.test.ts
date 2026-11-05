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
