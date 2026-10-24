import type { OutputCommand, ShortcutEvent } from './dictation-output';

export interface CaptureEvent {
  session: number;
  action: 'start' | 'stop' | 'cancel';
}
interface Output {
  request(command: OutputCommand): Promise<unknown>;
}
export type CapturePhase = 'starting' | 'recording' | 'transcribing' | 'done' | 'error';
interface Session {
  id: number;
  accepted: boolean;
  phase: CapturePhase;
  stopped: boolean;
  shortcutMode?: 'hold' | 'toggle';
  nextResult: number;
  text: string;
  pending: Promise<unknown>;
}

/** Main owns session identity; a recorder cannot choose another output target. */
export class CaptureSession {
  private current: Session | null = null;
  private generation = 0;
  private starting: Promise<void> | null = null;
  private stopPending = false;
  private replacement: { id: number; released: boolean; mode?: 'hold' | 'toggle' } | null = null;
  private listener: ((event: CaptureEvent) => void) | null = null;
  private queue: CaptureEvent[] = [];
  private acceptanceTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(
    private output: Output,
    private show: () => void,
    private hide: () => void,
  ) {}

  /** Register only the dedicated recorder; replacement must first cancel its old session. */
  listen(listener: (event: CaptureEvent) => void): () => void {
    if (this.listener) throw new Error('Recorder already registered');
    this.listener = listener;
    const queued = this.queue;
    this.queue = [];
    for (const event of queued) listener(event);
    return () => {
      if (this.listener !== listener) return;
      this.listener = null;
      void this.cancel().catch(() => {});
    };
  }

  start(origin: 'shortcut' | 'tray'): Promise<void> {
    if (this.starting) return this.starting;
    if (this.replacement || (this.current && !this.restartable())) return Promise.resolve();
    const generation = ++this.generation;
    this.stopPending = false;
    const operation = this.begin(origin, generation);
    this.starting = operation;
    void operation
      .finally(() => {
        if (this.starting === operation) this.starting = null;
      })
      .catch(() => {});
    return operation;
  }

  private async begin(origin: 'shortcut' | 'tray', generation: number): Promise<void> {
    const id = await this.output.request({ method: 'begin', origin });
    if (!Number.isSafeInteger(id) || Number(id) <= 0)
      throw new Error('Invalid native capture session');
    const session = Number(id);
    if (generation !== this.generation) {
      await this.output.request({ method: 'reject', session });
      return;
    }
    if (this.current) await this.replace(session);
    else await this.adopt(session);
  }

  /** Uses the native key-down destination without querying foreground focus again. */
  async shortcut(event: ShortcutEvent, mode: 'hold' | 'toggle'): Promise<void> {
    if (this.replacement) {
      if (
        event.session === this.replacement.id &&
        !event.pressed &&
        (this.replacement.mode ?? mode) === 'hold'
      )
        this.replacement.released = true;
      else if (event.pressed && event.session !== this.replacement.id)
        await this.output.request({ method: 'reject', session: event.session });
      return;
    }
    if (!event.pressed) {
      if ((this.current?.shortcutMode ?? mode) === 'hold' && this.current?.id === event.session)
        this.stop();
      return;
    }
    if (this.current && this.restartable() && this.current.id !== event.session) {
      await this.replace(event.session, mode);
      return;
    }
    if (this.current || this.starting) {
      if (mode === 'toggle') this.stop();
      if (this.current?.id !== event.session)
        await this.output.request({ method: 'reject', session: event.session });
      return;
    }
    this.generation++;
    await this.adopt(event.session, mode);
  }

  phase(id: number, phase: CapturePhase): void {
    if (this.current?.id === id) this.current.phase = phase;
  }
  canStart(): boolean {
    return !this.starting && !this.replacement && (!this.current || this.restartable());
  }
  private restartable(): boolean {
    return this.current?.phase === 'done' || this.current?.phase === 'error';
  }
  private async replace(id: number, mode?: 'hold' | 'toggle'): Promise<void> {
    const retired = this.cancel();
    const pending = { id, released: false, mode };
    this.replacement = pending;
    await retired;
    if (this.replacement !== pending) {
      await this.output.request({ method: 'reject', session: id });
      return;
    }
    this.replacement = null;
    await this.adopt(id, mode);
    if (pending.released) this.stop();
  }

