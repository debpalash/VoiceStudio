import { randomUUID } from 'node:crypto';
import { constants } from 'node:fs';
import { mkdir, open, opendir, rm } from 'node:fs/promises';
import { dirname, join } from 'node:path';
import destinationConfig from '../../../config/analytics.json';
import type { DiagnosticState } from '../preload/diagnostics';
import { FILE_RENAME, renameWithRetry } from './rename-retry';
import {
  diagnosticLimit,
  MAX_QUEUE_BYTES,
  MAX_RECORDS,
  sanitizeProperties,
  sanitizeRecord,
  validConsent,
  projectException,
  TTL_MS,
  type DiagnosticConsent,
  type DiagnosticEvent,
  type DiagnosticIdentity,
  type DiagnosticRecord,
} from './diagnostics-schema';

interface Destination {
  site: string;
  host: string;
  appUrl: string;
}
export interface DiagnosticsOptions {
  directory: string;
  identity: DiagnosticIdentity;
  env?: NodeJS.ProcessEnv;
  destination?: Destination | null;
  send?: typeof fetch;
  now?: () => number;
  /** Disable the periodic worker in deterministic tests; flush() runs the same worker. */
  automatic?: boolean;
  retryMs?: number;
}

const id = () => randomUUID().replaceAll('-', '');
const killed = (env: NodeJS.ProcessEnv) =>
  [env.OMNIVOICE_DIAGNOSTICS_DISABLED, env.OMNIVOICE_ANALYTICS_DISABLED].some((v) =>
    /^(1|true|yes|on)$/i.test((v ?? '').trim()),
  );

