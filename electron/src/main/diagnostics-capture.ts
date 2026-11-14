import type { BackendStatus } from '../preload/index.d';
import { diagnosticLimit, projectException, type DiagnosticEvent } from './diagnostics-schema';
import type { DiagnosticReports } from './diagnostics';
import type { MainProcessErrorSource } from './main-error-journal';

const incompatible = new Set([
  'WINDOWS_APP_CONTROL_BLOCKED',
  'ANTIVIRUS_BLOCKED',
  'EXEC_REFUSED',
  'GATEKEEPER_QUARANTINE',
  'EXEC_NOEXEC_MOUNT',
  'SYSTEM_LIBRARY_PROBLEM',
  'UNSUPPORTED_PLATFORM',
  'SOURCE_ENV_INCOMPLETE',
  'BROKEN_VENV',
  'ROCM_NOT_CONFIGURED',
]);

export function elapsedBucket(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 0) return 'unknown';
  if (seconds < 10) return 'lt_10s';
  if (seconds < 60) return 'lt_1m';
  if (seconds < 600) return 'lt_10m';
  if (seconds < 3600) return 'lt_1h';
  return seconds < 86400 ? 'lt_1d' : 'ge_1d';
}

/** Observes existing verdicts; it never interrupts, restarts or relabels work. */
export class DiagnosticStatusObserver {
  private previousStage = '';
  private previousElapsed = 0;
  private reported = new Set<string>();

  constructor(private readonly reports: Pick<DiagnosticReports, 'capture'>) {}

  observe(status: BackendStatus): void {
    if (status.remote) return;
    if (
      (['installing', 'starting'].includes(status.stage) && status.stage !== this.previousStage) ||
      status.elapsedMs < this.previousElapsed
    )
      this.reported.clear();
    this.previousStage = status.stage;
    this.previousElapsed = status.elapsedMs;
    const progress = status.setupProgress;
    if (
      status.stage === 'installing' &&
      progress?.stalled &&
      (progress.stalledSeconds ?? 0) * 1000 >= diagnosticLimit('stall_ms', 600_000)
    ) {
      this.once('stall', {
        category: 'install_stalled',
        stage: 'download',
        error_code: 'RUNTIME_DOWNLOAD_STALLED',
        reason: 'no_progress',
        elapsed_bucket: elapsedBucket(progress.stalledSeconds ?? 0),
      });
    }
    const failure = status.failure;
    if (!failure || !['failed', 'crashed', 'setup_required', 'port_in_use'].includes(status.stage))
      return;
    const native = failure.code.startsWith('NATIVE_');
    const stalled = failure.code === 'RUNTIME_DOWNLOAD_STALLED';
    const category = native
      ? 'backend_crash'
      : stalled
        ? 'install_stalled'
        : incompatible.has(failure.code)
          ? 'incompatibility'
          : status.stage === 'setup_required'
            ? 'install_failed'
            : 'operation_failed';
    this.once(`${status.stage}:${failure.code}`, {
      category,
      source: 'electron_main',
      stage: status.stage === 'setup_required' ? 'setup' : 'startup',
      error_code: failure.code,
      exit_code: status.exitCode ?? failure.exitCode,
      signal: status.exitSignal ?? failure.signal,
      reason: native ? 'crashed' : stalled ? 'no_progress' : 'launch_failed',
      elapsed_bucket: elapsedBucket(failure.elapsedSeconds ?? status.elapsedMs / 1000),
    });
  }
  private once(key: string, event: DiagnosticEvent): void {
    if (this.reported.has(key)) return;
    this.reported.add(key);
    this.reports.capture(event);
  }
}

export function installDiagnosticProcessCapture(
  reports: Pick<DiagnosticReports, 'capture' | 'enabled'>,
  source: MainProcessErrorSource = process,
): () => void {
  const capture = (error: unknown, reason: string) => {
    try {
      if (!reports.enabled) return;
      if (
        error instanceof Error &&
        ['AbortError', 'CanceledError', 'CancelledError'].includes(error.name)
      )
        return;
      reports.capture({
        category: 'app_crash',
        stage: 'other',
        reason,
        ...projectException(error),
      });
    } catch {
      /* Even a hostile exception getter must not break a fatal hook. */
    }
  };
  const fatal = (error: Error) => capture(error, 'unhandled_exception');
  const rejected = (error: unknown) => capture(error, 'unhandled_rejection');
  source.on('uncaughtExceptionMonitor', fatal);
  source.on('unhandledRejection', rejected);
  return () => {
    source.removeListener('uncaughtExceptionMonitor', fatal);
    source.removeListener('unhandledRejection', rejected);
  };
}

/** Electron provides these reasons; normal/expected exits and arbitrary strings are ignored. */
export function captureProcessGone(
  reports: Pick<DiagnosticReports, 'capture'>,
  renderer: boolean,
  reason: string,
  exitCode: number,
  expected = false,
): void {
  if (
    expected ||
    !['crashed', 'oom', 'killed', 'abnormal-exit', 'launch-failed', 'integrity-failure'].includes(
      reason,
    )
  )
    return;
  reports.capture({
    category: renderer ? 'renderer_crash' : 'app_crash',
    stage: renderer ? 'render' : 'other',
    reason:
      (
        {
          'abnormal-exit': 'process_exit',
          'launch-failed': 'launch_failed',
          'integrity-failure': 'integrity_failure',
        } as Record<string, string>
      )[reason] ?? reason,
    exit_code: exitCode,
  });
}
