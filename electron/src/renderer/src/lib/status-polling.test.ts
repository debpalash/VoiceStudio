import { describe, expect, it } from 'vitest';
import {
  ACTIVE_STATUS_POLL_MS,
  BUSY_BACKEND_POLL_MS,
  IDLE_COMPUTE_TARGET_POLL_MS,
  IDLE_STATUS_POLL_MS,
  batchStatusPollMs,
  computeTargetPollMs,
  loadedModelsPollMs,
  modelStatusPollMs,
  noteBackendStage,
  relaxWhenBackendBusy,
} from './status-polling';

describe('runtime status polling', () => {
  it('keeps active work responsive and backs off idle fallback requests', () => {
    expect(modelStatusPollMs(1, 'ready')).toBe(ACTIVE_STATUS_POLL_MS);
    expect(modelStatusPollMs(0, 'loading')).toBe(ACTIVE_STATUS_POLL_MS);
    expect(batchStatusPollMs(1)).toBe(ACTIVE_STATUS_POLL_MS);
    expect(loadedModelsPollMs(true)).toBe(ACTIVE_STATUS_POLL_MS);
    expect(computeTargetPollMs(1)).toBe(ACTIVE_STATUS_POLL_MS);

    expect(modelStatusPollMs(0, 'ready')).toBe(IDLE_STATUS_POLL_MS);
    expect(batchStatusPollMs(0)).toBe(IDLE_STATUS_POLL_MS);
    expect(loadedModelsPollMs(false)).toBe(IDLE_STATUS_POLL_MS);
    expect(computeTargetPollMs(0)).toBe(IDLE_COMPUTE_TARGET_POLL_MS);
  });

  it('floors every status poll while the backend is alive but busy (#2594)', () => {
    // Nothing is tracked for a stage the supervisor reports as ready.
    expect(relaxWhenBackendBusy(ACTIVE_STATUS_POLL_MS, 'ready')).toBe(ACTIVE_STATUS_POLL_MS);
    // A held event loop answers every poll late; one-second polls from several
    // widgets only queue up behind the job.
    expect(relaxWhenBackendBusy(ACTIVE_STATUS_POLL_MS, 'unresponsive')).toBe(BUSY_BACKEND_POLL_MS);
    // Slower polls are never sped up.
    expect(relaxWhenBackendBusy(IDLE_STATUS_POLL_MS, 'unresponsive')).toBe(IDLE_STATUS_POLL_MS);
  });

  it('applies the busy floor to the shared poll helpers', () => {
    noteBackendStage('unresponsive');
    try {
      expect(modelStatusPollMs(1, 'ready')).toBe(BUSY_BACKEND_POLL_MS);
      expect(batchStatusPollMs(3)).toBe(BUSY_BACKEND_POLL_MS);
      expect(loadedModelsPollMs(true)).toBe(BUSY_BACKEND_POLL_MS);
      expect(computeTargetPollMs(2)).toBe(BUSY_BACKEND_POLL_MS);
    } finally {
      noteBackendStage('ready');
    }
    expect(modelStatusPollMs(1, 'ready')).toBe(ACTIVE_STATUS_POLL_MS);
  });
});