function safeUrl(value: string, origin = false): boolean {
  try {
    const url = new URL(value);
    return (
      ['http:', 'https:'].includes(url.protocol) &&
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

export function diagnosticDestination(env: NodeJS.ProcessEnv): Destination | null {
  const config = destinationConfig as Record<string, unknown>;
  const site = (env.SUPERMETRY_SITE_ID ?? String(config.site ?? '')).trim();
  const host = (env.SUPERMETRY_HOST ?? String(config.host ?? '')).trim().replace(/\/$/, '');
  const appUrl = (env.SUPERMETRY_APP_URL ?? String(config.appUrl ?? '')).trim().replace(/\/$/, '');
  return site &&
    Buffer.byteLength(site) <= 300 &&
    !/[\x00-\x20]/.test(site) &&
    safeUrl(host, true) &&
    safeUrl(appUrl)
    ? { site, host, appUrl }
    : null;
}

/** Inspect and read the same bounded file, including a concurrent writer growing it. */
async function readJson(path: string, limit: number): Promise<unknown> {
  const file = await open(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const info = await file.stat();
    if (!info.isFile() || info.size > limit) throw new Error('Invalid diagnostics file');
    const buffer = Buffer.alloc(limit + 1);
    let length = 0;
    while (length < buffer.length) {
      const { bytesRead } = await file.read(buffer, length, buffer.length - length, length);
      if (!bytesRead) break;
      length += bytesRead;
    }
    if (length > limit) throw new Error('Invalid diagnostics file');
    return JSON.parse(buffer.toString('utf8', 0, length));
  } finally {
    await file.close();
  }
}

/** Private state replaces a symlink itself; export-oriented replaceFile follows it. */
async function writePrivate(path: string, text: string): Promise<void> {
  await mkdir(dirname(path), { recursive: true, mode: 0o700 });
  const temporary = join(dirname(path), `.diagnostic-${id()}.tmp`);
  const file = await open(temporary, 'wx', 0o600);
  try {
    try {
      await file.writeFile(text);
      await file.sync();
    } finally {
      await file.close();
    }
    await renameWithRetry(temporary, path, FILE_RENAME);
  } finally {
    await rm(temporary, { force: true }).catch(() => {});
  }
}

function diagnosticUserAgent(platform: string): string {
  const device =
    platform === 'macos'
      ? 'Macintosh; Intel Mac OS X 10_15_7'
      : platform === 'windows'
        ? 'Windows NT 10.0; Win64; x64'
        : 'X11; Linux x86_64';
  return `Mozilla/5.0 (${device}) AppleWebKit/537.36 (KHTML, like Gecko) VoiceStudio/1.0 Chrome/130.0.0.0 Safari/537.36`;
}

/** One desktop consent authority and bounded durable outbox, independent of Python. */
export class DiagnosticReports {
  private consent: DiagnosticConsent | null = null;
  private records: DiagnosticRecord[] = [];
  private dropped = 0;
  private revision = 0;
  private stopped = false;
  private initialized = false;
  private dirty = false;
  private scheduled = false;
  private killPurged = false;
  private killObserved = false;
  private work: Promise<void> | null = null;
  private control: Promise<unknown> = Promise.resolve();
  private active: AbortController | null = null;
  private activeId: string | null = null;
  private timer?: NodeJS.Timeout;
  private retryAt = 0;
  private wakeRetry: (() => void) | null = null;
  private readonly destination: Destination | null;
  private readonly env: NodeJS.ProcessEnv;
  private readonly now: () => number;
  private readonly send: typeof fetch;
  private readonly outbox: string;
  private readonly inbox: string;
  private readonly consentPath: string;
  private readonly sessionPath: string;

  constructor(private readonly options: DiagnosticsOptions) {
    this.env = options.env ?? process.env;
    this.destination =
      options.destination === undefined ? diagnosticDestination(this.env) : options.destination;
    this.now = options.now ?? Date.now;
    this.send = options.send ?? fetch;
    this.outbox = join(options.directory, 'outbox.json');
    this.inbox = join(options.directory, 'inbox');
    this.consentPath = join(options.directory, 'consent.json');
    this.sessionPath = join(options.directory, 'session.json');
  }

  get directory(): string {
    return this.options.directory;
  }
  get enabled(): boolean {
    // Pure memory barrier: even a brief kill observed on a fatal/capture path
    // cannot restore the old grant before the background worker revokes it.
    if (killed(this.env)) this.killObserved = true;
    return (
      !this.stopped &&
      this.initialized &&
      Boolean(this.consent?.enabled) &&
      Boolean(this.destination) &&
      !this.killObserved
    );
  }
  status(): DiagnosticState {
    return {
      available: Boolean(this.destination) && !killed(this.env),
      enabled: this.enabled,
      opted_in: this.consent?.enabled === true && !this.killObserved,
      prompted: this.consent !== null,
      pending_count: this.records.length,
      dropped_count: this.dropped,
    };
  }

  async initialize(): Promise<void> {
    try {
      const consent = await readJson(this.consentPath, 4096);
      if (validConsent(consent)) this.consent = consent;
    } catch {
      /* Missing or unreadable consent never grants collection. */
    }
    this.initialized = true;
    if (killed(this.env)) this.killObserved = true;
    if (this.killObserved) {
      this.requestKillRevocation();
      await this.control.catch(() => {});
    } else if (this.enabled) {
      try {
        const stored = await readJson(this.outbox, MAX_QUEUE_BYTES);
        if (stored && typeof stored === 'object' && !Array.isArray(stored)) {
          const data = stored as { records?: unknown; dropped?: unknown };
          if (Array.isArray(data.records)) {
            for (const value of data.records.slice(-MAX_RECORDS)) {
              const record = sanitizeRecord(value, this.consent!.epoch, this.now());
              if (record) this.add(record, false);
            }
          }
          if (Number.isSafeInteger(data.dropped))
            this.dropped = Math.max(0, Math.min(1_000_000, data.dropped as number));
        }
      } catch {
        /* A corrupt queue cannot become an upload. */
      }
      await this.recoverSession();
      await this.writeSession().catch(() => {});
    } else {
      await this.purge();
    }
    if (this.options.automatic !== false) {
      this.timer = setInterval(() => this.schedule(), 1000);
      this.timer.unref();
      this.schedule();
    }
  }

  /** No disk, configuration reads, networking or waiting on fatal/error paths. */
  capture(event: DiagnosticEvent): void {
    try {
      if (!this.enabled || !this.consent) return;
      const props = sanitizeProperties({
        ...event,
        ...this.options.identity,
        source: event.source ?? 'electron_main',
      });
      if (!props) return;
      this.add({
        version: 1,
        id: id(),
        timestamp_ms: this.now(),
        epoch: this.consent.epoch,
        props,
      });
      this.schedule();
    } catch {
      /* Failure reporting must never alter the operation. */
    }
  }

  async setConsent(enabled: boolean): Promise<DiagnosticState> {
    if (typeof enabled !== 'boolean') throw new Error('Invalid diagnostic consent');
    if (enabled && killed(this.env)) {
      this.killObserved = true;
      this.requestKillRevocation();
      await this.control.catch(() => {});
      return this.status();
    }
    if (enabled && this.enabled) return this.status();
    const freshGrant = enabled && this.killObserved;
    if (enabled) {
      this.killObserved = false;
      this.killPurged = false;
    }
    // Revoke immediately, before any pending write, connection or backoff can progress.
    const revision = ++this.revision;
    this.abort();
    this.consent =
      this.consent && this.consent.enabled === enabled && !freshGrant
        ? this.consent
        : {
            version: 1,
            enabled,
            epoch: id(),
            profile_id: `install_${id()}`,
          };
    const choice = this.consent;
    this.records = [];
    this.dirty = false;
    this.retryAt = 0;
    this.initialized = false;
    this.control = this.control
      .catch(() => {})
      .then(async () => {
        await mkdir(this.options.directory, { recursive: true });
        try {
          await writePrivate(this.consentPath, JSON.stringify(choice));
        } catch {
          // Removing an old grant is a fail-closed fallback when replacement fails.
          if (!enabled) await rm(this.consentPath, { force: true }).catch(() => {});
          throw new Error('Could not save diagnostic consent');
        }
        await this.work?.catch(() => {});
        await this.purge();
        if (revision !== this.revision) return;
        this.initialized = true;
        if (this.enabled) await this.writeSession();
      });
    await this.control;
    if (revision === this.revision && this.enabled) this.schedule();
    return this.status();
  }

  /** Waits only when explicitly requested by startup/tests/orderly shutdown. */
  async flush(): Promise<void> {
    if (!this.work)
      this.work = this.run()
        .catch(() => {})
        .finally(() => {
          this.work = null;
          if (this.killObserved) this.requestKillRevocation();
        });
    await this.work;
    // The worker may request revocation, whose control operation joins the
    // worker only after it returns. Joining here avoids a self-wait cycle.
    await this.control.catch(() => {});
  }

  async close(): Promise<void> {
    if (killed(this.env)) this.killObserved = true;
    if (this.killObserved) {
      this.requestKillRevocation();
      await this.control.catch(() => {});
    }
    this.stopped = true;
    this.revision++;
    if (this.timer) clearInterval(this.timer);
    this.abort();
    await this.work?.catch(() => {});
    if (this.killObserved) await this.purge();
    else await this.persist();
    await rm(this.sessionPath, { force: true }).catch(() => {});
  }

  private abort(): void {
    this.active?.abort();
    this.wakeRetry?.();
    this.wakeRetry = null;
  }
  private requestKillRevocation(): void {
    if (this.killPurged) return;
    this.killPurged = true;
    this.abort();
    this.records = [];
    this.dirty = false;
    if (this.consent?.enabled) {
      // Reuse the private OFF write and old-grant removal fallback. Do not
      // await from run(): setConsent itself waits for the current worker.
      void this.setConsent(false).catch(() => {
        this.killPurged = false;
      });
    } else {
      // A killed launch without a prior choice must create no profile/grant.
      this.control = this.control.catch(() => {}).then(() => this.purge());
    }
  }
  private schedule(): void {
    if (this.scheduled || this.stopped || this.options.automatic === false) return;
    if (killed(this.env)) this.abort();
    this.scheduled = true;
    queueMicrotask(() => {
      this.scheduled = false;
      void this.flush();
    });
  }
  private add(record: DiagnosticRecord, coalesce = true): void {
    if (this.records.some((old) => old.id === record.id)) return;
    const duplicate =
      coalesce &&
      this.records.find(
        (old) =>
          old.id !== this.activeId &&
          old.props.fingerprint === record.props.fingerprint &&
          Math.abs(old.timestamp_ms - record.timestamp_ms) <= diagnosticLimit('dedupe_ms', 60_000),
      );
    if (duplicate) {
      duplicate.props.occurrences = Math.min(
        1_000_000,
        Number(duplicate.props.occurrences) + Number(record.props.occurrences),
      );
    } else this.records.push(record);
    while (
      this.records.length > MAX_RECORDS ||
      Buffer.byteLength(JSON.stringify({ records: this.records, dropped: this.dropped })) >
        MAX_QUEUE_BYTES
    ) {
      this.records.shift();
      this.dropped = Math.min(1_000_000, this.dropped + 1);
    }
    this.dirty = true;
  }
  private async persist(): Promise<void> {
    const epoch = this.consent?.epoch;
    if (!epoch || !this.consent?.enabled || killed(this.env)) return;
    const revision = this.revision;
    this.records = this.records
      .map((r) => sanitizeRecord(r, epoch, this.now()))
      .filter((r): r is DiagnosticRecord => r !== null);
    const text = JSON.stringify({ version: 1, dropped: this.dropped, records: this.records });
    this.dirty = false;
    await mkdir(this.options.directory, { recursive: true });
    await writePrivate(this.outbox, text);
    if (revision !== this.revision) await rm(this.outbox, { force: true }).catch(() => {});
  }
  private async purge(): Promise<void> {
    await Promise.all(
      [this.outbox, this.inbox, this.sessionPath].map((path) =>
        rm(path, { recursive: path === this.inbox, force: true }).catch(() => {}),
      ),
    );
  }
  private async tryPersist(): Promise<boolean> {
    try {
      await this.persist();
      return true;
    } catch {
      this.dirty = true;
      return false;
    }
  }
  private async granted(epoch: string, revision: number): Promise<boolean> {
    if (!this.enabled || revision !== this.revision || this.consent?.epoch !== epoch) return false;
    let current: unknown;
    try {
      current = await readJson(this.consentPath, 4096);
    } catch {
      /* Missing/unreadable permission revokes the cached grant. */
    }
    if (revision !== this.revision) return false;
    if (
      this.enabled &&
      validConsent(current) &&
      current.enabled &&
      current.epoch === epoch &&
      current.profile_id === this.consent?.profile_id
    )
      return true;
    this.revision++;
    this.abort();
    this.records = [];
    this.dirty = false;
    this.consent = validConsent(current) && !current.enabled ? current : null;
    await this.purge();
    return false;
  }
  private async importInbox(): Promise<void> {
    if (!this.enabled || !this.consent) return;
    const epoch = this.consent.epoch;
    const directory = await opendir(this.inbox).catch(() => null);
    if (!directory) return;
    let count = 0;
    for await (const entry of directory) {
      if (++count > MAX_RECORDS * 4) break;
      if (!entry.isFile() || !/^[a-f0-9]{32}\.json$/.test(entry.name)) continue;
      const path = join(this.inbox, entry.name);
      try {
        const parsed = await readJson(path, 8192);
        const record = sanitizeRecord(parsed, epoch, this.now());
        if (record && this.enabled && this.consent?.epoch === epoch) {
          // Keep stable producer IDs. A crash between outbox commit and inbox
          // unlink must not count the same record again on the next launch.
          this.add(record, false);
          if (this.records.some((r) => r.id === record.id) && !(await this.tryPersist())) continue;
        }
      } catch {
        /* Invalid inbox records are discarded, never forwarded. */
      }
      await rm(path, { force: true }).catch(() => {});
    }
  }
  private async run(): Promise<void> {
    if (killed(this.env)) this.killObserved = true;
    if (this.killObserved) {
      this.requestKillRevocation();
      return;
    }
    if (!this.enabled || !this.consent || !this.destination) return;
    const epoch = this.consent.epoch;
    const revision = this.revision;
    if (!(await this.granted(epoch, revision))) return;
    await this.importInbox();
    if (this.dirty) await this.tryPersist();
    if (this.now() < this.retryAt) return;
    while (this.records.length && (await this.granted(epoch, revision))) {
      const record = sanitizeRecord(this.records[0], epoch, this.now());
      if (!record) {
        this.records.shift();
        this.dirty = true;
        continue;
      }
      const body = JSON.stringify({
        v: 1,
        id: record.id,
        ts: record.timestamp_ms,
        site: this.destination.site,
        name: 'diagnostic_report',
        url: `${this.destination.appUrl}/diagnostics`,
        profile_id: this.consent!.profile_id,
        props: record.props,
      });
      if (Buffer.byteLength(body) > diagnosticLimit('body_bytes', 8192)) {
        this.records.shift();
        this.dropped++;
        this.dirty = true;
        continue;
      }
      this.activeId = record.id;
      let terminal = false;
      for (let attempt = 0; attempt < diagnosticLimit('max_attempts', 3); attempt++) {
        if (!(await this.granted(epoch, revision))) break;
        const controller = new AbortController();
        this.active = controller;
        const timer = setTimeout(
          () => controller.abort(),
          diagnosticLimit('request_timeout_ms', 2000),
        );
        timer.unref();
        try {
          const response = await this.send(`${this.destination.host}/api/public/v1/events`, {
            method: 'POST',
            body,
            redirect: 'manual',
            credentials: 'omit',
            referrerPolicy: 'no-referrer',
            headers: {
              'Content-Type': 'application/json',
              Origin: new URL(this.destination.appUrl).origin,
              'User-Agent': diagnosticUserAgent(this.options.identity.platform),
            },
            signal: controller.signal,
          });
          terminal = response.status !== 429 && response.status < 500;
          await response.body?.cancel().catch(() => {});
        } catch {
          /* Offline reports remain durable; a feature never waits on this. */
        } finally {
          clearTimeout(timer);
          this.active = null;
        }
        if (terminal || revision !== this.revision || !this.enabled) break;
        if (attempt + 1 < diagnosticLimit('max_attempts', 3))
          await this.backoff(500 * 2 ** attempt);
      }
      this.activeId = null;
      if (revision !== this.revision || !this.enabled) return;
      if (!terminal) {
        this.retryAt = this.now() + (this.options.retryMs ?? 60_000);
        break;
      }
      this.records = this.records.filter((r) => r.id !== record.id);
      await rm(join(this.inbox, `${record.id}.json`), { force: true }).catch(() => {});
      this.dirty = true;
      await this.tryPersist();
    }
    if (this.dirty && revision === this.revision) await this.tryPersist();
  }
  private backoff(ms: number): Promise<void> {
    return new Promise((resolve) => {
      const timer = setTimeout(finish, ms);
      timer.unref();
      const self = this;
      function finish() {
        clearTimeout(timer);
        self.wakeRetry = null;
        resolve();
      }
      this.wakeRetry = finish;
    });
  }
  private async recoverSession(): Promise<void> {
    try {
      const marker = (await readJson(this.sessionPath, 4096)) as Record<string, unknown>;
      if (
        marker.version !== 1 ||
        marker.epoch !== this.consent?.epoch ||
        marker.active !== true ||
        !Number.isSafeInteger(marker.timestamp_ms) ||
        (marker.timestamp_ms as number) < this.now() - TTL_MS ||
        (marker.timestamp_ms as number) > this.now()
      )
        return;
      // The marker proves collection was enabled for THIS session. Earlier
      // manual journals are never replayed merely because somebody opts in.
      let projected: DiagnosticEvent = {
        category: 'app_crash',
        stage: 'startup',
        reason: 'unclean_exit',
      };
      const previousVersion =
        typeof marker.app_version === 'string'
          ? marker.app_version
          : this.options.identity.app_version;
      if (
        this.records.some(
          (record) =>
            record.timestamp_ms >= (marker.timestamp_ms as number) &&
            record.props.category === 'app_crash' &&
            record.props.source === 'electron_main' &&
            record.props.reason === 'unhandled_exception' &&
            record.props.app_version === previousVersion,
        )
      )
        return;
      try {
        const local = await readJson(
          join(dirname(this.options.directory), 'main-process-errors.json'),
          2 * 1024 * 1024,
        );
        if (Array.isArray(local)) {
          const recent = local
            .slice(-20)
            .reverse()
            .find((entry: unknown) => {
              if (!entry || typeof entry !== 'object') return false;
              const r = entry as Record<string, unknown>;
              const at = typeof r.at === 'string' ? Date.parse(r.at) : NaN;
              return (
                r.version === previousVersion &&
                typeof r.detail === 'string' &&
                at >= (marker.timestamp_ms as number) &&
                at <= this.now() &&
                r.origin === 'uncaughtException'
              );
            }) as Record<string, unknown> | undefined;
          if (recent) {
            const error = new Error();
            error.name = /^([A-Za-z]+Error):/.exec(String(recent.detail))?.[1] ?? 'OtherError';
            error.stack = String(recent.detail).slice(0, 32_768);
            projected = {
              ...projected,
              ...projectException(error),
              reason: 'unhandled_exception',
            };
          }
        }
      } catch {
        /* Raw journals remain local; an unreadable one adds no detail. */
      }
      const previousIdentity = Object.fromEntries(
        Object.entries(this.options.identity).map(([key, value]) => [key, marker[key] ?? value]),
      );
      const props = sanitizeProperties({
        ...projected,
        ...previousIdentity,
        source: 'electron_main',
      });
      if (props && this.enabled && this.consent)
        this.add({
          version: 1,
          id: id(),
          timestamp_ms: this.now(),
          epoch: this.consent.epoch,
          props,
        });
    } catch {
      /* An absent/invalid marker is not evidence of a crash. */
    }
  }
  private async writeSession(): Promise<void> {
    if (!this.enabled || !this.consent) return;
    const revision = this.revision;
    await mkdir(this.options.directory, { recursive: true });
    await writePrivate(
      this.sessionPath,
      JSON.stringify({
        version: 1,
        ...this.options.identity,
        epoch: this.consent.epoch,
        active: true,
        timestamp_ms: this.now(),
      }),
    );
    if (revision !== this.revision) await rm(this.sessionPath, { force: true }).catch(() => {});
  }
}
