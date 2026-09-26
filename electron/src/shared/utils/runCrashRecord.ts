export interface BackendCrashMarker {
  /** Unix seconds when the death was detected. */
  ts: number;
  exit_code: number | null;
  signal: number | null;
  /** Human-readable ExitStatus display ("exit status: 134", …). */
  exit_desc: string;
  backend_version: string;
  /** Seconds the backend had been running when it died. */
  uptime_s: number;
  /** Tail of backend_err.log captured at death time (~40 lines). */
  last_stderr: string;
  /** Whether the user already viewed/dismissed this crash. */
  acknowledged: boolean;
}

export interface LastRunCrashRecord {
  detected_at: number;
  started_at: number | null;
  ended_between: [number, number];
  uptime_hint_s: number | null;
  version: string;
  last_activity: { ts: number | null; kind: string; detail: string | null } | null;
  log_tail: string[];
}

/** Adapt a run-sentinel record to the CrashMarker shape the whole crash UI
 * already speaks. A sentinel can't know an exit code (the process died out
 * from under it), so exit_code/signal are null and exit_desc carries the
 * story; describeCrashExit() falls through to exit_desc for exactly this
 * shape. Exported for unit tests. */
export function _adaptLastRunCrash(
  record: LastRunCrashRecord,
  acknowledged: boolean,
): BackendCrashMarker {
  const activity = record.last_activity;
  const activityLine = activity?.kind
    ? [
        `last activity before the death: ${activity.kind}${activity.detail ? ` (${activity.detail})` : ''}`,
        '',
      ]
    : [];
  return {
    ts: Math.round(record.detected_at || 0),
    exit_code: null,
    signal: null,
    exit_desc: 'process ended uncleanly (previous run)',
    backend_version: record.version || '',
    uptime_s: Math.max(0, Math.round(record.uptime_hint_s ?? 0)),
    last_stderr: [...activityLine, ...(Array.isArray(record.log_tail) ? record.log_tail : [])]
      .join('\n')
      .trim(),
    acknowledged,
  };
}
