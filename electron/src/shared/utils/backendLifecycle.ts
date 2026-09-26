/**
 * backendLifecycle — frontend bridge to the desktop shell's backend lifecycle
 * (src-tauri/src/bootstrap.rs).
 *
 * The shell always knows whether the backend process is starting, ready,
 * being auto-restarted by the supervisor (#567), or terminally failed — but
 * until this module, api/client.ts guessed: it retried a transport failure
 * for ~2.9 s and then threw "Can't reach the local VoiceStudio backend", while
 * a real backend start/restart takes 10–20+ s (venv spawn + torch import).
 * Every request that landed in that window dead-ended with the scary toast,
 * which is why the error "kept coming up" on every restart/cold-start race.
 *
 * `backendLifecycleStage()` asks the shell (`bootstrap_status`) so the client
 * can keep waiting exactly as long as a start/restart is actually in
 * progress, and give up immediately when the shell says failed.
 *
 * Outside the Tauri shell (browser dev, Docker, LAN share) there is no shell
 * to ask — the stage is 'unknown' and callers keep today's short-retry
 * behavior.
 *
 * #1177: the shell's `BootstrapStage::Failed { message }` carries the WHOLE
 * diagnosis — exit code plus a ~30-line stderr tail, or the precise reason
 * `ensure_venv_ready` refused (Intel Mac, a failed `uv sync`, a blocked
 * GitHub). This module used to return only the stage tag and throw that
 * message away, so every backend-start failure collapsed into apiFetch's
 * evidence-free "Can't reach the local VoiceStudio backend" — the shell knew
 * exactly what went wrong and the frontend structurally could not read it.
 * The stage probe now returns `{ stage, message }` and callers surface it.
 */

export type BackendLifecycleStage = 'ready' | 'starting' | 'failed' | 'unknown';

/** The shell's lifecycle answer: the coarse stage plus, for `failed`, the
 * shell's full diagnosis (exit code + stderr tail, or the venv-bootstrap
 * reason). `message` is null for every non-failed stage. */
export interface BackendLifecycle {
  stage: BackendLifecycleStage;
  message: string | null;
}

/** Map the shell's BootstrapStage tag to the coarse lifecycle answer the
 * transport layer needs. Pure + exported for unit tests. */
export function classifyBootstrapStage(stage: string | null | undefined): BackendLifecycleStage {
  if (!stage) return 'unknown';
  if (stage === 'ready') return 'ready';
  if (stage === 'failed') return 'failed';
  // checking / awaiting_setup / downloading_uv / creating_venv /
  // installing_deps / starting_backend — the backend is legitimately not
  // listening yet, and the shell is actively working on it.
  return 'starting';
}

/** Normalize the shell's `bootstrap_status` payload into a BackendLifecycle.
 * Only a `failed` stage keeps a message — a stray message on any other stage
 * would be stale evidence attached to a healthy backend. Pure + exported for
 * unit tests. */
export function _toLifecycle(res: { stage?: string; message?: unknown } | null): BackendLifecycle {
  const stage = classifyBootstrapStage(res?.stage);
  if (stage !== 'failed') return { stage, message: null };
  const message = typeof res?.message === 'string' ? res.message.trim() : '';
  return { stage, message: message || null };
}

/** The shell's current backend lifecycle, or `{ stage: 'unknown' }` outside
 * Tauri / on IPC failure. Never throws.
 *
 * When the shell reports `failed` without a message — the stage was set by a
 * path that had no diagnosis, or a later transition already moved past the
 * Failed that did — fall back to the shell's RETAINED last failure
 * (`last_bootstrap_failure`, #1177). A precise diagnosis outranks silence,
 * and the retained copy is the only place it still exists. */
export async function backendLifecycleStage(): Promise<BackendLifecycle> {
  if (typeof window === 'undefined' || !window.voicestudio) {
    return { stage: 'unknown', message: null };
  }
  try {
    const status = await window.voicestudio.backend.getStatus();
    if (status.stage === 'ready') return { stage: 'ready', message: null };
    if (['failed', 'crashed', 'port_in_use'].includes(status.stage)) {
      const detail = status.message?.trim() || status.logTail.at(-1)?.trim() || null;
      return { stage: 'failed', message: detail };
    }
    if (['setup_required', 'installing', 'attaching', 'starting'].includes(status.stage)) {
      return { stage: 'starting', message: null };
    }
    return { stage: 'unknown', message: null };
  } catch {
    return { stage: 'unknown', message: null };
  }
}
