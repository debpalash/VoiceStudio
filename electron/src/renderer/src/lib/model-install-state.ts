/**
 * One state machine for every local model download the renderer shows:
 * queued -> downloading -> finishing (verify + install) -> ready, with
 * cancelling, cancelled and failed branches. Settings -> Models, first-run
 * setup, the feature prompts that install a missing model and the
 * notifications panel all read jobs through `describeInstall`, so the same
 * job never reads differently in two places.
 */

export type InstallStage =
  | 'queued'
  | 'downloading'
  | 'finishing'
  | 'cancelling'
  | 'cancelled'
  | 'failed'
  | 'ready';

/** What a failure needs the user to do, when the backend classified it. */
export type InstallProblem = 'disk' | 'gated' | 'mirror_gated' | 'network' | 'memory' | null;

export interface InstallJobLike {
  repo_id: string;
  state: string;
  bytes_done?: number;
  total_bytes?: number;
  rate?: number;
  eta_seconds?: number | null;
  error?: string;
  docs_topic?: string;
  retry_after_seconds?: number;
  failed_at?: number;
  phase?: string;
}

export interface InstallRecovery {
  to:
    | '/settings/storage'
    | '/settings/models'
    | '/settings/credentials'
    | '/settings/network'
    | '/settings/performance';
  /** i18n key naming the destination. */
  label: string;
}

export interface InstallView {
  stage: InstallStage;
  /** 0-100 once the total size is known. */
  pct: number | null;
  bytesDone: number;
  totalBytes: number | null;
  rate: number | null;
  etaSeconds: number | null;
  /** One line, safe to show inline; the full error stays in `detail`. */
  reason: string | null;
  detail: string | null;
  problem: InstallProblem;
  docsTopic: string | null;
  retryAfter: number;
  recovery: InstallRecovery | null;
}

export const ACTIVE_INSTALL_STAGES: ReadonlySet<InstallStage> = new Set([
  'queued',
  'downloading',
  'finishing',
  'cancelling',
]);

export const GATED_TOPICS: ReadonlySet<string> = new Set([
  'HF_AUTH_FAILED',
  'PYANNOTE_LICENSE_REQUIRED',
  'POCKETTTS_GATED_WEIGHTS',
]);

const NETWORK_TOPICS = new Set([
  'HF_MIRROR_UNREACHABLE',
  'SOCKS_PROXY_SUPPORT_MISSING',
  'SSL_HANDSHAKE_FAILURE',
  'TLS_CONNECTION_DROPPED',
]);

const MEMORY_TOPICS = new Set([
  'GPU_OOM',
  'COMPUTE_TYPE_UNSUPPORTED',
  'WINDOWS_PAGING_FILE_TOO_SMALL',
]);

export function installProblem(topic: string | null | undefined): InstallProblem {
  if (!topic) return null;
  if (topic === 'DISK_SPACE_LOW') return 'disk';
  if (topic === 'HF_MIRROR_GATED') return 'mirror_gated';
  if (GATED_TOPICS.has(topic)) return 'gated';
  if (NETWORK_TOPICS.has(topic)) return 'network';
  if (MEMORY_TOPICS.has(topic)) return 'memory';
  return null;
}

/** Where a failed download's docs topic is fixed. */
export function installRecovery(topic: string | null | undefined): InstallRecovery | null {
  switch (topic) {
    case 'DISK_SPACE_LOW':
    case 'AUDIO_IO_FAILED':
    case 'OS_INVALID_ARGUMENT':
      return { to: '/settings/storage', label: 'settings.storage' };
    case 'HF_MIRROR_UNREACHABLE':
    case 'HF_MIRROR_GATED':
      return { to: '/settings/models', label: 'models.mirror_title' };
    case 'SOCKS_PROXY_SUPPORT_MISSING':
    case 'SSL_HANDSHAKE_FAILURE':
    case 'TLS_CONNECTION_DROPPED':
      return { to: '/settings/network', label: 'settings.network' };
    default:
      if (topic && GATED_TOPICS.has(topic))
        return { to: '/settings/credentials', label: 'settings.credentials' };
      if (topic && MEMORY_TOPICS.has(topic))
        return { to: '/settings/performance', label: 'settings.perf_title' };
      return null;
  }
}

const REASON_LIMIT = 160;

/** The first meaningful line of an error, short enough for one row. */
export function oneLineReason(error: string | null | undefined): string | null {
  const line = (error ?? '')
    .split(/\r?\n/)
    .map((part) => part.trim())
    .find((part) => part && !/^traceback\b/i.test(part));
  if (!line) return null;
  const flat = line.replace(/\s+/g, ' ');
  return flat.length > REASON_LIMIT ? `${flat.slice(0, REASON_LIMIT - 1).trimEnd()}…` : flat;
}

function positive(value: number | null | undefined): number | null {
  return typeof value === 'number' && Number.isFinite(value) && value > 0 ? value : null;
}

