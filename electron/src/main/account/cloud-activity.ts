/**
 * The only door to VoiceStudio Cloud. Every request and timer goes through here,
 * so turning a switch off can stop all of them at once (owner rule 2026-10-16).
 */
export type FetchImpl = (url: string, init?: RequestInit) => Promise<Response>;

export class CloudDisabledError extends Error {
  constructor() {
    super('VoiceStudio Cloud is switched off');
    this.name = 'CloudDisabledError';
  }
}

export class CloudActivity {
  private readonly controllers = new Set<AbortController>();
  private readonly timers = new Set<NodeJS.Timeout>();
  private readonly stopHooks = new Set<() => void>();
  private finalAvailable = false;
  private readonly fetchImpl: FetchImpl;

  constructor(
    private readonly opts: {
      isAllowed: () => boolean;
      origins: Set<string>;
      fetchImpl?: FetchImpl;
    },
  ) {
    this.fetchImpl = opts.fetchImpl ?? fetch;
  }

  async fetch(
    url: string,
    init: RequestInit = {},
    /** `serverIssued`: a link the account API handed out (the profile photo), HTTPS only. */
    opts: { final?: boolean; timeoutMs?: number; serverIssued?: boolean } = {},
  ): Promise<Response> {
    if (!this.opts.isAllowed()) {
      if (!(opts.final && this.finalAvailable)) throw new CloudDisabledError();
      this.finalAvailable = false;
    }
    const target = new URL(url);
    if (opts.serverIssued) {
      if (target.protocol !== 'https:')
        throw new Error('Refusing a server-issued link that is not https');
    } else if (!this.opts.origins.has(target.origin))
      throw new Error('Refusing a request outside the VoiceStudio Cloud origin');
    const controller = new AbortController();
    this.controllers.add(controller);
    const timeout = setTimeout(
      () => controller.abort(new Error('timeout')),
      opts.timeoutMs ?? 30_000,
    );
    const release = () => {
      clearTimeout(timeout);
      this.controllers.delete(controller);
    };
    let res: Response;
    try {
      res = await this.fetchImpl(url, { ...init, redirect: 'error', signal: controller.signal });
    } catch (error) {
      release();
      throw error;
    }
    if (!res.body || [101, 204, 205, 304].includes(res.status)) {
      release();
      return res;
    }
    // The request stays tracked (and timed) until its body is read: switching off or
    // the timeout also stops a response that is still downloading.
    const reader = res.body.getReader();
    const body = new ReadableStream<Uint8Array>({
      start(stream) {
        controller.signal.addEventListener(
          'abort',
          () => {
            stream.error(controller.signal.reason);
            reader.cancel(controller.signal.reason).catch(() => {});
            release();
          },
          { once: true },
        );
      },
      async pull(stream) {
        try {
          const { done, value } = await reader.read();
          if (done) {
            stream.close();
            release();
          } else stream.enqueue(value);
        } catch (error) {
          stream.error(error);
          release();
        }
      },
      cancel(reason) {
        release();
        return reader.cancel(reason);
      },
    });
    return new Response(body, {
      status: res.status,
      statusText: res.statusText,
      headers: res.headers,
    });
  }

  setTimeout(fn: () => void, ms: number): void {
    if (!this.opts.isAllowed()) return;
    const timer = setTimeout(() => {
      this.timers.delete(timer);
      if (this.opts.isAllowed()) fn();
    }, ms);
    this.timers.add(timer);
  }

  onStop(fn: () => void): () => void {
    this.stopHooks.add(fn);
    return () => this.stopHooks.delete(fn);
  }

  /** Abort everything now. One `final` request (the revocation) stays possible. */
  stopAll(): void {
    for (const c of this.controllers) c.abort(new CloudDisabledError());
    this.controllers.clear();
    for (const t of this.timers) clearTimeout(t);
    this.timers.clear();
    for (const hook of this.stopHooks) hook();
    this.finalAvailable = true;
  }
}
