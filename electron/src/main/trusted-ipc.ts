/**
 * One gate for every IPC handler.
 *
 * Electron IPC is addressed to a webContents, not to a frame: a handler
 * registered with `ipcMain.handle` answers any renderer that can reach the main
 * process, including a guest page loaded in a view. Handlers that change host
 * state (window chrome, shortcuts, watch folders, updater, account) therefore
 * have to check that the request comes from the app's own main frame before they
 * run. Doing that by hand at each call site is what `fix/ipc` had to retrofit:
 * some handlers were checked, most were not, and nothing stopped the next one
 * from being written without a check.
 *
 * Every registration goes through `handleTrusted` or `onTrusted`; both assert the
 * sender against the window it belongs to and its own trusted URL, so a new
 * channel cannot ship unchecked. `ipc-registry.test.ts` enforces that no module
 * registers a channel directly.
 */
import { BrowserWindow, ipcMain } from 'electron';

import { isTrustedRenderer } from './trusted-renderer';

type InvokeArgs = Parameters<Parameters<typeof ipcMain.handle>[1]>;
type IpcEvent = InvokeArgs[0];
type OnArgs = Parameters<Parameters<typeof ipcMain.on>[1]>;
type IpcOnEvent = OnArgs[0];

/** The window that owns `event`, or null when it is not a window's webContents. */
function ownerWindow(event: IpcEvent | IpcOnEvent): BrowserWindow | null {
  return BrowserWindow.fromWebContents(event.sender);
}

/**
 * Throw unless `event` came from `owner`'s own main frame, on a trusted URL.
 *
 * `owner` is passed in when the caller already resolved the window it expects
 * (a modal it opened, the window whose state it is reading); the assertion also
 * proves that window exists.
 */
export function assertTrustedMainFrame(
  event: IpcEvent,
  owner: BrowserWindow | null,
  message = 'Untrusted main-frame request',
): asserts owner is BrowserWindow {
  if (
    !owner ||
    event.sender !== owner.webContents ||
    event.senderFrame !== owner.webContents.mainFrame ||
    !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
  ) {
    throw new Error(message);
  }
}

/**
 * Throw unless `event` came from its own window's main frame on a trusted URL.
 *
 * Used where the channel is shared by the main window and the app's utility
 * windows (the capture overlay, a tray-opened window): all of them load the app
 * origin, so the check is about the frame, not about which window it is.
 */
export function assertTrustedTopLevel(
  event: IpcEvent | IpcOnEvent,
  message = 'Untrusted top-level request',
): void {
  const owner = ownerWindow(event);
  if (
    !owner ||
    event.sender !== owner.webContents ||
    event.senderFrame !== owner.webContents.mainFrame ||
    !isTrustedRenderer(event.senderFrame.url, process.env.ELECTRON_RENDERER_URL)
  ) {
    throw new Error(message);
  }
}

/**
 * The owner when `event` is trusted, `null` when it is not.
 *
 * `ipcMain.on` listeners have no reply channel to surface an error on, so a
 * trusted-frame assertion that throws would become an unhandled main-process
 * exception. This variant drops the event instead, which is the right answer for
 * a window-control message from an untrusted frame.
 */
export function trustedOwner(
  event: IpcEvent | IpcOnEvent,
  owner: BrowserWindow | null,
): BrowserWindow | null {
  try {
    assertTrustedMainFrame(event as IpcEvent, owner);
    return owner;
  } catch {
    return null;
  }
}

/** `ipcMain.handle` behind the trust gate. */
export function handleTrusted<Args extends unknown[], R>(
  channel: string,
  handler: (event: IpcEvent, ...args: Args) => R,
): void {
  ipcMain.handle(channel, (event, ...args) => {
    assertTrustedTopLevel(event);
    return handler(event, ...(args as Args));
  });
}

/** `ipcMain.on` behind the trust gate. */
export function onTrusted<Args extends unknown[]>(
  channel: string,
  handler: (event: IpcOnEvent, ...args: Args) => void,
): void {
  ipcMain.on(channel, (event, ...args) => {
    try {
      assertTrustedTopLevel(event);
    } catch {
      // A one-way channel has no reply to carry the refusal, so an unauthorized
      // event is dropped rather than thrown as an unhandled main-process error.
      return;
    }
    handler(event, ...(args as Args));
  });
}