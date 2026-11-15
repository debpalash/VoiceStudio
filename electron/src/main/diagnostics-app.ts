import analyticsConfig from '../../../config/analytics.json';
import type { DiagnosticProps } from './diagnostics-schema';

export interface DiagnosticAppDestination {
  appId: string;
  ingestKey: string;
  host: string;
  sessionPings: boolean;
}
export function safeDiagnosticUrl(value: string, origin = false): boolean {
  try {
    const url = new URL(value);
    const literal = /^http:\/\/(\[[^\]]+\]|[^:/?#]+)(?::[0-9]+)?(?:\/|$)/i
      .exec(value)?.[1]
      ?.toLowerCase();
    return (
      (url.protocol === 'https:' ||
        (url.protocol === 'http:' &&
          ['localhost', '127.0.0.1', '[::1]'].includes(literal ?? ''))) &&
      Boolean(url.hostname) &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      (!origin || url.pathname === '/') &&
      Buffer.byteLength(value) <= 1988 &&
      !/[\x00-\x20]/.test(value)
    );
  } catch {
    return false;
  }
}
export function diagnosticAppDestination(
  env: NodeJS.ProcessEnv,
  generated: unknown = typeof __SUPERMETRY_APP_CONFIG__ === 'undefined'
    ? undefined
    : __SUPERMETRY_APP_CONFIG__,
): DiagnosticAppDestination | null {
  const config =
    generated && typeof generated === 'object' && !Array.isArray(generated)
      ? (generated as Record<string, unknown>)
      : {};
  const text = (value: unknown) => (typeof value === 'string' ? value.trim() : '');
  const appId = text(env.SUPERMETRY_APP_ID ?? config.app_id);
  const ingestKey = text(env.SUPERMETRY_INGEST_KEY ?? config.ingest_key);
  const selectedHost = text(env.SUPERMETRY_APP_HOST ?? config.host).replace(/\/+$/, '');
  const host = safeDiagnosticUrl(selectedHost, true)
    ? selectedHost
    : text(analyticsConfig.host).replace(/\/$/, '');
  if (
    !/^[a-z0-9]{6,32}$/.test(appId) ||
    !/^smk_[a-z2-7]{39}$/.test(ingestKey) ||
    !safeDiagnosticUrl(host, true)
  )
    return null;
  return {
    appId,
    ingestKey,
    host,
    sessionPings: !!(
      config.diagnostics &&
      typeof config.diagnostics === 'object' &&
      (config.diagnostics as Record<string, unknown>).sessionPings === true
    ),
  };
}
export const appPlatform = (value: unknown): string => {
  const name = String(value ?? '').toLowerCase();
  if (['darwin', 'mac', 'osx'].includes(name)) return 'macos';
  if (['win32', 'win'].includes(name)) return 'windows';
  return ['windows', 'macos', 'linux', 'ios', 'android', 'web'].includes(name) ? name : 'other';
};
export const appArch = (value: unknown): string => {
  const name = String(value ?? '').toLowerCase();
  if (['x64', 'amd64', 'x86_64'].includes(name)) return 'x64';
  if (['arm64', 'aarch64'].includes(name)) return 'arm64';
  if (['ia32', 'x86'].includes(name)) return 'x86';
  return ['arm', 'armv7l'].includes(name) ? 'arm' : 'other';
};
const tagKeys = [
  'category',
  'source',
  'stage',
  'error_code',
  'engine_id',
  'uptime_bucket',
  'elapsed_bucket',
  'memory_bucket',
  'disk_bucket',
  'schema_version',
];
/** Only explicitly named, already sanitized failure fields enter app intake. */
export function diagnosticAppFields(props: DiagnosticProps): Record<string, unknown> {
  const category = props.category;
  const crash = ['app_crash', 'backend_crash', 'renderer_crash'].includes(String(category));
  const kind = crash
    ? 'crash'
    : ['install_failed', 'install_stalled', 'incompatibility'].includes(String(category))
      ? category
      : [
            'generation_failed',
            'model_load_failed',
            'engine_failed',
            'download_failed',
            'operation_failed',
          ].includes(String(category))
        ? 'operation_failed'
        : 'error';
  const tags: DiagnosticProps = {};
  for (const key of tagKeys) {
    const value = props[key];
    if (value !== undefined && value !== '' && Buffer.byteLength(String(value)) <= 200)
      tags[key] = value;
  }
  const event: Record<string, unknown> = {
    kind,
    level: crash ? 'fatal' : 'error',
    handled: !crash,
  };
  for (const [from, to, cap] of [
    ['app_version', 'release', 64],
    ['build_commit', 'build', 64],
    ['device_family', 'device_class', 32],
    ['reason', 'exit_reason', 32],
    ['error_type', 'error_type', 64],
  ] as const) {
    const value = props[from];
    if (typeof value === 'string' && value && Buffer.byteLength(value) <= cap) event[to] = value;
  }
  event.platform = appPlatform(props.platform);
  event.arch = appArch(props.arch);
  for (const key of ['signal', 'exit_code', 'frames', 'fingerprint', 'occurrences'])
    if (
      props[key] !== undefined &&
      (key !== 'signal' || Buffer.byteLength(String(props[key])) <= 32)
    )
      event[key] = props[key];
  event.tags = tags;
  return event;
}
export type AppDropReason = 'too_old' | 'invalid' | 'duplicate' | 'rate_limited';
export interface DiagnosticAppReply {
  terminal: number[];
  dropped: { index: number; reason: AppDropReason }[];
  warnings: number;
}
export async function diagnosticAppReply(
  response: Response,
  count: number,
): Promise<DiagnosticAppReply | null> {
  try {
    if (!response.body) return null;
    const reader = response.body.getReader();
    const chunks: Uint8Array[] = [];
    let size = 0;
    try {
      while (true) {
        const value = await reader.read();
        if (value.done) break;
        size += value.value.byteLength;
        if (size > 16_384) return null;
        chunks.push(value.value);
      }
    } finally {
      await reader.cancel().catch(() => {});
    }
    const value = JSON.parse(Buffer.concat(chunks).toString('utf8'));
    if (
      !value ||
      typeof value !== 'object' ||
      !Number.isInteger(value.accepted) ||
      value.accepted < 0 ||
      !Array.isArray(value.dropped) ||
      value.accepted + value.dropped.length !== count
    )
      return null;
    const used = new Set<number>();
    const dropped: DiagnosticAppReply['dropped'] = [];
    for (const drop of value.dropped) {
      if (
        !drop ||
        !Number.isInteger(drop.index) ||
        drop.index < 0 ||
        drop.index >= count ||
        used.has(drop.index) ||
        !['too_old', 'invalid', 'duplicate', 'rate_limited'].includes(drop.reason)
      )
        return null;
      used.add(drop.index);
      dropped.push({ index: drop.index, reason: drop.reason });
    }
    const retained = new Set(
      dropped.filter((drop) => drop.reason === 'rate_limited').map((drop) => drop.index),
    );
    const warnings = Array.isArray(value.warnings)
      ? value.warnings.filter((warning: unknown) => {
          if (!warning || typeof warning !== 'object') return false;
          const row = warning as Record<string, unknown>;
          return (
            Number.isInteger(row.index) &&
            (row.index as number) >= 0 &&
            (row.index as number) < count &&
            row.reason === 'tag_not_allowed'
          );
        }).length
      : 0;
    return {
      terminal: Array.from({ length: count }, (_, index) => index).filter(
        (index) => !retained.has(index),
      ),
      dropped,
      warnings,
    };
  } catch {
    return null;
  }
}
