import { afterEach, expect, it, vi } from 'vitest';
import { backendLifecycleStage } from '../utils/backendLifecycle';

afterEach(() => {
  vi.unstubAllGlobals();
});

function stubStage(stage: string, message?: string): void {
  vi.stubGlobal('voicestudio', {
    backend: { getStatus: async () => ({ stage, message, logTail: [] }) },
  });
}

// #2430 — a live-but-busy backend is mid-job, not down.
//
// The shell's supervisor had already proved the process was alive (it checks
// exitCode/signalCode before reporting), yet `unresponsive` fell through to
// `unknown`, and `unknown` is the branch that tells apiFetch to give up at once.
// Every request issued during a long generation therefore dead-ended with
// "Can't reach the local VoiceStudio backend" while the backend was faithfully
// working through the job. `starting` is the honest answer: still in progress,
// keep waiting, and let it land when the event loop frees up.
describe('backendLifecycleStage — a busy backend is still in progress (#2430)', () => {
  it('holds requests open instead of dead-ending them', async () => {
    stubStage('unresponsive');
    expect(await backendLifecycleStage()).toEqual({ stage: 'starting', message: null });
  });

  it('never carries a failure message on it', async () => {
    stubStage('unresponsive', 'Backend is running but busy on port 3900.');
    expect(await backendLifecycleStage()).toEqual({ stage: 'starting', message: null });
  });

  it('still gives up immediately for a real terminal failure', async () => {
    stubStage('failed', 'The Python environment is missing or incomplete.');
    expect(await backendLifecycleStage()).toEqual({
      stage: 'failed',
      message: 'The Python environment is missing or incomplete.',
    });
  });

  it('still reports unknown with no shell to ask', async () => {
    vi.stubGlobal('voicestudio', undefined);
    expect(await backendLifecycleStage()).toEqual({ stage: 'unknown', message: null });
  });
});
