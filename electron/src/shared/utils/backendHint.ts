/**
 * backendHint - turns a raw backend failure message into the localized,
 * actionable advice that goes with it.
 *
 * `BackendStatus.message` is raw technical text assembled in the main process
 * from OS errors and process output, so it stays English and verbatim (it is
 * also what a bug report quotes). The advice a user can act on, though, must
 * not: "spawn UNKNOWN" means nothing to most people, and the three recurring
 * Windows startup reports - a launch the OS refused (#2440), a backend still
 * booting on a slow no-GPU host when the wait ran out (#2445), and a checkout
 * inside a synced folder (#2465) - each have one fix that the raw text never
 * names. This maps the stable shapes of those messages to i18n keys; anything
 * it does not recognise yields no hint rather than a wrong one.
 */

import {
  classifyInstallFailure,
  failureGuidance,
  inSyncedFolder,
  type BackendCause,
  type BackendHintKey,
} from './installFailure';

export { inSyncedFolder };
export type { BackendCause, BackendHintKey };

/**
 * The hints that apply to a failed-backend message, most specific first.
 * Never throws; an empty list means "show the message alone".
 */
export function backendFailureHints(message: string | undefined): BackendHintKey[] {
  if (!message) return [];
  return failureGuidance(classifyInstallFailure({ message }), { message }).hints;
}

/** The single primary action that fixes (or best diagnoses) each cause. */
export type BackendCauseAction = 'retry' | 'choose_folder' | 'copy_diagnostics';

export const BACKEND_CAUSE_ACTION: Record<BackendCause, BackendCauseAction> = {
  smart_app_control: 'retry',
  app_control_policy: 'retry',
  antivirus: 'retry',
  spawn_blocked: 'retry',
  gatekeeper: 'retry',
  noexec: 'choose_folder',
  download: 'retry',
  illegal_instruction: 'copy_diagnostics',
  native_crash: 'copy_diagnostics',
  missing_env: 'choose_folder',
  permission: 'choose_folder',
  synced_folder: 'choose_folder',
  slow_start: 'retry',
};

/**
 * The primary action for a cause on this install. Moving the environment only
 * exists for the app-managed runtime of an installed app: a source checkout's
 * `.venv` lives in the repository, the folder picker refuses it, and a
 * "Choose another folder" button there did nothing (#3480). Retry re-checks the
 * environment instead, which is what a source user needs after running setup.
 */
export function backendCauseAction(
  cause: BackendCause | null,
  { managedRuntime }: { managedRuntime: boolean },
): BackendCauseAction {
  if (!cause) return 'retry';
  const action = BACKEND_CAUSE_ACTION[cause];
  return action === 'choose_folder' && !managedRuntime ? 'retry' : action;
}

/**
 * Collapses a failed-backend message into ONE named cause, most specific
 * first, or null when nothing is recognised (the raw message is then shown
 * alone with Retry).
 */
export function backendFailureCause(message: string | undefined): BackendCause | null {
  if (!message) return null;
  return failureGuidance(classifyInstallFailure({ message }), { message }).cause;
}
