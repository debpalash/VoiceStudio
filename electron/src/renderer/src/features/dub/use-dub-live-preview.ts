import { createStreamingPreview, supportsStreamingPreview } from '@/lib/audio/streaming-preview';
import { backendWebSocketUrl } from '@/lib/api/websocket';
import { beginAppActivity } from '@/lib/app-activity';
import { acquireSynthesis } from '@/lib/synthesis-lock';
import { segmentGenInputs } from '@shared/utils/segments';
import { useCallback, useEffect, useRef, useState } from 'react';
import { useTranslation } from 'react-i18next';
import { toast } from 'sonner';
import type { DubSegment } from './dub-session';

export const LIVE_DUB_PREVIEW_DELAY_MS = 400;
const TOAST_THROTTLE_MS = 4_000;

interface LiveSession {
  id: string;
  socket: WebSocket | null;
  player: ReturnType<typeof createStreamingPreview> | null;
  releaseSynthesis: (() => void) | null;
  finishActivity: (() => void) | null;
}

export function useDubLivePreview({ enabled, language }: { enabled: boolean; language: string }) {
  const { t } = useTranslation();
  const [liveSegmentId, setLiveSegmentId] = useState<string | null>(null);
  const session = useRef<LiveSession | null>(null);
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const intent = useRef(0);
  const enabledRef = useRef(enabled);
  const lastToast = useRef({ key: '', at: 0 });

  const notify = useCallback((key: string, message: string) => {
    const now = Date.now();
    if (lastToast.current.key === key && now - lastToast.current.at < TOAST_THROTTLE_MS) return;
    lastToast.current = { key, at: now };
    toast.error(message);
  }, []);

  const releaseWork = useCallback((active: LiveSession) => {
    active.releaseSynthesis?.();
    active.releaseSynthesis = null;
    active.finishActivity?.();
    active.finishActivity = null;
  }, []);

  const stop = useCallback(() => {
    if (timer.current) {
      clearTimeout(timer.current);
      timer.current = null;
    }
    const active = session.current;
    if (!active) return;
    session.current = null;
    active.socket?.close();
    active.socket = null;
    active.player?.fail();
    active.player = null;
    releaseWork(active);
    setLiveSegmentId(null);
  }, [releaseWork]);

  const start = useCallback(
    async (segment: DubSegment, text: string, requestIntent: number) => {
      if (
        requestIntent !== intent.current ||
        !enabledRef.current ||
        !supportsStreamingPreview() ||
        !text.trim()
      )
        return;
      stop();
      const inputs = segmentGenInputs({ ...segment, text });
      if (!inputs.profile_id && !inputs.instruct) {
        notify('voice', t('dub.live_preview_pick_voice'));
        return;
      }
      const releaseSynthesis = acquireSynthesis();
      if (!releaseSynthesis) {
        notify('busy', t('tts_errors.generation_in_progress'));
        return;
      }
      const active: LiveSession = {
        id: segment.id,
        socket: null,
        player: null,
        releaseSynthesis,
        finishActivity: beginAppActivity('synthesis'),
      };
      session.current = active;
      setLiveSegmentId(segment.id);
      try {
        const socket = new WebSocket(await backendWebSocketUrl('/ws/tts'));
        if (session.current !== active || requestIntent !== intent.current) {
          socket.close();
          releaseWork(active);
          return;
        }
        active.socket = socket;
        socket.binaryType = 'arraybuffer';
        socket.onopen = () => {
          const payload: Record<string, unknown> = {
            text,
            speed: inputs.speed || 1,
          };
          if (inputs.profile_id) payload.voice = inputs.profile_id;
          if (inputs.instruct) payload.instruct = inputs.instruct;
          const targetLanguage = inputs.target_lang || language;
          if (targetLanguage && targetLanguage !== 'Auto') payload.language = targetLanguage;
          socket.send(JSON.stringify(payload));
        };
        socket.onmessage = (event) => {
          if (session.current !== active) return;
          if (event.data instanceof ArrayBuffer) {
            active.player?.appendPcm16Bytes(event.data);
            return;
          }
          let message: { type?: string; sample_rate?: number; detail?: string };
          try {
            message = JSON.parse(String(event.data)) as typeof message;
          } catch {
            return;
          }
          if (message.type === 'start' && Number.isFinite(message.sample_rate)) {
            active.player = createStreamingPreview(message.sample_rate!, 0, () => {
              if (session.current !== active) return;
              session.current = null;
              setLiveSegmentId(null);
            });
          } else if (message.type === 'done') {
            active.socket = null;
            socket.onclose = null;
            socket.close();
            releaseWork(active);
            if (active.player) active.player.finalize();
            else {
              session.current = null;
              setLiveSegmentId(null);
            }
          } else if (message.type === 'error') {
            notify('stream', t('tts_errors.error_prefix', { message: message.detail || '' }));
            stop();
          }
        };
        socket.onerror = () => {
          if (session.current === active) stop();
        };
        socket.onclose = () => {
          if (session.current === active) stop();
        };
      } catch (error) {
        if (session.current === active) {
          notify(
            'connect',
            t('tts_errors.error_prefix', {
              message: error instanceof Error ? error.message : String(error),
            }),
          );
          stop();
        } else releaseWork(active);
      }
    },
    [language, notify, releaseWork, stop, t],
  );

  const onEdit = useCallback(
    (segment: DubSegment, text: string) => {
      if (!enabledRef.current) return;
      const nextIntent = ++intent.current;
      stop();
      timer.current = setTimeout(() => {
        timer.current = null;
        void start(segment, text, nextIntent);
      }, LIVE_DUB_PREVIEW_DELAY_MS);
    },
    [start, stop],
  );

  const onToggle = useCallback(
    (segment: DubSegment) => {
      const nextIntent = ++intent.current;
      if (session.current?.id === segment.id) {
        stop();
        return;
      }
      void start(segment, segment.text, nextIntent);
    },
    [start, stop],
  );

  useEffect(() => {
    enabledRef.current = enabled;
    if (!enabled) {
      intent.current += 1;
      stop();
    }
  }, [enabled, stop]);
  useEffect(
    () => () => {
      intent.current += 1;
      stop();
    },
    [stop],
  );

  return { liveSegmentId, onEdit, onToggle, stop };
}
