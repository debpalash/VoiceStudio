import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';

const PREFS_FILE = 'window-prefs.json';

/** What the main window's close button (X, Alt+F4, Cmd+W) should do. */
export type WindowCloseAction =
  /** Let the close proceed: the app is already quitting. */
  | 'allow'
  /** Flush renderer state, then hide the window; the app and its jobs keep running. */
  | 'hide'
  /** Flush renderer state, then destroy the window; the app keeps running. */
  | 'destroy'
  /** Quit through the orderly path (stops the backend and running jobs). */
  | 'quit';

export interface WindowCloseContext {
  platform: NodeJS.Platform;
  /** Started from a terminal without a window; its idle timer decides shutdown. */
  headlessOwner: boolean;
  /** Teardown finished and the app is exiting. */
  quitting: boolean;
  /** An orderly quit or an OS session end is already under way. */
  quitRequested: boolean;
  /** Settings → General → keep running in the system tray. */
  closeToTray: boolean;
  /** The tray icon exists, so the user has a way back to a hidden window. */
  trayAvailable: boolean;
}

/**
 * Closing the window quits the app on Windows and Linux and keeps it running on
 * macOS. The opt-in tray setting makes every platform keep running, hiding the
 * window rather than destroying it so in-progress renderer state survives.
 * Explicit quits (tray Quit, Cmd+Q, update install, session end) never reach the
 * hide branch: they set `quitting` or `quitRequested` first.
 */
export function decideWindowClose(context: WindowCloseContext): WindowCloseAction {
  if (context.quitting) return 'allow';
  if (context.quitRequested) return 'quit';
  // A headless owner has no tray-managed window: hiding it would keep the idle
  // timer from ever seeing the window go away.
  if (context.headlessOwner) return 'destroy';
  if (context.closeToTray && context.trayAvailable) return 'hide';
  return context.platform === 'darwin' ? 'destroy' : 'quit';
}

/** Off unless the user turned it on. */
export function readCloseToTray(userData: string): boolean {
  try {
    const saved: unknown = JSON.parse(readFileSync(join(userData, PREFS_FILE), 'utf8'));
    return (
      !!saved && typeof saved === 'object' && (saved as Record<string, unknown>).closeToTray === true
    );
  } catch {
    return false;
  }
}

/** Throws when the choice cannot be written, so the caller never confirms an unsaved value. */
export function saveCloseToTray(userData: string, enabled: boolean): void {
  writeFileSync(join(userData, PREFS_FILE), JSON.stringify({ closeToTray: enabled }));
}

/**
 * Whether the main window is in use, and which close is still pending.
 *
 * A window hidden to the tray is live but not in use: `voicestudio stop` may
 * end the app, and the discovery record says so. A minimized window is still in
 * use. Any show cancels a close that is still flushing, so reopening from the
 * tray or a second launch always wins over the late continuation of an earlier
 * close.
 */
export class WindowUse {
  private hidden = false;
  private epoch = 0;

  get hiddenToTray(): boolean {
    return this.hidden;
  }

  /** The window was hidden to the tray. */
  hide(): void {
    this.hidden = true;
  }

  /** The window was shown (or is gone): in use again, and pending closes are void. */
  show(): void {
    this.hidden = false;
    this.epoch++;
  }

  /** Start a close; the returned check is false once the window was shown after it. */
  beginClose(): () => boolean {
    const epoch = this.epoch;
    return () => this.epoch === epoch;
  }

  /** The `window` field of the discovery record. */
  inUse(live: boolean): boolean {
    return live && !this.hidden;
  }
}

/** What `voicestudio stop` does with the app. */
export function decideTerminalStop(context: { windowLive: boolean; hiddenToTray: boolean }): {
  /** Begin the orderly quit. False while the user is working in a window. */
  quit: boolean;
  /** Flush renderer state first: a live (hidden) window may have queued writes. */
  flush: boolean;
} {
  return {
    quit: !context.windowLive || context.hiddenToTray,
    flush: context.windowLive,
  };
}

/**
 * Whether a Dock click (macOS `activate`) must bring the window back: it is gone,
 * or hidden to the tray where a missing tray icon would leave no other way in.
 */
export function needsRestore(window: { live: boolean; visible: boolean }): boolean {
  return !window.live || !window.visible;
}
