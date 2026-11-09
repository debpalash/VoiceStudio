/**
 * Headless desktop ownership for terminal commands.
 *
 * The terminal command never launches the Python backend itself: it starts
 * this app without a window (`--voicestudio-background`; Chromium owns `--headless`), so the same BackendSupervisor,
 * environment and port selection serve both the CLI and the GUI. A later GUI
 * launch reaches the running instance through Electron's single-instance lock
 * and only opens a window in it.
 */
import { renameSync, rmSync, writeFileSync, readFileSync } from 'node:fs';
import { join } from 'node:path';
import type { BackendStage } from '../preload/index.d';

export const HEADLESS_FLAG = '--voicestudio-background';
export const STOP_FLAG = '--voicestudio-stop';
export const CHECK_UPDATES_FLAG = '--voicestudio-check-updates';

export type LaunchIntent = { headless: boolean; stop: boolean; checkUpdates: boolean };

export function parseLaunchIntent(argv: readonly string[]): LaunchIntent {
  return {
    headless: argv.includes(HEADLESS_FLAG),
    stop: argv.includes(STOP_FLAG),
    checkUpdates: argv.includes(CHECK_UPDATES_FLAG),
  };
}

/**
 * Arguments for relaunching after a shutdown raced a new launch. Only intent
 * flags survive: Chromium adds its own switches to a second instance's argv,
 * and a Linux relaunch passes through the public command's argument parser.
 */
export function relaunchArguments(intent: LaunchIntent): string[] {
  if (intent.stop) return [];
  return [
    ...(intent.headless ? [HEADLESS_FLAG] : []),
    ...(intent.checkUpdates ? [CHECK_UPDATES_FLAG] : []),
  ];
}

export type BackendActivity = { inflight: number; busy: string[]; idle_seconds: number };

/** Stages where the backend is (re)starting or installing: never idle. */
const STARTING: ReadonlySet<BackendStage> = new Set(['attaching', 'starting', 'installing']);

export const DEFAULT_IDLE_SHUTDOWN_S = 600;

export function idleShutdownMs(raw = process.env.VOICESTUDIO_IDLE_SHUTDOWN_S): number {
  const seconds = Number(raw);
  return (Number.isFinite(seconds) && seconds > 0 ? seconds : DEFAULT_IDLE_SHUTDOWN_S) * 1000;
}

export type IdleShutdownOptions = {
  idleMs: number;
  pollMs?: number;
  hasWindow: () => boolean;
  stage: () => BackendStage;
  /** `null` when the activity endpoint could not be read. */
  readActivity: () => Promise<BackendActivity | null>;
  onIdle: () => void;
  now?: () => number;
};

/**
 * Stops a terminal-started app once nothing has used it for `idleMs`.
 *
 * Conservative by design: an open window, a starting backend, open requests,
 * running jobs, or a ready backend whose activity cannot be read all keep it
 * alive. Only a backend that is gone or failed is judged by the clock alone.
 */
export class IdleShutdown {
  private readonly now: () => number;
  private lastUse: number;
  private timer: ReturnType<typeof setInterval> | null = null;
  private fired = false;

  constructor(private readonly options: IdleShutdownOptions) {
    this.now = options.now ?? Date.now;
    this.lastUse = this.now();
  }

  start(): void {
    if (this.timer) return;
    this.timer = setInterval(() => void this.tick(), this.options.pollMs ?? 15_000);
    this.timer.unref?.();
  }

  stop(): void {
    if (this.timer) clearInterval(this.timer);
    this.timer = null;
  }

  /** A new terminal request or a window counts as use. */
  touch(): void {
    this.lastUse = this.now();
  }

  async tick(): Promise<boolean> {
    if (this.fired) return true;
    const { hasWindow, stage, readActivity, idleMs } = this.options;
    if (hasWindow() || STARTING.has(stage())) {
      this.touch();
      return false;
    }
    const activity = await readActivity();
    if (hasWindow()) {
      this.touch();
      return false;
    }
    let idleFor = this.now() - this.lastUse;
    if (activity) {
      if (activity.inflight > 0 || activity.busy.length > 0) {
        this.touch();
        return false;
      }
      idleFor = Math.min(idleFor, activity.idle_seconds * 1000);
    } else if (stage() === 'ready' || stage() === 'unresponsive') {
      // A long job can block the event loop; unknown is never idle.
      this.touch();
      return false;
    }
    if (idleFor < idleMs) return false;
    this.fired = true;
    this.stop();
    this.options.onIdle();
    return true;
  }
}

export const DISCOVERY_FILE = 'backend.json';

export type DiscoveryRecord = {
  schema: 1;
  /** Lets a waiting terminal command fail fast on setup or startup errors. */
  stage: BackendStage;
  url: string;
  pid: number;
  version: string;
  /** Started for a terminal command; closing its window returns to headless. */
  headless: boolean;
  /** A window is open, so `voicestudio stop` must leave the app to its user. */
  window: boolean;
  /** The app uses a remote backend; the CLI must not send it unauthenticated requests. */
  remote: boolean;
};

/** Publish where this app's local backend listens; the CLI reads it. */
export function writeDiscovery(directory: string, record: DiscoveryRecord): void {
  const target = join(directory, DISCOVERY_FILE);
  const temporary = `${target}.${record.pid}.tmp`;
  writeFileSync(temporary, JSON.stringify(record) + '\n', { mode: 0o600 });
  renameSync(temporary, target);
}

/** Remove the record only while it is still this process's. */
export function clearDiscovery(directory: string, pid: number): void {
  const target = join(directory, DISCOVERY_FILE);
  try {
    const current = JSON.parse(readFileSync(target, 'utf8')) as Partial<DiscoveryRecord>;
    if (current.pid !== pid) return;
  } catch {
    return;
  }
  rmSync(target, { force: true });
}
