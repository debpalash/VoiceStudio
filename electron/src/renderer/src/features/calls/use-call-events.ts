import { useEffect, useRef, useState } from 'react';
import { callEventsUrl, parseCallEvent, type CallEvent } from '@/lib/api/calls';

export type StreamState = 'idle' | 'connecting' | 'open' | 'reconnecting' | 'closed';

const EVENT_TYPES = ['status', 'transcript', 'agent_state', 'outcome', 'ended'] as const;
const MAX_RETRIES = 8;

export function reconnectDelay(attempt: number): number {
  return Math.min(10_000, 1000 * 2 ** Math.max(0, attempt - 1));
}

/**
 * Follow a call's live events. The browser retries dropped connections on its
 * own; when it gives up (an HTTP error closes the stream) we reopen with
 * backoff, and past the retry budget keep retrying at the capped delay
 * while reporting the stream as lost. Every reopen asks the caller to resync from `GET /calls/{id}`, so a
 * gap in the stream never loses transcript lines. The stream closes for good
 * on `ended` and on unmount.
 */
export function useCallEvents(
  callId: string | null,
  enabled: boolean,
  onEvent: (event: CallEvent) => void,
  onResync: () => void,
): StreamState {
  const [state, setState] = useState<StreamState>('idle');
  const handlers = useRef({ onEvent, onResync });
  handlers.current = { onEvent, onResync };

  useEffect(() => {
    if (!callId || !enabled || typeof EventSource === 'undefined') {
      setState('idle');
      return;
    }
    let source: EventSource | null = null;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let attempts = 0;
    let finished = false;
    let reopened = false;

    const close = (next: StreamState) => {
      finished = next === 'closed';
      source?.close();
      source = null;
      setState(next);
    };
    const handle = (name: string) => (message: MessageEvent<string>) => {
      const event = parseCallEvent(message.data, name);
      if (!event) return;
      handlers.current.onEvent(event);
      if (event.type === 'ended') close('closed');
    };
    const open = () => {
      if (finished) return;
      // Past the retry budget the UI says the stream is lost, but we keep
      // trying at the capped delay so a long call still recovers.
      if (attempts <= MAX_RETRIES) setState(attempts ? 'reconnecting' : 'connecting');
      const current = new EventSource(callEventsUrl(callId));
      source = current;
      current.onopen = () => {
        if (reopened) handlers.current.onResync();
        reopened = false;
        attempts = 0;
        setState('open');
      };
      current.onmessage = handle('message');
      for (const type of EVENT_TYPES) current.addEventListener(type, handle(type) as EventListener);
      current.onerror = () => {
        if (finished || source !== current) return;
        reopened = true;
        if (current.readyState === EventSource.CONNECTING) {
          if (attempts <= MAX_RETRIES) setState('reconnecting');
          return;
        }
        current.close();
        source = null;
        attempts += 1;
        if (attempts > MAX_RETRIES) {
          setState('closed');
          handlers.current.onResync();
        } else {
          setState('reconnecting');
        }
        timer = setTimeout(open, reconnectDelay(attempts));
      };
    };
    open();
    return () => {
      finished = true;
      if (timer) clearTimeout(timer);
      source?.close();
      source = null;
    };
  }, [callId, enabled]);

  return state;
}
