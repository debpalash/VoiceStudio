/**
 * useDubLivePreview — live-as-you-edit dub audio (ROADMAP: "Real-time dub
 * preview (stream TTS as you edit)").
 *
 * When the opt-in `dubLivePreview` pref is on and the user edits a segment's
 * translated text, the edit is debounced (~400 ms) and the line is streamed
 * over the EXISTING `/ws/tts` WebSocket (binary PCM16 sentence chunks) with
 * the segment's CAST voice, playing progressively through the same Web Audio
 * chunk player the streaming /generate preview uses. A new keystroke or a
 * segment change closes the previous socket first, so sockets never pile up.
 *
 * Boundaries (deliberate):
 *   • Admission — every stream runs inside `withTtsInflight`, the same
 *     process-wide chokepoint /generate holds, so a live preview can never
 *     race a running generation (busy → the standard localized toast). The
 *     backend side already serializes on the GPU pool.
 *   • Nothing is persisted: `/ws/tts` audio is ear-only. Export still goes
 *     through the full-quality generate path (`finalizeTtsBeforeExport`),
 *     and the incremental re-dub flow is untouched.
 *   • Voice resolution goes through `segmentGenInputs` — the SAME expansion
 *     the dub generate body uses (`preset:` → instruct) — so the preview
 *     voice cannot disagree with what Generate would render. No CAST voice
 *     at all → skip with an actionable "assign a voice" toast.
 *   • Local-first: `/ws/tts` streams on this machine by design (the route
 *     itself refuses remote workers); no new outbound calls.
 */
import { useCallback, useEffect, useRef, useState } from 'react';
import { toast } from 'react-hot-toast';
import { useTranslation } from 'react-i18next';
import { API } from '../api/client';
import { authenticatedWsUrl } from '../api/authSession';
import { withTtsInflight, TtsGenerationBusyError } from '../api/generate';
import { createStreamingChunkPlayer, supportsStreamingPreview } from '../utils/streamingTts';
import { segmentGenInputs } from '../utils/segments';
import { useAppStore } from '../store';

/** Keystroke → stream debounce. Long enough to skip mid-word churn, short
 *  enough to feel live. Exported for the fake-timer tests. */
export const LIVE_PREVIEW_DEBOUNCE_MS = 400;

/** Same-message toast throttle so a typing burst can't stack toasts. */
const TOAST_THROTTLE_MS = 4000;

