import { mkdirSync, readFileSync, renameSync, writeFileSync } from 'node:fs';
import { dirname } from 'node:path';
import { scrubText } from '../shared/utils/scrub';

const MAX_RECORDS = 20;
const MAX_DETAIL = 20_000;

interface MainErrorRecord {
  at: string;
  version: string;
  origin: NodeJS.UncaughtExceptionOrigin;
  detail: string;
}

export interface MainProcessErrorSource {
  on(
    event: 'uncaughtExceptionMonitor',
    listener: (error: Error, origin: NodeJS.UncaughtExceptionOrigin) => void,
  ): unknown;
  on(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
  removeListener(
    event: 'uncaughtExceptionMonitor',
    listener: (error: Error, origin: NodeJS.UncaughtExceptionOrigin) => void,
  ): unknown;
  removeListener(event: 'unhandledRejection', listener: (reason: unknown) => void): unknown;
}

function errorDetail(error: unknown): string {
  if (error instanceof Error)
    return scrubText(error.stack || `${error.name}: ${error.message}`).slice(0, MAX_DETAIL);
  try {
    return scrubText(JSON.stringify(error)).slice(0, MAX_DETAIL);
  } catch {
    return scrubText(String(error)).slice(0, MAX_DETAIL);
  }
}

/** Durable evidence for fatal Electron-main failures. Recording must never mask the original crash. */
export class MainErrorJournal {
  private records: MainErrorRecord[] = [];

  constructor(
    private readonly path: string,
    private readonly version: string,
  ) {
    try {
      const parsed: unknown = JSON.parse(readFileSync(path, 'utf8'));
      if (Array.isArray(parsed)) {
        this.records = parsed
          .filter(
            (value): value is MainErrorRecord =>
              Boolean(value) &&
              typeof value.at === 'string' &&
              typeof value.version === 'string' &&
              (value.origin === 'uncaughtException' || value.origin === 'unhandledRejection') &&
              typeof value.detail === 'string',
          )
          .slice(-MAX_RECORDS);
      }
    } catch {
      /* missing or corrupt journal */
    }
  }

  record(error: unknown, origin: NodeJS.UncaughtExceptionOrigin, now = new Date()): void {
    this.records.push({
      at: now.toISOString(),
      version: this.version,
      origin,
      detail: errorDetail(error),
    });
    this.records = this.records.slice(-MAX_RECORDS);
    try {
      mkdirSync(dirname(this.path), { recursive: true });
      const temporary = `${this.path}.tmp`;
      writeFileSync(temporary, JSON.stringify(this.records), { encoding: 'utf8', mode: 0o600 });
      renameSync(temporary, this.path);
    } catch {
      /* preserve the original fatal error */
    }
  }

  recent(limit = 5): string {
    return this.records
      .slice(-Math.max(0, limit))
      .map((record) => `[${record.at}] ${record.origin}\n${record.detail}`)
      .join('\n\n');
  }
}

/** Record fatal exceptions and keep rejected background work from terminating Electron. */
export function installMainProcessErrorHandlers(
  journal: MainErrorJournal,
  source: MainProcessErrorSource = process,
  report: (reason: unknown) => void = (reason) =>
    console.error('[main] Background operation rejected', reason),
): () => void {
  const fatal = (error: Error, origin: NodeJS.UncaughtExceptionOrigin) =>
    journal.record(error, origin);
  const rejected = (reason: unknown) => {
    journal.record(reason, 'unhandledRejection');
    report(reason);
  };
  source.on('uncaughtExceptionMonitor', fatal);
  source.on('unhandledRejection', rejected);
  return () => {
    source.removeListener('uncaughtExceptionMonitor', fatal);
    source.removeListener('unhandledRejection', rejected);
  };
}

/** Consume a deliberately detached main-process task while preserving actionable evidence. */
export function observeMainProcessTask(
  task: Promise<unknown>,
  journal: MainErrorJournal,
  label: string,
  report: (reason: unknown) => void = (reason) => console.error(`[main] ${label}`, reason),
): void {
  void task.catch((reason) => {
    const source = reason instanceof Error ? reason : new Error(String(reason));
    const error = new Error(`${label}: ${source.message}`, { cause: source });
    journal.record(error, 'unhandledRejection');
    report(error);
  });
}
