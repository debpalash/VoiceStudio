import { createHash } from 'node:crypto';
import rawSchema from '../../../config/diagnostics.json';
import type { DiagnosticCategory, DiagnosticStage } from '../preload/diagnostics';

export type DiagnosticProps = Record<string, string | number | boolean>;
export interface DiagnosticRecord {
  version: 1;
  id: string;
  timestamp_ms: number;
  epoch: string;
  props: DiagnosticProps;
}
export interface DiagnosticConsent {
  version: 1;
  enabled: boolean;
  epoch: string;
  profile_id: string;
}
export interface DiagnosticEvent {
  category: DiagnosticCategory;
  source?: string;
  stage?: DiagnosticStage | string;
  error_code?: string;
  error_type?: string;
  engine_id?: string;
  frames?: string;
  reason?: string;
  device_family?: string;
  exit_code?: number;
  signal?: string;
  uptime_bucket?: string;
  elapsed_bucket?: string;
  memory_bucket?: string;
  disk_bucket?: string;
}
export interface DiagnosticIdentity {
  app_version: string;
  build_commit: string;
  platform: string;
  arch: string;
}

const schema = rawSchema as Record<string, unknown>;
export const HEX_ID = /^[a-f0-9]{32}$/;
const fields: Record<string, string> = {
  category: 'categories',
  source: 'sources',
  stage: 'stages',
  error_code: 'error_codes',
  error_type: 'error_types',
  engine_id: 'engine_ids',
  platform: 'platforms',
  arch: 'arches',
  device_family: 'device_families',
  signal: 'signals',
  reason: 'reasons',
  uptime_bucket: 'buckets',
  elapsed_bucket: 'buckets',
  memory_bucket: 'buckets',
  disk_bucket: 'buckets',
};
const fallback: Record<string, string> = {
  stage: 'other',
  error_code: 'UNKNOWN',
  error_type: 'OtherError',
  engine_id: 'unknown',
  platform: 'unknown',
  arch: 'unknown',
  device_family: 'unknown',
  signal: 'unknown',
  reason: 'unknown',
  uptime_bucket: 'unknown',
  elapsed_bucket: 'unknown',
  memory_bucket: 'unknown',
  disk_bucket: 'unknown',
};
const enums = new Map(
  Object.entries(fields).map(([field, list]) => [
    field,
    new Set(
      Array.isArray(schema[list])
        ? (schema[list] as unknown[]).filter((v): v is string => typeof v === 'string')
        : [],
    ),
  ]),
);
const knownFrames = new Set([
  'electron_main',
  'renderer',
  'preload',
  ...(Array.isArray(schema.frame_paths)
    ? schema.frame_paths.filter((v): v is string => typeof v === 'string')
    : []),
]);
const limits = (schema.limits ?? {}) as Record<string, unknown>;
export function diagnosticLimit(key: string, defaultValue: number): number {
  const value = limits[key];
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : defaultValue;
}
export const MAX_RECORDS = Math.min(
  64,
  diagnosticLimit('queue_count', 64),
  diagnosticLimit('main_queue_count', 32),
);
export const MAX_QUEUE_BYTES = Math.min(
  512 * 1024,
  diagnosticLimit('queue_bytes', 512 * 1024),
  diagnosticLimit('main_queue_bytes', 256 * 1024),
);
export const TTL_MS = Math.min(7 * 86400_000, diagnosticLimit('ttl_ms', 7 * 86400_000));

export function enumValue(field: string, value: unknown): string | undefined {
  const allowed = enums.get(field);
  if (typeof value === 'string' && allowed?.has(value)) return value;
  return fallback[field];
}

/** Only exact app-owned locations survive; arbitrary filenames and source lines never do. */
export function sanitizeFrames(value: unknown): string | undefined {
  if (typeof value !== 'string' || Buffer.byteLength(value) > 1800) return undefined;
  const kept: string[] = [];
  for (const line of value.split('\n').slice(0, 24)) {
    const match = /^([^:\s]+):(\d{1,8})(?::(\d{1,8}))?$/.exec(line.trim());
    if (!match) continue;
    const location = /^assets\/[A-Za-z0-9_-]+\.(?:js|mjs)$/.test(match[1]) ? 'renderer' : match[1];
    if (!knownFrames.has(location)) continue;
    if (Number(match[2]) < 1 || Number(match[2]) > 10_000_000) continue;
    if (match[3] && (Number(match[3]) < 1 || Number(match[3]) > 10_000_000)) continue;
    const frame = `${location}:${Number(match[2])}${match[3] ? `:${Number(match[3])}` : ''}`;
    if (Buffer.byteLength([...kept, frame].join('\n')) > 1800) break;
    kept.push(frame);
  }
  return kept.length ? kept.join('\n') : undefined;
}

export function technicalFingerprint(props: DiagnosticProps): string {
  const technical = Object.fromEntries(
    Object.keys(props)
      .filter((key) => key !== 'fingerprint' && key !== 'occurrences' && key !== 'schema_version')
      .sort()
      .map((key) => [key, props[key]]),
  );
  return createHash('sha256').update(JSON.stringify(technical)).digest('hex');
}

