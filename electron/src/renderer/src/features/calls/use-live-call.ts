import { useCallback, useEffect, useReducer, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import { getCall, callTime, type CallEvent, type CallRecord } from '@/lib/api/calls';
import { callPhase, initialLiveState, liveCallReducer, type LiveCallState } from './call-state';
import { useCallEvents, type StreamState } from './use-call-events';

export const CALLS_KEY = ['calls'] as const;
export const callKey = (id: string) => ['calls', 'detail', id] as const;

export interface LiveCall {
  call: CallRecord | null;
  state: LiveCallState;
  stream: StreamState;
  loading: boolean;
  error: unknown;
  /** Seconds since the other side answered, ticking while the call is live. */
  elapsed: number;
  setTakeover: (enabled: boolean) => void;
}

/** One selected call: its record, live SSE state and a ticking clock. */
export function useLiveCall(callId: string | null): LiveCall {
  const queryClient = useQueryClient();
  const query = useQuery({
    queryKey: callKey(callId ?? ''),
    queryFn: ({ signal }) => getCall(callId as string, signal),
    enabled: Boolean(callId),
  });
  const [state, dispatch] = useReducer(liveCallReducer, undefined, () => initialLiveState());
  const [answeredAt, setAnsweredAt] = useState<number | null>(null);
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    dispatch({ kind: 'reset' });
    setAnsweredAt(null);
  }, [callId]);
  useEffect(() => {
    if (query.data && query.data.id === callId) dispatch({ kind: 'snapshot', call: query.data });
  }, [query.data, callId]);

  const live = Boolean(callId && query.data && !state.ended);
  const onEvent = useCallback(
    (event: CallEvent) => {
      dispatch({ kind: 'event', event });
      if (
        event.type === 'ended' ||
        event.type === 'outcome' ||
        (event.type === 'status' && callPhase(event.status) === 'ended')
      ) {
        // The finished record carries the summary and the full transcript;
        // CALLS_KEY also covers this call's detail query.
        void queryClient.invalidateQueries({ queryKey: CALLS_KEY });
      }
    },
    [queryClient],
  );
  const stream = useCallEvents(callId, live, onEvent, () => void query.refetch());

  const phase = callPhase(state.status);
  useEffect(() => {
    if (phase === 'in_call' && answeredAt === null) {
      setAnsweredAt(callTime(query.data?.started_at) ?? Date.now());
    }
  }, [phase, answeredAt, query.data?.started_at]);
  useEffect(() => {
    if (!live || phase !== 'in_call') return;
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, [live, phase]);

  const started = callTime(query.data?.started_at) ?? answeredAt;
  const elapsed = state.ended
    ? (state.durationS ?? query.data?.duration_s ?? 0)
    : started
      ? Math.max(0, (now - started) / 1000)
      : 0;

  return {
    call: query.data && query.data.id === callId ? query.data : null,
    state,
    stream,
    loading: query.isLoading,
    error: query.error,
    elapsed,
    setTakeover: (enabled) => dispatch({ kind: 'takeover', enabled }),
  };
}