export default function useDubLivePreview({ enabled }) {
  const { t } = useTranslation();
  const [liveSegId, setLiveSegId] = useState(null);
  const enabledRef = useRef(enabled);
  const timerRef = useRef(null);
  const sessionRef = useRef(null); // { segId, ws, player, abort, admission }
  const intentRef = useRef(0);
  const lastToastRef = useRef({ key: '', at: 0 });

  const throttledToast = useCallback((key, show) => {
    const now = Date.now();
    const last = lastToastRef.current;
    if (last.key === key && now - last.at < TOAST_THROTTLE_MS) return;
    lastToastRef.current = { key, at: now };
    show();
  }, []);

  /** Close the live socket and silence its player. Resolves the stream's
   *  admission promise, so `withTtsInflight` releases the in-flight slot. */
  const abortSession = useCallback(() => {
    if (timerRef.current) {
      clearTimeout(timerRef.current);
      timerRef.current = null;
    }
    const session = sessionRef.current;
    if (!session) return Promise.resolve();
    session.abort();
    // The admission promise settles only after withTtsInflight has released
    // the slot — awaiting it means the next stream can't trip over our own
    // in-flight count.
    return session.admission ?? Promise.resolve();
  }, []);

  /** One socket lifetime: connect → send → play chunks → done/error/close. */
  const streamOnce = useCallback(
    (session, payload) =>
      new Promise((resolve) => {
        if (session.settled) {
          resolve();
          return;
        }
        const stopPlayer = () => {
          const player = session.player;
          session.player = null;
          try {
            player?.fail();
          } catch {
            /* teardown must never throw into the admission chain */
          }
        };
        const settle = ({ keepPlayer = false } = {}) => {
          if (session.settled) return;
          session.settled = true;
          if (!keepPlayer) stopPlayer();
          try {
            session.ws?.close();
          } catch {
            /* already closed */
          }
          if (!keepPlayer && sessionRef.current === session) sessionRef.current = null;
          setLiveSegId((cur) => (cur === session.segId ? null : cur));
          resolve();
        };
        session.abort = () => {
          if (!session.settled) {
            settle();
            return;
          }
          stopPlayer();
          if (sessionRef.current === session) sessionRef.current = null;
        };

        (async () => {
          let endpoint;
          try {
            // Same one-use-ticket boundary as /ws/events and /ws/transcribe.
            endpoint = await authenticatedWsUrl('/ws/tts', { apiBase: API });
          } catch (err) {
            // Say so — a silently dead toggle is the one failure the user
            // can't diagnose (a backend that refuses the ticket looks exactly
            // like "the feature does nothing").
            throttledToast('live-connect', () =>
              toast.error(t('tts_errors.error_prefix', { message: err?.message || '' })),
            );
            settle();
            return;
          }
          if (session.settled) return;
          let ws;
          try {
            ws = new WebSocket(endpoint);
          } catch {
            settle();
            return;
          }
          ws.binaryType = 'arraybuffer';
          session.ws = ws;
          ws.onopen = () => ws.send(JSON.stringify(payload));
          ws.onmessage = (event) => {
            if (session.settled) return;
            if (typeof event.data !== 'string') {
              session.player?.appendPcm16Bytes(event.data);
              return;
            }
            let msg;
            try {
              msg = JSON.parse(event.data);
            } catch {
              return;
            }
            if (msg.type === 'start') {
              session.player = createStreamingChunkPlayer({
                label: payload.text,
                sampleRate: msg.sample_rate,
                onDone: () => {
                  session.player = null;
                  if (!session.settled) {
                    session.abort();
                  } else if (sessionRef.current === session) {
                    sessionRef.current = null;
                  }
                },
              });
            } else if (msg.type === 'done') {
              // Release network/admission state now, but retain the player in
              // sessionRef until its buffered tail ends. A later edit, toggle,
              // or unmount can therefore still silence obsolete audio.
              const keepPlayer = Boolean(session.player);
              session.player?.finalize();
              settle({ keepPlayer });
            } else if (msg.type === 'error') {
              throttledToast('live-error', () =>
                toast.error(t('tts_errors.error_prefix', { message: msg.detail || '' })),
              );
              settle();
            }
            // "routing" frames are advisory (local-stream notice) — ignored.
          };
          ws.onerror = () => settle();
          ws.onclose = () => settle();
        })();
      }),
    [t, throttledToast],
  );

  const startStream = useCallback(
    async (seg, text, intent) => {
      if (intent !== intentRef.current) return;
      if (!enabledRef.current || !supportsStreamingPreview()) return;
      if (!text || !text.trim()) {
        void abortSession();
        return;
      }
      // Same voice expansion as the dub generate body (#281 helper): a
      // `preset:` id becomes instruct text, everything else is a profile id.
      const inputs = segmentGenInputs({ ...seg, text });
      if (!inputs.profile_id && !inputs.instruct) {
        void abortSession();
        throttledToast('live-no-voice', () =>
          toast(t('dub.live_preview_pick_voice'), { icon: '🎤' }),
        );
        return;
      }
      const payload = { text, speed: inputs.speed || 1.0 };
      if (inputs.profile_id) payload.voice = inputs.profile_id;
      if (inputs.instruct) payload.instruct = inputs.instruct;
      const lang = inputs.target_lang || useAppStore.getState().dubLang;
      if (lang && lang !== 'Auto') payload.language = lang;

      await abortSession();
      if (!enabledRef.current || intent !== intentRef.current) return;

      const session = { segId: seg.id, ws: null, player: null, settled: false };
      // Pre-stream abort: admission can refuse (busy) before streamOnce ever
      // installs the socket-aware settle — the session must still clear its
      // refs so the row indicator can't stick on.
      session.abort = () => {
        session.settled = true;
        if (sessionRef.current === session) sessionRef.current = null;
        setLiveSegId((cur) => (cur === session.segId ? null : cur));
      };
      sessionRef.current = session;
      setLiveSegId(seg.id);
      session.admission = withTtsInflight(() => streamOnce(session, payload)).catch((err) => {
        session.abort();
        if (err instanceof TtsGenerationBusyError) {
          throttledToast('live-busy', () =>
            toast(t('tts_errors.generation_in_progress'), { icon: '⏳' }),
          );
        }
      });
    },
    [abortSession, streamOnce, t, throttledToast],
  );

  /** Text-input edit: close the previous socket NOW, re-stream after the
   *  debounce. A segment switch mid-type goes through the same path. */
  const onLiveEdit = useCallback(
    (seg, text) => {
      if (!enabledRef.current) return;
      const intent = ++intentRef.current;
      void abortSession();
      timerRef.current = setTimeout(() => {
        timerRef.current = null;
        void startStream(seg, text, intent);
      }, LIVE_PREVIEW_DEBOUNCE_MS);
    },
    [abortSession, startStream],
  );

  /** Row speaker button: stream this line's current text now, or stop it. */
  const onLiveToggle = useCallback(
    (seg) => {
      const intent = ++intentRef.current;
      if (sessionRef.current?.segId === seg.id) {
        void abortSession();
        return;
      }
      void startStream(seg, seg.text, intent);
    },
    [abortSession, startStream],
  );

  // Toggle off / unmount: nothing may keep streaming. The ref keeps the
  // debounce/stream callbacks stable across toggles (row memo identity).
  useEffect(() => {
    enabledRef.current = enabled;
    if (!enabled) {
      intentRef.current += 1;
      void abortSession();
    }
  }, [enabled, abortSession]);
  useEffect(
    () => () => {
      intentRef.current += 1;
      void abortSession();
    },
    [abortSession],
  );

  return { liveSegId, onLiveEdit, onLiveToggle, stop: abortSession };
}
