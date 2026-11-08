/** What the backend was doing, and the memory left, at its last reported activity. */
export interface CrashContext {
  kind: string;
  detail?: string;
  engine?: string;
  device?: string;
  ram_free_gb?: number;
  ram_total_gb?: number;
  swap_free_gb?: number;
  swap_total_gb?: number;
  seconds_before_exit?: number;
}

export const CRASH_CONTEXT_PREFIX = 'VOICESTUDIO_CRASH_CONTEXT=';

/** Identifiers only: a value shaped like a path or free text is dropped, not scrubbed. */
const IDENTIFIER = /^[\w.:-]{1,80}$/;
const TEXT_FIELDS = ['detail', 'engine', 'device'] as const;
const NUMBER_FIELDS = ['ram_free_gb', 'ram_total_gb', 'swap_free_gb', 'swap_total_gb'] as const;

const identifier = (value: unknown): value is string =>
  typeof value === 'string' && IDENTIFIER.test(value);

/** A log line can come from anything the backend loads, so nothing in it is trusted. */
export function parseCrashContext(line: string): CrashContext | undefined {
  if (!line.startsWith(CRASH_CONTEXT_PREFIX)) return undefined;
  let parsed: unknown;
  try {
    parsed = JSON.parse(line.slice(CRASH_CONTEXT_PREFIX.length));
  } catch {
    return undefined;
  }
  if (typeof parsed !== 'object' || parsed === null) return undefined;
  const fields = parsed as Record<string, unknown>;
  if (!identifier(fields.kind)) return undefined;
  const context: CrashContext = { kind: fields.kind };
  for (const name of TEXT_FIELDS) {
    const value = fields[name];
    if (identifier(value)) context[name] = value;
  }
  for (const name of NUMBER_FIELDS) {
    const value = fields[name];
    if (typeof value === 'number' && Number.isFinite(value) && value >= 0) context[name] = value;
  }
  return context;
}

/** Report lines, in English like the rest of the report's facts. */
export function formatCrashContext(context: CrashContext | null | undefined): string[] {
  if (!context) return [];
  const { kind, detail, engine, device, seconds_before_exit: seconds } = context;
  const { ram_free_gb: ramFree, ram_total_gb: ramTotal } = context;
  const { swap_free_gb: swapFree, swap_total_gb: swapTotal } = context;
  const lines = [
    `Last activity: ${kind}${detail ? ` ${detail}` : ''}${
      seconds != null ? ` (${seconds} s before exit)` : ''
    }`,
  ];
  if (engine) lines.push(`Engine: ${engine}`);
  if (device) lines.push(`Device: ${device}`);
  if (ramFree != null && ramTotal != null) {
    const swap =
      swapFree == null || swapTotal == null
        ? ''
        : swapTotal > 0
          ? `, ${swapFree} of ${swapTotal} GB swap`
          : ', no swap';
    lines.push(`Free memory then: ${ramFree} of ${ramTotal} GB RAM${swap}`);
  }
  return lines;
}
