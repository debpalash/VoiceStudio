import { expect, it } from 'vitest';
import type { CallEvent, CallRecord } from '@/lib/api/calls';
import { callPhase, formatDuration, initialLiveState, liveCallReducer } from './call-state';

const record = (patch: Partial<CallRecord> = {}): CallRecord => ({
  id: 'c1',
  direction: 'outbound',
  to_masked: '+1 •••• 0199',
  status: 'ringing',
  created_at: 1_700_000_000,
  started_at: null,
  ended_at: null,
  duration_s: null,
  profile_id: 'p1',
  brief: 'Book a table',
  outcome: null,
  summary: null,
  ...patch,
});
const play = (events: CallEvent[], state = initialLiveState()) =>
  events.reduce((current, event) => liveCallReducer(current, { kind: 'event', event }), state);

it('maps backend and Twilio statuses onto the four timeline phases', () => {
  expect(callPhase('queued')).toBe('dialing');
  expect(callPhase('ringing')).toBe('ringing');
  expect(callPhase('in-progress')).toBe('in_call');
  expect(callPhase('IN_PROGRESS')).toBe('in_call');
  expect(callPhase('no-answer')).toBe('ended');
  expect(callPhase(undefined)).toBe('dialing');
});

it('builds the live view from a stream of SSE events', () => {
  const state = play([
    { type: 'status', status: 'in_progress' },
    { type: 'agent_state', state: 'speaking' },
    { type: 'transcript', speaker: 'agent', text: 'Hi, I would like', final: false, t: 1 },
    { type: 'transcript', speaker: 'agent', text: 'Hi, I would like a table', final: true, t: 1 },
    { type: 'agent_state', state: 'listening' },
    { type: 'transcript', speaker: 'caller', text: 'Sure', final: false, t: 4 },
    { type: 'transcript', speaker: 'caller', text: 'Sure, for when?', final: false, t: 4 },
  ]);
  expect(callPhase(state.status)).toBe('in_call');
  expect(state.agentState).toBe('listening');
  expect(state.lines).toEqual([
    { speaker: 'agent', text: 'Hi, I would like a table', final: true, t: 1 },
    { speaker: 'caller', text: 'Sure, for when?', final: false, t: 4 },
  ]);
});

it('does not duplicate transcript lines replayed after a reconnect', () => {
  const line: CallEvent = {
    type: 'transcript',
    speaker: 'agent',
    text: 'Hello',
    final: true,
    t: 2,
  };
  const state = play([line, line]);
  expect(state.lines).toHaveLength(1);
  const resynced = liveCallReducer(state, {
    kind: 'snapshot',
    call: record({
      status: 'in_progress',
      transcript: [{ speaker: 'agent', text: 'Hello', t: 2 }],
    }),
  });
  expect(resynced.lines).toHaveLength(1);
});

it('records the outcome and freezes the call when it ends', () => {
  const state = play([
    { type: 'status', status: 'in_progress' },
    { type: 'agent_state', state: 'thinking' },
    { type: 'outcome', outcome: 'booked', summary: 'Table for 2 at 8pm Friday.' },
    { type: 'ended', duration_s: 95 },
    { type: 'agent_state', state: 'speaking' },
  ]);
  expect(state).toMatchObject({
    ended: true,
    agentState: null,
    outcome: 'booked',
    summary: 'Table for 2 at 8pm Friday.',
    durationS: 95,
    takeover: false,
  });
  expect(callPhase(state.status)).toBe('ended');
});

it('hydrates a finished call from its record and resets between calls', () => {
  const state = initialLiveState(
    record({
      status: 'completed',
      ended_at: 1_700_000_100,
      duration_s: 60,
      outcome: 'needs_you',
      summary: 'They need a callback.',
      transcript: [{ speaker: 'caller', text: 'Call back later', t: 3 }],
    }),
  );
  expect(state).toMatchObject({ ended: true, outcome: 'needs_you', durationS: 60 });
  expect(state.lines).toEqual([{ speaker: 'caller', text: 'Call back later', t: 3, final: true }]);
  const taken = liveCallReducer(state, { kind: 'takeover', enabled: true });
  expect(taken.takeover).toBe(true);
  expect(liveCallReducer(taken, { kind: 'reset' })).toEqual(initialLiveState());
});