function stageOf(job: InstallJobLike): InstallStage {
  if (job.state === 'cancelling' || job.phase === 'cancelling') return 'cancelling';
  if (job.state === 'failed') return 'failed';
  if (job.state === 'cancelled' || job.state === 'install_cancelled') return 'cancelled';
  if (job.state === 'done') return 'ready';
  const total = positive(job.total_bytes);
  const done = Math.max(0, job.bytes_done ?? 0);
  if (total && done >= total) return 'finishing';
  if (!total && done === 0) return 'queued';
  return 'downloading';
}

/** Read one backend install job as a stage the UI can name. */
export function describeInstall(job: InstallJobLike): InstallView {
  const stage = stageOf(job);
  const total = positive(job.total_bytes);
  const bytesDone = Math.max(0, job.bytes_done ?? 0);
  const failed = stage === 'failed';
  const topic = failed ? (job.docs_topic ?? null) : null;
  return {
    stage,
    pct:
      stage === 'finishing' || stage === 'ready'
        ? 100
        : total
          ? Math.min(100, Math.floor((bytesDone / total) * 100))
          : null,
    bytesDone,
    totalBytes: total,
    rate: stage === 'downloading' ? positive(job.rate) : null,
    etaSeconds: stage === 'downloading' ? positive(job.eta_seconds) : null,
    reason: failed ? oneLineReason(job.error) : null,
    detail: failed && job.error ? job.error : null,
    problem: installProblem(topic),
    docsTopic: topic,
    retryAfter: failed ? Math.max(0, Math.ceil(job.retry_after_seconds ?? 0)) : 0,
    recovery: installRecovery(topic),
  };
}

/**
 * Several downloads read as one (a model pack): summed bytes and speed, an
 * ETA from what is left, and the least advanced stage.
 */
export function describeInstallGroup(jobs: readonly InstallJobLike[]): InstallView | null {
  const views = jobs.map(describeInstall).filter((view) => ACTIVE_INSTALL_STAGES.has(view.stage));
  if (!views.length) return null;
  const sizes = views.every((view) => view.totalBytes !== null);
  const bytesDone = views.reduce((sum, view) => sum + view.bytesDone, 0);
  const totalBytes = sizes ? views.reduce((sum, view) => sum + (view.totalBytes ?? 0), 0) : null;
  const rate = views.reduce((sum, view) => sum + (view.rate ?? 0), 0) || null;
  const order: InstallStage[] = ['queued', 'downloading', 'finishing', 'cancelling'];
  const stage = views.some((view) => view.stage === 'downloading')
    ? 'downloading'
    : order.find((candidate) => views.some((view) => view.stage === candidate))!;
  const pct =
    stage === 'finishing'
      ? 100
      : totalBytes
        ? Math.min(100, Math.floor((bytesDone / totalBytes) * 100))
        : null;
  return {
    ...views[0]!,
    stage,
    pct,
    bytesDone,
    totalBytes,
    rate: stage === 'downloading' ? rate : null,
    etaSeconds:
      stage === 'downloading' && rate && totalBytes && totalBytes > bytesDone
        ? (totalBytes - bytesDone) / rate
        : null,
  };
}

/** A terminal view the hook keeps after the backend forgets the job. */
export function terminalInstallView(
  stage: 'cancelled' | 'ready' | 'failed',
  error?: string | null,
  topic?: string | null,
): InstallView {
  return {
    ...describeInstall({ repo_id: '', state: 'done' }),
    stage,
    pct: stage === 'ready' ? 100 : null,
    reason: stage === 'failed' ? oneLineReason(error) : null,
    detail: stage === 'failed' ? (error ?? null) : null,
    problem: stage === 'failed' ? installProblem(topic) : null,
    docsTopic: stage === 'failed' ? (topic ?? null) : null,
    recovery: stage === 'failed' ? installRecovery(topic) : null,
  };
}

/** Localised remaining time ("3 min", "1.5 hr") for a download ETA. */
export function formatInstallEta(seconds: number | null | undefined, locale?: string): string {
  if (!seconds || seconds <= 0 || !Number.isFinite(seconds)) return '';
  // Pick the unit from the rounded-up value, so 59.4 s reads "1 min" and
  // 59 min 1 s reads "1 hr" instead of "60 sec" / "60 min".
  const wholeSeconds = Math.ceil(seconds);
  const wholeMinutes = Math.ceil(seconds / 60);
  const [value, unit] =
    wholeSeconds < 60
      ? [wholeSeconds, 'second']
      : wholeMinutes < 60
        ? [wholeMinutes, 'minute']
        : [Math.round((seconds / 3600) * 10) / 10, 'hour'];
  try {
    return new Intl.NumberFormat(locale, {
      style: 'unit',
      unit,
      unitDisplay: 'short',
      maximumFractionDigits: 1,
    }).format(value);
  } catch {
    return `${value} ${unit[0]}`;
  }
}
