import { describe, expect, it } from 'vitest';
import {
  describeInstall,
  describeInstallGroup,
  formatInstallEta,
  oneLineReason,
  terminalInstallView,
} from './model-install-state';

const job = (fields: Record<string, unknown>) => ({
  repo_id: 'org/model',
  state: 'downloading',
  ...fields,
});

describe('describeInstall', () => {
  it('walks queued -> downloading -> finishing from the reported bytes', () => {
    expect(describeInstall(job({})).stage).toBe('queued');
    const running = describeInstall(
      job({ bytes_done: 256, total_bytes: 1024, rate: 64, eta_seconds: 12 }),
    );
    expect(running).toMatchObject({ stage: 'downloading', pct: 25, rate: 64, etaSeconds: 12 });
    const finishing = describeInstall(job({ bytes_done: 1024, total_bytes: 1024, rate: 9 }));
    expect(finishing).toMatchObject({ stage: 'finishing', pct: 100, rate: null, etaSeconds: null });
  });

  it('reads cancel and terminal backend states', () => {
    expect(describeInstall(job({ state: 'cancelling', bytes_done: 5 })).stage).toBe('cancelling');
    expect(describeInstall(job({ phase: 'cancelling' })).stage).toBe('cancelling');
    expect(describeInstall(job({ state: 'install_cancelled' })).stage).toBe('cancelled');
    expect(describeInstall(job({ state: 'done' })).stage).toBe('ready');
  });

  it('gives a failure one line, its next step and where to fix it', () => {
    const disk = describeInstall(
      job({
        state: 'failed',
        docs_topic: 'DISK_SPACE_LOW',
        error: 'No space left on device\nTraceback (most recent call last): ...',
        retry_after_seconds: 4.2,
      }),
    );
    expect(disk).toMatchObject({
      stage: 'failed',
      problem: 'disk',
      reason: 'No space left on device',
      retryAfter: 5,
      recovery: { to: '/settings/storage' },
    });
    expect(describeInstall(job({ state: 'failed', docs_topic: 'HF_AUTH_FAILED' }))).toMatchObject({
      problem: 'gated',
      recovery: { to: '/settings/credentials' },
    });
    expect(describeInstall(job({ state: 'failed', docs_topic: 'HF_MIRROR_GATED' })).problem).toBe(
      'mirror_gated',
    );
    expect(describeInstall(job({ state: 'failed', error: 'boom' }))).toMatchObject({
      problem: null,
      recovery: null,
      reason: 'boom',
    });
  });

  it('never carries failure details on a running job', () => {
    const view = describeInstall(job({ docs_topic: 'DISK_SPACE_LOW', error: 'stale' }));
    expect(view).toMatchObject({ problem: null, reason: null, recovery: null, retryAfter: 0 });
  });
});

describe('oneLineReason', () => {
  it('keeps the first meaningful line and caps its length', () => {
    expect(oneLineReason('\n  Traceback (x)\n  HTTP 403   Forbidden  \nmore')).toBe(
      'HTTP 403 Forbidden',
    );
    const long = oneLineReason('x'.repeat(400));
    expect(long).toHaveLength(160);
    expect(long?.endsWith('…')).toBe(true);
    expect(oneLineReason('')).toBeNull();
  });
});

describe('describeInstallGroup', () => {
  it('sums a pack into one progress with an ETA from what is left', () => {
    const view = describeInstallGroup([
      job({ repo_id: 'a', bytes_done: 100, total_bytes: 400, rate: 10 }),
      job({ repo_id: 'b', bytes_done: 100, total_bytes: 100 }),
      job({ repo_id: 'c', state: 'failed' }),
    ]);
    expect(view).toMatchObject({ stage: 'downloading', pct: 40, rate: 10, etaSeconds: 30 });
    expect(describeInstallGroup([job({ state: 'failed' })])).toBeNull();
  });
});

describe('terminal views and ETA', () => {
  it('keeps a start failure classified', () => {
    expect(terminalInstallView('failed', 'offline\ntrace', 'DISK_SPACE_LOW')).toMatchObject({
      stage: 'failed',
      reason: 'offline',
      problem: 'disk',
    });
    expect(terminalInstallView('cancelled')).toMatchObject({ stage: 'cancelled', reason: null });
  });

  it('formats remaining time with the locale', () => {
    expect(formatInstallEta(0)).toBe('');
    expect(formatInstallEta(42, 'en')).toMatch(/42\s*sec/);
    expect(formatInstallEta(150, 'en')).toMatch(/3\s*min/);
    expect(formatInstallEta(5400, 'en')).toMatch(/1\.5\s*hr/);
  });

  it('moves to the next unit when rounding up reaches it', () => {
    // A rate-derived ETA is fractional, so it can sit just under a unit boundary.
    expect(formatInstallEta(59.4, 'en')).toMatch(/^1\s*min$/);
    expect(formatInstallEta(3540.5, 'en')).toMatch(/^1\s*hr$/);
    expect(formatInstallEta(3599, 'en')).toMatch(/^1\s*hr$/);
    expect(formatInstallEta(59, 'en')).toMatch(/^59\s*sec$/);
    expect(formatInstallEta(3540, 'en')).toMatch(/^59\s*min$/);
  });
});
