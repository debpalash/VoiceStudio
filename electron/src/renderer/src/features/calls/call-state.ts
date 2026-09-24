import type { AgentState, CallEvent, CallOutcome, CallRecord, CallSpeaker } from '@/lib/api/calls';

/** The four steps the status timeline shows. */
export type CallPhase = 'dialing' | 'ringing' | 'in_call' | 'ended';
export const CALL_PHASES: readonly CallPhase[] = ['dialing', 'ringing', 'in_call', 'ended'];

const PHASE_BY_STATUS: Record<string, CallPhase> = {
  queued: 'dialing',
  initiated: 'dialing',
  dialing: 'dialing',
  ringing: 'ringing',
  answered: 'in_call',
  in_progress: 'in_call',
  in_call: 'in_call',
  active: 'in_call',
  completed: 'ended',
  ended: 'ended',
  failed: 'ended',
  busy: 'ended',
  no_answer: 'ended',
  canceled: 'ended',
  cancelled: 'ended',
  hung_up: 'ended',
};

/** Map any backend/Twilio status spelling (`in-progress`, `IN_PROGRESS`) onto a phase. */
export function callPhase(status: string | null | undefined): CallPhase {
  const key = (status ?? '').toLowerCase().replace(/[\s-]+/g, '_');
  return PHASE_BY_STATUS[key] ?? 'dialing';
}

export interface LiveLine {
  speaker: CallSpeaker;
  text: string;
  t: number;
  /** False while the speaker is still talking (interim ASR). */
  final: boolean;
}

export interface LiveCallState {
  status: string;
  agentState: AgentState | null;
  lines: LiveLine[];
  outcome: CallOutcome | null;
  summary: string | null;
  ended: boolean;
  takeover: boolean;
  durationS: number | null;
  endedAt: string | number | null;
}

export type LiveCallAction =
  | { kind: 'snapshot'; call: CallRecord }
  | { kind: 'event'; event: CallEvent }
  | { kind: 'takeover'; enabled: boolean }
  | { kind: 'reset' };

export function initialLiveState(call?: CallRecord | null): LiveCallState {
  const base: LiveCallState = {
    status: 'dialing',
    agentState: null,
    lines: [],
    outcome: null,
    summary: null,
    ended: false,
    takeover: false,
    durationS: null,
    endedAt: null,
  };
  return call ? liveCallReducer(base, { kind: 'snapshot', call }) : base;
}

const sameLine = (a: LiveLine, b: { speaker: CallSpeaker; text: string; t: number }) =>
  a.speaker === b.speaker && a.text === b.text && Math.abs(a.t - b.t) < 0.001;

function addTranscript(lines: LiveLine[], line: LiveLine): LiveLine[] {
  // An interim line is the speaker's still-growing utterance: it replaces the
  // speaker's previous interim line instead of stacking duplicates.
  const pending = lines.findIndex((item) => !item.final && item.speaker === line.speaker);
  if (line.final && lines.some((item) => item.final && sameLine(item, line))) {
    // A reconnect replays history; keep one copy and drop the stale interim.
    return pending === -1 ? lines : lines.filter((_, index) => index !== pending);
  }
  if (pending !== -1) return lines.map((item, index) => (index === pending ? line : item));
  return [...lines, line];
}

export function liveCallReducer(state: LiveCallState, action: LiveCallAction): LiveCallState {
  if (action.kind === 'reset') return initialLiveState();
  if (action.kind === 'takeover') return { ...state, takeover: action.enabled };
  if (action.kind === 'snapshot') {
    const { call } = action;
    // A snapshot can be older than lines already streamed (a reconnect resync
    // racing live events), so merge instead of replacing: never drop a line.
    const finals: LiveLine[] = state.lines.filter((line) => line.final);
    for (const line of call.transcript ?? []) {
      if (!finals.some((item) => sameLine(item, line))) finals.push({ ...line, final: true });
    }
    finals.sort((a, b) => a.t - b.t);
    // Interim lines the snapshot cannot know about survive, after the finals.
    const interim = state.lines.filter(
      (line) =>
        !line.final && !finals.some((item) => item.speaker === line.speaker && item.t >= line.t),
    );
    const ended = state.ended || callPhase(call.status) === 'ended' || call.ended_at != null;
    return {
      ...state,
      // Once ended, a late snapshot saying the call is still live must not move
      // the timeline back.
      status:
        state.ended && callPhase(call.status) !== 'ended'
          ? state.status
          : call.status || state.status,
      // An ended call keeps the interim lines the record did not supersede, as
      // final: nobody is still talking, and dropping them loses the last words.
      lines: ended
        ? [...finals, ...interim.map((line) => ({ ...line, final: true }))]
        : [...finals, ...interim],
      outcome: call.outcome ?? state.outcome,
      summary: call.summary ?? state.summary,
      ended,
      agentState: ended ? null : state.agentState,
      takeover: call.takeover ?? state.takeover,
      durationS: call.duration_s ?? state.durationS,
      endedAt: call.ended_at ?? state.endedAt,
    };
  }
  const { event } = action;
  switch (event.type) {
    case 'status': {
      const ended = state.ended || callPhase(event.status) === 'ended';
      return {
        ...state,
        status: event.status,
        ended,
        agentState: ended ? null : state.agentState,
      };
    }
    case 'transcript':
      return {
        ...state,
        lines: addTranscript(state.lines, {
          speaker: event.speaker,
          text: event.text,
          t: event.t,
          final: event.final,
        }),
      };
    case 'agent_state':
      return state.ended
        ? state
        : {
            ...state,
            agentState: event.state,
            takeover: event.takeover ?? state.takeover,
          };
    case 'outcome':
      return { ...state, outcome: event.outcome, summary: event.summary ?? state.summary };
    case 'ended': {
      const closed: LiveCallState = {
        ...state,
        ended: true,
        agentState: null,
        takeover: false,
        status: event.status ?? (callPhase(state.status) === 'ended' ? state.status : 'completed'),
        durationS: event.duration_s ?? state.durationS,
        endedAt: event.ended_at ?? state.endedAt ?? Date.now() / 1000,
        lines: state.lines.filter((line) => line.final || line.text.trim()),
      };
      // The backend may send the finished record along: fold it in directly.
      // The backend may send the finished record along: merge it first, so a
      // partial it completes is replaced rather than kept twice.
      if (event.call) return liveCallReducer(closed, { kind: 'snapshot', call: event.call });
      return { ...closed, lines: closed.lines.map((line) => ({ ...line, final: true })) };
    }
  }
}

/** m:ss (or h:mm:ss) for the elapsed-time readout. */
export function formatDuration(totalSeconds: number | null | undefined): string {
  const seconds = Math.max(0, Math.floor(totalSeconds ?? 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = String(seconds % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${s}` : `${m}:${s}`;
}
