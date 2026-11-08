/**
 * Periodic app check-in: tells the account server which app and version a
 * signed-in person uses. It grants nothing and runs only while signed in with
 * account features on; errors are never shown and never logged with the token.
 */
export type CheckPlatform = 'macos' | 'windows' | 'linux';

const PLATFORMS: Record<string, CheckPlatform> = {
  darwin: 'macos',
  win32: 'windows',
  linux: 'linux',
};

export const MIN_INTERVAL_S = 60;
export const MAX_INTERVAL_S = 21600;
export const DEFAULT_INTERVAL_S = 300;
export const FIRST_CHECK_DELAY_MS = 5000;

export function clampInterval(seconds: number): number {
  return Math.min(MAX_INTERVAL_S, Math.max(MIN_INTERVAL_S, Math.round(seconds)));
}

export class AppCheck {
  private timer: NodeJS.Timeout | null = null;
  private running = false;
  private stopped = false;
  private epoch = 0;
  private interval = DEFAULT_INTERVAL_S;
  private readonly platform: CheckPlatform | undefined;

  constructor(
    private readonly opts: {
      api: {
        appCheck(body: {
          app: 'oss';
          app_version: string;
          platform: CheckPlatform;
        }): Promise<number | null>;
      };
      version: string;
      platform: string;
      isActive: () => boolean;
    },
  ) {
    this.platform = PLATFORMS[opts.platform];
  }

  /** Call whenever the switches or the session change: starts or stops as needed. */
  reconcile(): void {
    if (this.stopped || !this.platform) return;
    if (!this.opts.isActive()) {
      this.cancel();
      return;
    }
    if (!this.running) {
      this.running = true;
      this.schedule(FIRST_CHECK_DELAY_MS);
    }
  }

  /** Stop for good (quit). */
  stop(): void {
    this.stopped = true;
    this.cancel();
  }

  private cancel(): void {
    this.running = false;
    this.epoch++;
    if (this.timer) clearTimeout(this.timer);
    this.timer = null;
  }

  private schedule(ms: number): void {
    if (this.timer) clearTimeout(this.timer);
    const epoch = this.epoch;
    this.timer = setTimeout(() => void this.tick(epoch), ms);
    this.timer.unref?.();
  }

  private async tick(epoch: number): Promise<void> {
    this.timer = null;
    if (epoch !== this.epoch || !this.opts.isActive()) {
      this.cancel();
      return;
    }
    let wait = this.interval;
    try {
      const next = await this.opts.api.appCheck({
        app: 'oss',
        app_version: this.opts.version,
        platform: this.platform!,
      });
      if (next !== null) {
        this.interval = clampInterval(next);
        wait = this.interval;
      }
    } catch (error) {
      // 404: the server has no route yet. Anything else: try again later.
      if ((error as { status?: number } | null)?.status === 404) wait = MAX_INTERVAL_S;
    }
    if (epoch !== this.epoch || !this.opts.isActive()) {
      if (epoch === this.epoch) this.cancel();
      return;
    }
    this.schedule(wait * 1000);
  }
}