/** Rebuild rather than spread any caller/disk data. This runs again before delivery. */
export function sanitizeProperties(value: unknown): DiagnosticProps | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const input = value as Record<string, unknown>;
  const category = enumValue('category', input.category);
  const source = enumValue('source', input.source);
  if (!category || !source) return null;
  const out: DiagnosticProps = { category, source, schema_version: 1 };
  for (const key of Object.keys(fields)) {
    if (key === 'category' || key === 'source' || (!(key in input) && key.endsWith('_bucket')))
      continue;
    const safe = enumValue(key, input[key]);
    if (safe !== undefined) out[key] = safe;
  }
  if (
    typeof input.app_version === 'string' &&
    input.app_version.length <= 64 &&
    /^\d{1,4}\.\d{1,4}\.\d{1,4}(?:-[a-z0-9]+(?:[.-][a-z0-9]+)*)?$/.test(input.app_version)
  )
    out.app_version = input.app_version;
  if (typeof input.build_commit === 'string' && /^[a-f0-9]{7,40}$/i.test(input.build_commit))
    out.build_commit = input.build_commit.toLowerCase();
  const frames = sanitizeFrames(input.frames);
  if (frames) out.frames = frames;
  if (
    Number.isInteger(input.exit_code) &&
    (input.exit_code as number) >= -0x80000000 &&
    (input.exit_code as number) <= 0xffffffff
  )
    out.exit_code = input.exit_code as number;
  out.occurrences =
    typeof input.occurrences === 'number' && Number.isInteger(input.occurrences)
      ? Math.max(1, Math.min(1_000_000, input.occurrences))
      : 1;
  out.fingerprint = technicalFingerprint(out);
  return out;
}

const UI_KEYS = new Set([
  'category',
  'source',
  'stage',
  'error_code',
  'error_type',
  'engine_id',
  'frames',
  'reason',
]);
export function validUiInput(value: unknown): value is DiagnosticEvent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const input = value as Record<string, unknown>;
  return (
    Object.keys(input).every((key) => UI_KEYS.has(key)) &&
    enumValue('category', input.category) !== undefined &&
    (input.source === undefined || input.source === 'renderer') &&
    Object.values(input).every(
      (field) => field === undefined || (typeof field === 'string' && field.length <= 16_384),
    )
  );
}

export function validConsent(value: unknown): value is DiagnosticConsent {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return false;
  const v = value as DiagnosticConsent;
  return (
    v.version === 1 &&
    typeof v.enabled === 'boolean' &&
    typeof v.epoch === 'string' &&
    HEX_ID.test(v.epoch) &&
    typeof v.profile_id === 'string' &&
    /^install_[a-f0-9]{32}$/.test(v.profile_id)
  );
}

export function sanitizeRecord(
  value: unknown,
  epoch: string,
  now: number,
): DiagnosticRecord | null {
  if (!value || typeof value !== 'object' || Array.isArray(value)) return null;
  const r = value as DiagnosticRecord;
  if (
    r.version !== 1 ||
    typeof r.id !== 'string' ||
    !HEX_ID.test(r.id) ||
    r.epoch !== epoch ||
    !Number.isSafeInteger(r.timestamp_ms) ||
    r.timestamp_ms < now - TTL_MS ||
    r.timestamp_ms > now + 60_000
  )
    return null;
  const props = sanitizeProperties(r.props);
  return props ? { version: 1, id: r.id, timestamp_ms: r.timestamp_ms, epoch, props } : null;
}

/** Error messages and the first stack line are intentionally never inspected. */
export function projectException(error: unknown): Pick<DiagnosticEvent, 'error_type' | 'frames'> {
  try {
    if (!(error instanceof Error)) return { error_type: 'OtherError' };
    const error_type = enumValue('error_type', error.name) ?? 'OtherError';
    const frames: string[] = [];
    for (const line of String(error.stack ?? '')
      .slice(0, 32_768)
      .split('\n')
      .slice(1, 81)) {
      const match = /(?:\(|\s)([^()]+):(\d{1,8}):(\d{1,8})\)?$/.exec(line.trim());
      if (!match) continue;
      const path = match[1].replaceAll('\\', '/');
      const known = [...knownFrames].find((candidate) => path.endsWith(`/${candidate}`));
      const bundled = /\/(?:out\/)?main\/index\.(?:js|mjs)$/.test(path)
        ? 'electron_main'
        : /\/(?:out\/)?preload\/index\.(?:js|mjs)$/.test(path)
          ? 'preload'
          : undefined;
      if (known || bundled) {
        const frame = `${known ?? bundled}:${match[2]}:${match[3]}`;
        if (Buffer.byteLength([...frames, frame].join('\n')) > 1800 || frames.length >= 24) break;
        frames.push(frame);
      }
    }
    return { error_type, frames: sanitizeFrames(frames.join('\n')) };
  } catch {
    return { error_type: 'OtherError' };
  }
}
