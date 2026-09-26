import { startMicCapture } from '@shared/utils/aec/micCapture';
import { subscribeFarEnd } from '@shared/utils/aec/farEndBus';
import {
  AEC_FAR,
  AEC_NEAR,
  floatToInt16,
  tagFrame,
} from '@shared/utils/aec/pcm';
import {
  classifySherpaFinal,
  sherpaSummaryTail,
} from '@shared/utils/captureProtocol';
import type { TranscriptEntry } from '@shared/utils/transcriptionsStore';
import { apiJson } from '@/lib/api/client';
import { backendWebSocketUrl } from '@/lib/api/websocket';
import { beginAppActivity } from '@/lib/app-activity';
import { getAecEnabled } from '@/lib/store/dictation-settings';

export interface DictationState {
  stage: 'idle' | 'starting' | 'recording' | 'transcribing' | 'done' | 'error';
  text: string;
  modelStage?: string;
  issue?: 'model' | 'microphone' | 'connection' | 'transcription' | 'storage';
  paused: boolean;
}
interface Frame extends Partial<TranscriptEntry> {
  type: string;
  final_kind?: string;
  stage?: string;
  model_silent?: boolean;
}

/** One microphone/WS session; stale async completions cannot publish into a later one. */
export class LiveDictation {
  private state: DictationState = { stage: 'idle', text: '', paused: false };
  private listeners = new Set<() => void>();
  private generation = 0;
  private request: AbortController | null = null;
  private stream: MediaStream | null = null;
  private socket: WebSocket | null = null;
  private stopGraph: (() => Promise<void>) | null = null;
  private unsubscribeFarEnd: (() => void) | null = null;
  private deadline: ReturnType<typeof setTimeout> | null = null;
  private committed: string[] = [];
  private onResult: (entry: Partial<TranscriptEntry>) => void = () => {};
  private startedAt = 0;
  private finishActivity: (() => void) | null = null;

