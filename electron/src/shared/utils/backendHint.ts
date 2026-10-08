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

export type BackendHintKey =
  | 'backend.hint_spawn_blocked'
  | 'backend.hint_slow_start'
  | 'backend.hint_synced_folder';

/** Cloud-sync clients whose placeholders / locks break a Python environment. */
const SYNCED_FOLDER =
  /(^|[\\/])(onedrive(\s+-\s+[^\\/]+)?|dropbox|icloud\s*drive|google\s*drive|nextcloud)([\\/]|$)/i;

/** True when the text names a path inside a cloud-synced folder. */
export function inSyncedFolder(text: string): boolean {
  return SYNCED_FOLDER.test(text);
}

/**
 * The hints that apply to a failed-backend message, most specific first.
 * Never throws; an empty list means "show the message alone".
 */
export function backendFailureHints(message: string | undefined): BackendHintKey[] {
  if (!message) return [];
  const hints: BackendHintKey[] = [];
  // The OS refused to run the program: libuv reports a blocked executable on
  // Windows as the bare `spawn UNKNOWN`, and EPERM/EACCES elsewhere.
  if (/\bCould not start\b.*\b(spawn UNKNOWN|UNKNOWN|EPERM|EACCES)\b/s.test(message)) {
    hints.push('backend.hint_spawn_blocked');
  }
  // The readiness wait ran out on a launch that was not obviously dead.
  if (/did not answer on port \d+ within \d+ s/.test(message)) {
    hints.push('backend.hint_slow_start');
  }
  if (inSyncedFolder(message)) hints.push('backend.hint_synced_folder');
  return hints;
}
