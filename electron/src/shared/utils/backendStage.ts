/**
 * backendStage — one place that decides what a lifecycle stage means for a
 * request, shared by the main process, the shared client and the renderer.
 *
 * `unresponsive` (#2430) is the stage that made this necessary. A live backend
 * running a long inference monopolizes Python's event loop for longer than the
 * health deadline, and the supervisor reports that as a distinct, non-terminal
 * stage. The process is still listening — the supervisor proves it before
 * publishing, by checking `exitCode`/`signalCode` — so a request issued then is
 * answered late rather than never.
 *
 * Every consumer previously spelled this "stage === 'ready'", which made the
 * busy backend behave like a dead one: the renderer threw before the request
 * was even issued and took React Query offline, while the shared client waited.
 * They must not disagree about whether a request can still succeed.
 */

/**
 * True when a backend in `stage` can still answer a request, so callers should
 * issue it and let it resolve rather than rejecting it up front.
 *
 * `ready` obviously; `unresponsive` because the backend is alive and merely
 * mid-job. Every other stage means the backend is either not listening yet
 * (setup, install, start, attach) or terminally gone (crash, port taken, failed
 * to start), so a request must not be sent at all.
 */
export function isBackendReachable(stage: string): boolean {
  return stage === 'ready' || stage === 'unresponsive';
}

/**
 * True when a backend in `stage` is known to be a live process that is not
 * answering right now, and recovers on its own once its current job finishes.
 */
export function isBackendBusy(stage: string): boolean {
  return stage === 'unresponsive';
}