  getSnapshot = () => this.state;
  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => {
      this.listeners.delete(listener);
    };
  };
  private publish(patch: Partial<DictationState>) {
    this.state = { ...this.state, ...patch };
    for (const listener of this.listeners) listener();
  }

  async start(
    onResult: (entry: Partial<TranscriptEntry>) => void,
    deviceId?: string,
  ): Promise<void> {
    if (['starting', 'recording', 'transcribing'].includes(this.state.stage)) return;
    this.release();
    this.finishActivity = beginAppActivity('dictation');
    const generation = ++this.generation;
    const current = () => generation === this.generation;
    const request = new AbortController();
    this.request = request;
    this.onResult = onResult;
    this.committed = [];
    this.publish({
      stage: 'starting',
      text: '',
      issue: undefined,
      modelStage: undefined,
      paused: false,
    });
    let issue: DictationState['issue'] = 'connection';
    try {
      const [prefs, catalogue] = await Promise.all([
        apiJson<{ enabled: boolean; model_id: string }>('/dictation/prefs', {
          signal: request.signal,
        }),
        apiJson<{
          engine_available: boolean;
          models: { id: string; installed: boolean }[];
        }>('/dictation/models', { signal: request.signal }),
      ]);
      if (!current()) return;
      if (
        !prefs.enabled ||
        !catalogue.engine_available ||
        !catalogue.models.some((model) => model.id === prefs.model_id && model.installed)
      ) {
        this.fail('model');
        return;
      }
      issue = 'microphone';
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          ...(deviceId ? { deviceId: { exact: deviceId } } : {}),
        },
      });
      if (!current()) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      this.stream = stream;
      issue = 'connection';
      const url = new URL(await backendWebSocketUrl('/ws/transcribe'));
      url.searchParams.set('model', prefs.model_id);
      url.searchParams.set('pcm', '1');
      url.searchParams.set('sr', '16000');
      const aecEnabled = getAecEnabled();
      if (aecEnabled) url.searchParams.set('aec', '1');
      const socket = new WebSocket(url);
      this.socket = socket;
      socket.onmessage = (event) => {
        if (!current()) return;
        try {
          this.frame(JSON.parse(String(event.data)) as Frame);
        } catch {
          this.fail('transcription');
        }
      };
      await new Promise<void>((resolve, reject) => {
        const timer = setTimeout(() => reject(new Error('Connection timeout')), 10_000);
        const finish = (error?: Error) => {
          clearTimeout(timer);
          request.signal.removeEventListener('abort', aborted);
          if (error) reject(error);
          else resolve();
        };
        const aborted = () => finish(new Error('Cancelled'));
        request.signal.addEventListener('abort', aborted, { once: true });
        socket.onopen = () => finish();
        socket.onerror = () => finish(new Error('Connection failed'));
        socket.onclose = () => finish(new Error('Connection closed'));
      });
      if (!current()) return;
      socket.onerror = () => {
        if (current()) this.fail('connection');
      };
      socket.onclose = () => {
        if (current() && this.state.stage !== 'done') this.fail('connection');
      };
      issue = 'microphone';
      const stop = await startMicCapture(
        stream,
        (frame) => {
          if (!current() || this.state.paused || this.state.stage !== 'recording') return;
          if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 1024 * 1024) {
            this.fail('connection');
            return;
          }
          const pcm = floatToInt16(frame);
          socket.send(aecEnabled ? tagFrame(pcm, AEC_NEAR) : pcm.buffer);
        },
        { sampleRate: 16000, channels: 1 },
      );
      if (!current()) {
        await stop();
        return;
      }
      this.stopGraph = stop;
      if (aecEnabled) {
        this.unsubscribeFarEnd = subscribeFarEnd((frame) => {
          if (!current() || this.state.stage !== 'recording') return;
          if (socket.readyState !== WebSocket.OPEN || socket.bufferedAmount > 1024 * 1024) return;
          socket.send(tagFrame(floatToInt16(frame), AEC_FAR));
        });
      }
      this.startedAt = Date.now();
      this.publish({ stage: 'recording' });
    } catch {
      if (current()) this.fail(issue);
    }
  }

  pause(): void {
    if (this.state.stage === 'recording') this.publish({ paused: !this.state.paused });
  }
  async stop(): Promise<void> {
    if (this.state.stage !== 'recording') return;
    const generation = this.generation;
    this.publish({ stage: 'transcribing', paused: false });
    const stop = this.stopGraph;
    this.stopGraph = null;
    this.unsubscribeFarEnd?.();
    this.unsubscribeFarEnd = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    await stop?.().catch(() => {});
    if (generation !== this.generation) return;
    if (this.socket?.readyState !== WebSocket.OPEN) {
      this.fail('connection');
      return;
    }
    this.socket.send('EOF');
    this.deadline = setTimeout(
      () => this.fail('transcription'),
      Math.min(120_000, Math.max(15_000, Date.now() - this.startedAt + 10_000)),
    );
  }

  cancel(): void {
    this.generation++;
    this.release();
    this.publish({
      stage: 'idle',
      text: '',
      paused: false,
      modelStage: undefined,
      issue: undefined,
    });
  }
  private frame(message: Frame): void {
    if (message.type === 'status') {
      this.publish({
        modelStage: message.stage === 'ready' ? undefined : message.stage,
      });
      return;
    }
    if (message.type === 'error' || (message.model_silent && !message.text?.trim())) {
      this.fail('transcription');
      return;
    }
    if (message.type === 'partial') {
      this.publish({
        text: [...this.committed, message.text || ''].filter(Boolean).join(' '),
      });
      return;
    }
    if (message.type !== 'final') return;
    // Legacy PCM fallback sends one terminal final without final_kind.
    const kind = message.final_kind === undefined ? 'summary' : classifySherpaFinal(message);
    const text = message.refined_text || message.text || '';
    if (kind === 'utterance') {
      this.committed.push(text);
      this.publish({ text: this.committed.join(' ') });
      try {
        this.onResult(message);
      } catch {
        this.fail('storage');
      }
      return;
    }
    if (kind === 'ignore') return;
    const tail = sherpaSummaryTail(message.text || '', this.committed);
    this.publish({ text: text || this.committed.join(' ') });
    try {
      if (tail)
        this.onResult(
          this.committed.length
            ? { ...message, text: tail, refined_text: undefined, segments: [] }
            : message,
        );
    } catch {
      this.fail('storage');
      return;
    }
    this.generation++;
    this.release();
    this.publish({ stage: 'done', modelStage: undefined, paused: false });
  }
  private fail(issue: DictationState['issue']): void {
    this.generation++;
    this.release();
    this.publish({
      stage: 'error',
      issue,
      modelStage: undefined,
      paused: false,
    });
  }
  private release(): void {
    this.finishActivity?.();
    this.finishActivity = null;
    this.request?.abort();
    this.request = null;
    if (this.deadline) clearTimeout(this.deadline);
    this.deadline = null;
    this.stream?.getTracks().forEach((track) => track.stop());
    this.stream = null;
    const stop = this.stopGraph;
    this.stopGraph = null;
    this.unsubscribeFarEnd?.();
    this.unsubscribeFarEnd = null;
    void stop?.().catch(() => {});
    if (this.socket) {
      this.socket.onmessage = null;
      this.socket.onclose = null;
      this.socket.onerror = null;
      this.socket.close();
      this.socket = null;
    }
  }
}
