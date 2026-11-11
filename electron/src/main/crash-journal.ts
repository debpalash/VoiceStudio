import { readFileSync, writeFileSync } from 'node:fs';
import { renameSyncWithRetry } from './rename-retry';
import type { NativeCrashRecord } from '../preload/index.d';
import { type CrashContext, parseCrashContext } from '../shared/utils/crashContext';
import { nativeCrashExcerpt } from '../shared/utils/crashReport';
import { describeExitCode, isNativeFaultExit } from '../shared/utils/nativeExit';
import { scrubText } from '../shared/utils/scrub';

/** Bounds a buffered fault dump: four GPU workers plus server threads at
 * Python's 100-frame faulthandler depth stay well inside it. */
const MAX_DUMP_LINES = 2000;

/** The frames of the thread that faulted: Python's dump calls it "Current thread". */
function faultingThread(lines: readonly string[]): string[] {
  const start = lines.findIndex((line) => line.trim().startsWith('Current thread '));
  if (start < 0) return [];
  const end = lines.findIndex((line, index) => index > start && line.trim().startsWith('Thread '));
  return lines.slice(start + 1, end < 0 ? undefined : end);
}

/** transformers' worker threads that read weights from the memory-mapped file. */
const THREADED_WEIGHT_LOAD =
  /transformers[\\/]core_model_loading\.py", line \d+ in (?:_materialize_copy|_job|spawn_materialize)\b/;

/**
 * A native crash whose faulting thread was one of transformers' weight-loading
 * workers (#3767, #3772: two machines, the same frame). transformers reads
 * weights on four threads unless HF_DEACTIVATE_ASYNC_LOAD is set, and loads one
 * at a time under memory pressure itself.
 */
export function threadedWeightLoadCrash(
  records: readonly NativeCrashRecord[],
): NativeCrashRecord | undefined {
  return records.find(
    (record) =>
      isNativeFaultExit({ exitCode: record.exitCode, signal: record.signal }) &&
      faultingThread(record.logTail).some((line) => THREADED_WEIGHT_LOAD.test(line)),
  );
}

export const SEQUENTIAL_WEIGHT_LOAD_ENV = 'HF_DEACTIVATE_ASYNC_LOAD';

/**
 * After such a crash, later starts of this version load weights on one thread.
 * It changes only load speed and is logged with its reason; the crash record
 * stays for the report, and an upgrade starts with the default again. A value
 * the user set is never overridden. Returns the log line, or undefined.
 */
export function applyWeightLoadFallback(
  env: NodeJS.ProcessEnv,
  records: readonly NativeCrashRecord[],
): string | undefined {
  if (env[SEQUENTIAL_WEIGHT_LOAD_ENV] !== undefined) return undefined;
  const crash = threadedWeightLoadCrash(records);
  if (!crash) return undefined;
  env[SEQUENTIAL_WEIGHT_LOAD_ENV] = '1';
  return (
    `A previous backend crashed natively (${describeExitCode(crash.exitCode, crash.signal ?? 'unknown')}) ` +
    `inside the threaded model weight loader; loading model weights one at a time ` +
    `(${SEQUENTIAL_WEIGHT_LOAD_ENV}=1).`
  );
}

