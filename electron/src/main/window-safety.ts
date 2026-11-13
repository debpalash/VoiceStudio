import type { BrowserWindow } from 'electron';

function isDestroyedFailure(error: unknown): boolean {
  return /object has been destroyed|webcontents was destroyed/i.test(
    error instanceof Error ? error.message : String(error),
  );
}

export function isLiveWindow(window: BrowserWindow | null | undefined): window is BrowserWindow {
  if (!window) return false;
  try {
    return !window.isDestroyed() && !window.webContents.isDestroyed();
  } catch (error) {
    if (isDestroyedFailure(error)) return false;
    throw error;
  }
}

/** Restore and focus a live window without racing its native teardown. */
export function activateLiveWindow(window: BrowserWindow | null | undefined): boolean {
  if (!isLiveWindow(window)) return false;
  try {
    if (window.isMinimized()) window.restore();
    window.show();
    window.focus();
    return true;
  } catch (error) {
    if (isDestroyedFailure(error)) return false;
    throw error;
  }
}

/** Deliver asynchronous status only while the renderer still exists. */
export function sendToLiveWindow(
  window: BrowserWindow | null | undefined,
  channel: string,
  ...args: unknown[]
): boolean {
  if (!isLiveWindow(window)) return false;
  try {
    window.webContents.send(channel, ...args);
    return true;
  } catch (error) {
    // webContents can disappear between the guards and send(). Consume only
    // that normal close/reload race; keep unrelated IPC faults visible.
    if (
      !isLiveWindow(window) || isDestroyedFailure(error)
    )
      return false;
    throw error;
  }
}
