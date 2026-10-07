import { readFileSync, writeFileSync, renameSync } from 'node:fs';
import type { NativeCrashRecord } from '../preload/index.d';
import { nativeCrashExcerpt } from '../shared/utils/crashReport';
import { scrubText } from '../shared/utils/scrub';

/** Bounds a buffered fault dump: four GPU workers plus server threads at
 * Python's 100-frame faulthandler depth stay well inside it. */
const MAX_DUMP_LINES = 2000;

/** Small version-scoped local journal. Read failures must never block startup. */
export class CrashJournal {
  private records: NativeCrashRecord[] = [];
  private nativeLines: string[] = [];
  private captureOpen = false;
  private streaming = false;
  resetCapture(streaming = true): void {
    this.nativeLines = [];
    this.captureOpen = false;
    this.streaming = streaming;
  }
  /**
   * Buffer the whole all-thread dump before the supervisor ring evicts its
   * start. It is condensed only at record time (#2382): keeping the first 40
   * raw lines of the faulting thread used to fill the record with one
   * recursive torch frame and drop both the VoiceStudio frames that say what
   * was loading and every other thread.
   */
  captureLine(line: string): void {
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
  ): NativeCrashRecord | undefined {
    logTail = logTail.map(scrubText);
    const native = nativeCrashExcerpt(
      this.streaming ? this.nativeLines.join('\n') : logTail.join('\n'),
    );
    this.resetCapture(false);
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
      renameSync(this.path + '.tmp', this.path);
    } catch {
      /* retain in memory when storage is unavailable */
    }
  }
}