it('formats elapsed time', () => {
  expect(formatDuration(0)).toBe('0:00');
  expect(formatDuration(95.7)).toBe('1:35');
  expect(formatDuration(3725)).toBe('1:02:05');
  expect(formatDuration(null)).toBe('0:00');
});

it('never drops streamed lines when a stale snapshot arrives after a reconnect', () => {
  const state = play([
    { type: 'transcript', speaker: 'agent', text: 'Hello', final: true, t: 1 },
    { type: 'transcript', speaker: 'caller', text: 'Hi there', final: true, t: 3 },
    { type: 'transcript', speaker: 'agent', text: 'A table for two', final: true, t: 5 },
  ]);
  const resynced = liveCallReducer(state, {
    kind: 'snapshot',
    call: record({
      status: 'in_progress',
      transcript: [
        { speaker: 'agent', text: 'Hello', t: 1 },
        { speaker: 'caller', text: 'Hi there', t: 3 },
      ],
    }),
  });
  expect(resynced.lines.map((line) => line.text)).toEqual(['Hello', 'Hi there', 'A table for two']);
  const ended = liveCallReducer(resynced, {
    kind: 'snapshot',
    call: record({ status: 'completed', transcript: [{ speaker: 'caller', text: 'Bye', t: 9 }] }),
  });
  expect(ended.lines.map((line) => line.text)).toEqual([
    'Hello',
    'Hi there',
    'A table for two',
    'Bye',
  ]);
});

it('applies take-over from agent_state and the finished record sent with ended', () => {
  const taken = play([{ type: 'agent_state', state: 'listening', takeover: true }]);
  expect(taken.takeover).toBe(true);
  const state = play(
    [
      {
        type: 'ended',
        call: record({
          status: 'completed',
          ended_at: 1_700_000_050,
          duration_s: 41,
          outcome: 'done',
          summary: 'Open 9 to 5.',
          transcript: [{ speaker: 'caller', text: 'We open at 9', t: 4 }],
        }),
      },
    ],
    taken,
  );
  expect(state).toMatchObject({
    ended: true,
    outcome: 'done',
    summary: 'Open 9 to 5.',
    durationS: 41,
  });
  expect(state.lines.map((line) => line.text)).toEqual(['We open at 9']);
});

it('keeps an ended call ended when a stale snapshot says it is live', () => {
  const ended = play([{ type: 'ended', status: 'completed' } as CallEvent]);
  const after = liveCallReducer(ended, {
    kind: 'snapshot',
    call: record({ status: 'in-progress' }),
  });
  expect(after.ended).toBe(true);
  expect(callPhase(after.status)).toBe('ended');
});

it('keeps the last interim utterance when the call ends with its record', () => {
  const state = play([
    { type: 'transcript', speaker: 'caller', text: 'See you at eight', final: false, t: 5 },
    { type: 'ended', status: 'completed', call: record({ status: 'completed', transcript: [] }) },
  ] as CallEvent[]);
  expect(state.lines.map((line) => [line.text, line.final])).toEqual([['See you at eight', true]]);
});

it('replaces a partial utterance with the completed one from the ended record', () => {
  const state = play([
    { type: 'transcript', speaker: 'caller', text: 'See you at', final: false, t: 5 },
    {
      type: 'ended',
      status: 'completed',
      call: record({
        status: 'completed',
        transcript: [{ speaker: 'caller', text: 'See you at eight', t: 5 }],
      }),
    },
  ] as CallEvent[]);
  expect(state.lines.map((line) => [line.text, line.final])).toEqual([['See you at eight', true]]);
});