/** Small version-scoped local journal. Read failures must never block startup. */
export class CrashJournal {
  private records: NativeCrashRecord[] = [];
  private nativeLines: string[] = [];
  private captureOpen = false;
  private streaming = false;
  /** Outlives resetCapture: the fault dump that follows must not erase it. */
  private context?: { value: CrashContext; receivedAt: number };
  resetCapture(streaming = true): void {
    this.nativeLines = [];
    this.captureOpen = false;
    this.streaming = streaming;
  }
  /** A new backend process: what the previous one was doing says nothing about it. */
  startProcess(): void {
    this.context = undefined;
    this.resetCapture();
  }
  /**
   * Buffer the whole all-thread dump before the supervisor ring evicts its
   * start. It is condensed only at record time (#2382): keeping the first 40
   * raw lines of the faulting thread used to fill the record with one
   * recursive torch frame and drop both the VoiceStudio frames that say what
   * was loading and every other thread.
   */
  captureLine(line: string): void {
    const context = parseCrashContext(line);
    if (context) {
      this.context = { value: context, receivedAt: Date.now() };
      return;
    }
    line = scrubText(line);
    this.streaming = true;
    const trimmed = line.trim();
    if (/^(?:Fatal Python error:|Windows fatal exception:)/.test(trimmed)) {
      this.resetCapture();
      this.nativeLines = [line.slice(0, 4096)];
      this.captureOpen = true;
      return;
    }
    if (!this.captureOpen) return;
    if (trimmed.startsWith('Extension modules:')) {
      this.captureOpen = false;
      return;
    }
    if (this.nativeLines.length < MAX_DUMP_LINES) this.nativeLines.push(line.slice(0, 4096));
  }
  constructor(
    private path: string,
    private version: string,
  ) {
    try {
      const stored: unknown = JSON.parse(readFileSync(path, 'utf8'));
      if (Array.isArray(stored))
        this.records = stored
          .filter(
            (
              value,
            ): value is Omit<NativeCrashRecord, 'acknowledged'> & {
              acknowledged?: boolean;
            } =>
              value &&
              value.version === version &&
              Number.isFinite(value.timestamp) &&
              Number.isFinite(value.uptimeMs) &&
              Array.isArray(value.logTail) &&
              value.logTail.every((line: unknown) => typeof line === 'string') &&
              (value.exitCode === null || Number.isInteger(value.exitCode)) &&
              (value.signal === null || typeof value.signal === 'string'),
          )
          .map((value) => ({
            ...value,
            logTail: value.logTail.map(scrubText),
            acknowledged: value.acknowledged === true,
          }))
          .slice(0, 3);
    } catch {
      /* missing or corrupt journal */
    }
  }
  latest(): NativeCrashRecord | undefined {
    return this.records[0];
  }
  /** Newest first; only this version's records. */
  all(): readonly NativeCrashRecord[] {
    return this.records;
  }
  acknowledgeLatest(): NativeCrashRecord | undefined {
    const latest = this.records[0];
    if (!latest || latest.acknowledged) return latest;
    this.records[0] = { ...latest, acknowledged: true };
    this.persist();
    return this.records[0];
  }
  record(
    exitCode: number | null,
    signal: string | null,
    uptimeMs: number,
    logTail: string[],
    /** The supervisor waits for a replacement backend before recording. */
    exitedAt: number = Date.now(),
  ): NativeCrashRecord | undefined {
    logTail = logTail.map(scrubText);
    const native = nativeCrashExcerpt(
      this.streaming ? this.nativeLines.join('\n') : logTail.join('\n'),
    );
    this.resetCapture(false);
    const context = this.context && {
      ...this.context.value,
      seconds_before_exit: Math.max(0, Math.round((exitedAt - this.context.receivedAt) / 1000)),
    };
    this.context = undefined;
    // EX_CONFIG is a port collision; Windows debugger termination is not a backend fault.
    if (exitCode === 78 || exitCode === 0x40010004) return undefined;
    this.records.unshift({
      timestamp: Date.now(),
      version: this.version,
      exitCode,
      signal,
      uptimeMs: Math.max(0, uptimeMs),
      logTail: native
        ? native
            .split('\n')
            .slice(0, 40)
            .map((line) => line.slice(0, 4096))
        : logTail.slice(-40).map((line) => line.slice(-4096)),
      ...(context ? { context } : {}),
      acknowledged: false,
    });
    this.records = this.records.slice(0, 3);
    this.persist();
    return this.records[0];
  }
  private persist(): void {
    try {
      writeFileSync(this.path + '.tmp', JSON.stringify(this.records), {
        encoding: 'utf8',
        mode: 0o600,
      });
      renameSyncWithRetry(this.path + '.tmp', this.path);
    } catch {
      /* retain in memory when storage is unavailable */
    }
  }
}
