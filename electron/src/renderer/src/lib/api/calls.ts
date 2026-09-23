/**
 * Typed client for the phone-call agent (`/calls`). VoiceStudio places and
 * answers calls through the user's own Twilio number; nothing is dialled until
 * the user confirms a call in the Calls workspace.
 */
import { ApiError, apiFetch, apiJson, apiPath } from './client';

export type CallDirection = 'outbound' | 'inbound';
export type CallOutcome = 'booked' | 'done' | 'not_done' | 'needs_you' | 'failed';
export type CallSpeaker = 'agent' | 'caller';
export type AgentState = 'listening' | 'thinking' | 'speaking';
export type InboundMode = 'greeting' | 'agent';
export type ReadinessId = 'credentials' | 'tunnel' | 'number' | 'llm' | 'asr' | 'voice';

export interface TranscriptLine {
  speaker: CallSpeaker;
  text: string;
  /** Seconds since the call was answered. */
  t: number;
}

export interface CallRecord {
  id: string;
  direction: CallDirection;
  to_masked: string;
  status: string;
  /** ISO timestamps or epoch seconds; parse with `callTime`. */
  created_at: string | number | null;
  started_at: string | number | null;
  ended_at: string | number | null;
  duration_s: number | null;
  profile_id: string | null;
  brief: string;
  outcome: CallOutcome | null;
  summary: string | null;
  transcript?: TranscriptLine[];
  max_minutes?: number | null;
  takeover?: boolean;
  /** The opening line actually spoken; it always plays in full. */
  disclosure?: string;
  agent_state?: AgentState | null;
  /** True when a recording exists (`GET /calls/{id}/recording`). */
  recording?: boolean | string | null;
  error?: string | null;
  /** Detail view only: status changes with their times. */
  timeline?: Array<{ status: string; t?: number | string | null; at?: number | string | null }>;
}

export interface StartCallInput {
  to: string;
  brief: string;
  profile_id: string;
  engine?: string;
  language?: string;
  /**
   * The line spoken first. Omit it to use the settings template (the backend
   * fills `{name}`); an empty string opts this call out of the disclosure.
   */
  disclosure?: string;
  max_minutes?: number;
}

export interface CallSettings {
  from_number: string;
  /** Fills `{name}` in the disclosure template. */
  user_name?: string;
  disclosure_template: string;
  inbound_mode: InboundMode;
  inbound_brief: string;
  max_concurrent: number;
  record_calls: boolean;
}

export interface ReadinessItem {
  id: ReadinessId;
  ok: boolean;
  detail: string;
}

export type CallEvent =
  | { type: 'status'; status: string; [key: string]: unknown }
  | {
      type: 'transcript';
      speaker: CallSpeaker;
      text: string;
      final: boolean;
      t: number;
    }
  | { type: 'agent_state'; state: AgentState; takeover?: boolean }
  | { type: 'outcome'; outcome: CallOutcome | null; summary?: string | null }
  | {
      type: 'ended';
      status?: string;
      duration_s?: number | null;
      ended_at?: string | number;
      /** The finished record (#2306 sends it with `ended`). */
      call?: CallRecord;
    };

const id = (value: string) => encodeURIComponent(value);
const post = (body: unknown): RequestInit => ({ method: 'POST', body: JSON.stringify(body) });

/**
 * True when the backend predates the Calls API: the route itself is missing.
 * Any other failure is a real error the UI must show.
 */
export function callsUnavailable(error: unknown): boolean {
  return error instanceof ApiError && (error.status === 404 || error.status === 405);
}

export async function startCall(input: StartCallInput): Promise<CallRecord> {
  const { call } = await apiJson<{ call: CallRecord }>('/calls', post(input));
  return call;
}

export async function listCalls(limit = 50, signal?: AbortSignal): Promise<CallRecord[]> {
  const { calls } = await apiJson<{ calls: CallRecord[] }>(`/calls?limit=${limit}`, { signal });
  return calls;
}

export function getCall(callId: string, signal?: AbortSignal): Promise<CallRecord> {
  return apiJson<CallRecord>(`/calls/${id(callId)}`, { signal });
}

