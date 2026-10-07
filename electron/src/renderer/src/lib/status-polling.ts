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
export const IDLE_COMPUTE_TARGET_POLL_MS = 15_000;

export function modelStatusPollMs(activityCount: number, status?: string): number {
  return relaxWhenBackendBusy(
    activityCount > 0 || status === 'loading' ? ACTIVE_STATUS_POLL_MS : IDLE_STATUS_POLL_MS,
  );
}

export function batchStatusPollMs(jobCount: number): number {
  return relaxWhenBackendBusy(jobCount > 0 ? ACTIVE_STATUS_POLL_MS : IDLE_STATUS_POLL_MS);
}

export function loadedModelsPollMs(active: boolean): number {
  return relaxWhenBackendBusy(active ? ACTIVE_STATUS_POLL_MS : IDLE_STATUS_POLL_MS);
}

export function computeTargetPollMs(activeTasks: number | undefined): number {
  return relaxWhenBackendBusy(activeTasks ? ACTIVE_STATUS_POLL_MS : IDLE_COMPUTE_TARGET_POLL_MS);
}