  private async adopt(session: number, shortcutMode?: 'hold' | 'toggle'): Promise<void> {
    this.current = {
      id: session,
      shortcutMode,
      accepted: false,
      phase: 'starting',
      stopped: false,
      nextResult: 0,
      text: '',
      pending: Promise.resolve(),
    };
    try {
      // begin captures focus before this can reveal or focus the recorder.
      this.show();
      this.acceptanceTimer = setTimeout(() => {
        void this.cancel().catch(() => {});
      }, 15_000);
      this.emit({ session, action: 'start' });
      if (this.stopPending) this.stop();
    } catch (error) {
      await this.cancel();
      throw error;
    }
  }

  stop(): void {
    if (!this.current) {
      if (this.starting) this.stopPending = true;
      return;
    }
    if (this.current.stopped) return;
    this.current.stopped = true;
    this.current.phase = 'transcribing';
    this.emit({ session: this.current.id, action: 'stop' });
  }

  async accept(id: number): Promise<void> {
    const session = this.require(id);
    if (session.accepted) return;
    // Serialize acceptance with delivery/finish. The helper itself also validates IDs.
    await this.enqueue(session, async () => {
      this.require(id);
      if (session.accepted) return;
      await this.output.request({ method: 'activate', session: id });
      this.require(id);
      session.accepted = true;
      this.clearTimer();
    });
  }

  deliver(id: number, sequence: number, text: string): Promise<'inserted' | 'copied'> {
    const session = this.require(id);
    if (!session.accepted) return Promise.reject(new Error('Recorder has not accepted capture'));
    if (!Number.isSafeInteger(sequence) || sequence !== session.nextResult)
      return Promise.reject(new Error('Duplicate or out-of-order transcript'));
    if (typeof text !== 'string' || Buffer.byteLength(text, 'utf8') > 256 * 1024)
      return Promise.reject(new Error('Invalid transcript'));
    // Consume before delivery: a lost acknowledgement must NEVER replay keystrokes.
    session.nextResult++;
    return this.enqueue(session, async () => {
      this.require(id);
      session.text += text;
      const outcome = await this.output.request({ method: 'deliver', session: id, text });
      if (outcome === 'copied')
        await this.output.request({ method: 'copy', session: id, text: session.text });
      if (outcome !== 'inserted' && outcome !== 'copied') throw new Error('Invalid output outcome');
      return outcome;
    });
  }

  async finish(id: number): Promise<void> {
    const session = this.require(id);
    // Queue finish after already accepted utterances, never ahead of them.
    await this.enqueue(
      session,
      async () => {
        this.require(id);
        try {
          await this.output.request({ method: session.accepted ? 'finish' : 'reject', session: id });
        } finally {
          if (this.current === session) this.reset();
        }
      },
      true,
    );
  }

  async cancelFor(id: number | null): Promise<void> {
    if (
      id === null ? this.current?.accepted : this.current?.id !== id && this.replacement?.id !== id
    )
      return;
    await this.cancel();
  }

  async cancel(): Promise<void> {
    const session = this.current;
    const replacement = this.replacement;
    this.replacement = null;
    this.generation++;
    this.starting = null;
    this.reset();
    if (replacement)
      await this.output.request({ method: 'reject', session: replacement.id }).catch(() => {});
    if (!session) return;
    this.emit({ session: session.id, action: 'cancel' });
    // Already-running output cannot be replayed or revoked. Pending output checks
    // identity again and is dropped; finish waits for the running operation.
    await session.pending.catch(() => {});
    await this.output.request({ method: 'finish', session: session.id }).catch(() => {});
    await this.output.request({ method: 'reject', session: session.id }).catch(() => {});
  }

  private enqueue<T>(
    session: Session,
    operation: () => Promise<T>,
    cleanup = false,
  ): Promise<T> {
    // Delivery errors remain sticky: only cleanup may cross a failed acknowledgement.
    const previous = cleanup ? session.pending.catch(() => {}) : session.pending;
    const pending = previous.then(operation);
    session.pending = pending;
    return pending;
  }
  private require(id: number): Session {
    if (!this.current || this.current.id !== id) throw new Error('Stale capture session');
    return this.current;
  }
  private emit(event: CaptureEvent): void {
    if (this.listener) this.listener(event);
    else if (event.action !== 'cancel') this.queue.push(event);
  }
  private clearTimer(): void {
    if (this.acceptanceTimer) clearTimeout(this.acceptanceTimer);
    this.acceptanceTimer = null;
  }
  private reset(): void {
    this.current = null;
    this.queue = [];
    this.stopPending = false;
    this.clearTimer();
    this.hide();
  }
}