export async function sayOnCall(callId: string, text: string): Promise<void> {
  await apiJson<unknown>(`/calls/${id(callId)}/say`, post({ text }));
}

export async function setTakeover(callId: string, enabled: boolean): Promise<void> {
  await apiJson<unknown>(`/calls/${id(callId)}/takeover`, post({ enabled }));
}

export async function hangUp(callId: string): Promise<void> {
  await apiJson<unknown>(`/calls/${id(callId)}/hangup`, post({}));
}

export async function deleteCall(callId: string): Promise<void> {
  await apiFetch(`/calls/${id(callId)}`, { method: 'DELETE' });
}

/** Playable URL for a call's recording, when the record says one exists. */
export function callRecordingUrl(callId: string): string {
  return apiPath(`/calls/${id(callId)}/recording`);
}

/** Fill `{name}` the way the backend does (`someone` when no name is known). */
export function renderDisclosure(template: string, name: string | null | undefined): string {
  return template.trim().replaceAll('{name}', name?.trim() || 'someone');
}

export function getCallSettings(signal?: AbortSignal): Promise<CallSettings> {
  return apiJson<CallSettings>('/calls/settings', { signal });
}

export function saveCallSettings(settings: Partial<CallSettings>): Promise<CallSettings> {
  return apiJson<CallSettings>('/calls/settings', {
    method: 'PUT',
    body: JSON.stringify(settings),
  });
}

export async function getReadiness(signal?: AbortSignal): Promise<ReadinessItem[]> {
  const body = await apiJson<ReadinessItem[] | { items?: ReadinessItem[] }>('/calls/readiness', {
    signal,
  });
  return Array.isArray(body) ? body : (body.items ?? []);
}

/** Same-origin SSE URL for a call's live events (proxied like every `/api` path). */
export function callEventsUrl(callId: string): string {
  return apiPath(`/calls/${id(callId)}/events`);
}

/** Parse one SSE payload into a typed event; unknown shapes are dropped. */
export function parseCallEvent(data: string, name?: string): CallEvent | null {
  let parsed: unknown;
  try {
    parsed = JSON.parse(data);
  } catch {
    return null;
  }
  if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) return null;
  const object = parsed as Record<string, unknown>;
  const type =
    typeof object.type === 'string' ? object.type : name && name !== 'message' ? name : '';
  switch (type) {
    case 'status':
      return typeof object.status === 'string' ? { ...object, type, status: object.status } : null;
    case 'transcript':
      if (
        (object.speaker !== 'agent' && object.speaker !== 'caller') ||
        typeof object.text !== 'string'
      )
        return null;
      return {
        type,
        speaker: object.speaker,
        text: object.text,
        final: object.final !== false,
        t: typeof object.t === 'number' ? object.t : 0,
      };
    case 'agent_state': {
      const state = object.state ?? object.agent_state;
      if (state !== 'listening' && state !== 'thinking' && state !== 'speaking') return null;
      return typeof object.takeover === 'boolean'
        ? { type, state, takeover: object.takeover }
        : { type, state };
    }
    case 'outcome':
      return {
        type,
        outcome: (object.outcome as CallOutcome | null) ?? null,
        summary: typeof object.summary === 'string' ? object.summary : null,
      };
    case 'ended': {
      const call =
        object.call && typeof object.call === 'object' && !Array.isArray(object.call)
          ? (object.call as CallRecord)
          : undefined;
      return {
        type,
        ...(call ? { call } : {}),
        status: typeof object.status === 'string' ? object.status : undefined,
        duration_s: typeof object.duration_s === 'number' ? object.duration_s : null,
        ended_at:
          typeof object.ended_at === 'string' || typeof object.ended_at === 'number'
            ? object.ended_at
            : undefined,
      };
    }
    default:
      return null;
  }
}

/** Milliseconds since the epoch for a wire timestamp (ISO string or epoch seconds). */
export function callTime(value: string | number | null | undefined): number | null {
  if (value == null || value === '') return null;
  if (typeof value === 'number') return value < 1e12 ? value * 1000 : value;
  const numeric = Number(value);
  if (Number.isFinite(numeric)) return callTime(numeric);
  const parsed = Date.parse(value);
  return Number.isNaN(parsed) ? null : parsed;
}
