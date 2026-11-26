import { isBackendBusy } from '@shared/utils/backendStage';

export const ACTIVE_STATUS_POLL_MS = 1_000;
/**
 * Floor for background status polls while the supervisor reports a live but
 * busy (`unresponsive`) backend (#2594, #2601, #2247). Its event loop is held
 * by a job, so every one-second poll from every widget just queues up behind
 * it — occupying the renderer's few sockets per host and, once the job ends,
 * landing on the backend as a burst. Status can wait a few seconds.
 */
export const BUSY_BACKEND_POLL_MS = 5_000;

// Fed by use-backend-status on every supervisor update. Kept here, rather than
// read back from that hook, so this module has no dependency on it.
let currentStage = 'ready';

export function noteBackendStage(stage: string): void {
  currentStage = stage;
}

/** `ms`, lengthened to the busy-backend floor while the backend is stalled. */
export function relaxWhenBackendBusy(ms: number, stage: string = currentStage): number {
  return isBackendBusy(stage) ? Math.max(ms, BUSY_BACKEND_POLL_MS) : ms;
}

export const IDLE_STATUS_POLL_MS = 30_000;
/**
 * Idle poll for status the `/ws/events` socket already invalidates (model status,
 * loaded models). While the socket is connected the poll is only a safety net.
 */
export const EVENT_BACKED_IDLE_POLL_MS = 60_000;

let eventsConnected = false;

/** Fed by RealtimeEventSync as the `/ws/events` socket opens and closes. */
export function noteEventsSocket(connected: boolean): void {
  eventsConnected = connected;
}

function idleEventBackedMs(): number {
  return eventsConnected ? EVENT_BACKED_IDLE_POLL_MS : IDLE_STATUS_POLL_MS;
}

export function modelStatusPollMs(activityCount: number, status?: string): number {
  return relaxWhenBackendBusy(
    activityCount > 0 || status === 'loading' ? ACTIVE_STATUS_POLL_MS : idleEventBackedMs(),
  );
}

export function batchStatusPollMs(jobCount: number): number {
  return relaxWhenBackendBusy(jobCount > 0 ? ACTIVE_STATUS_POLL_MS : IDLE_STATUS_POLL_MS);
}

export function loadedModelsPollMs(active: boolean): number {
  return relaxWhenBackendBusy(active ? ACTIVE_STATUS_POLL_MS : idleEventBackedMs());
}
