import { mkdtempSync, readFileSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { describe, expect, it, vi } from 'vitest';
import {
  installMainProcessErrorHandlers,
  MainErrorJournal,
  observeMainProcessTask,
  type MainProcessErrorSource,
} from './main-error-journal';

function journalPath(): string {
  return join(mkdtempSync(join(tmpdir(), 'voicestudio-main-errors-')), 'errors.json');
}

describe('MainErrorJournal', () => {
  it('persists a bounded fatal-error history that survives restart', () => {
    const path = journalPath();
    const journal = new MainErrorJournal(path, '1.2.3');
    for (let index = 0; index < 25; index += 1) {
      journal.record(new Error(`failure-${index}`), 'uncaughtException', new Date(index * 1_000));
    }

    const records = JSON.parse(readFileSync(path, 'utf8')) as Array<{ detail: string }>;
    expect(records).toHaveLength(20);
    expect(records[0].detail).toContain('failure-5');
    expect(new MainErrorJournal(path, '1.2.3').recent(2)).toContain('failure-24');
  });

  it('recovers from corrupt state and records non-Error rejection values', () => {
    const path = journalPath();
    writeFileSync(path, 'corrupt');
    const journal = new MainErrorJournal(path, '1.2.3');
    journal.record({ reason: 'rejected' }, 'unhandledRejection', new Date(0));

    expect(journal.recent()).toContain('unhandledRejection');
    expect(journal.recent()).toContain('rejected');
  });

  it('scrubs home paths and credential-shaped values before persistence', () => {
    const path = journalPath();
    const credential = `sk-${'a'.repeat(24)}`;
    const journal = new MainErrorJournal(path, '1.2.3');
    journal.record(
      new Error(`failed in C:\\Users\\private-user\\project with ${credential}`),
      'uncaughtException',
    );

    const persisted = readFileSync(path, 'utf8');
    expect(persisted).not.toContain('private-user');
    expect(persisted).not.toContain(credential);
  });

  it('journals monitored exceptions and contains rejected background work', () => {
    const path = journalPath();
    const journal = new MainErrorJournal(path, '1.2.3');
    const listeners = new Map<string, (...args: unknown[]) => void>();
    const source = {
      on: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
        listeners.set(event, listener);
      }),
      removeListener: vi.fn((event: string, listener: (...args: unknown[]) => void) => {
        if (listeners.get(event) === listener) listeners.delete(event);
      }),
    } as unknown as MainProcessErrorSource;
    const report = vi.fn();
    const close = installMainProcessErrorHandlers(journal, source, report);

    listeners.get('uncaughtExceptionMonitor')?.(new Error('fatal'), 'uncaughtException');
    const rejection = new Error('background failed');
    listeners.get('unhandledRejection')?.(rejection);

    expect(journal.recent()).toContain('fatal');
    expect(journal.recent()).toContain('background failed');
    expect(report).toHaveBeenCalledWith(rejection);
    close();
    expect(listeners.size).toBe(0);
  });

  it('consumes detached task failures with their operation context', async () => {
    const journal = new MainErrorJournal(journalPath(), '1.2.3');
    const report = vi.fn();

    observeMainProcessTask(Promise.reject(new Error('pipe closed')), journal, 'Backend startup', report);
    await Promise.resolve();

    expect(journal.recent()).toContain('Backend startup: pipe closed');
    expect(report).toHaveBeenCalledTimes(1);
  });
});
