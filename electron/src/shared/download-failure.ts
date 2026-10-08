/**
 * Carry a failed backend download's HTTP response across the Electron IPC
 * boundary.
 *
 * `ipcMain.handle` rejections reach the renderer as a bare message string
 * ("Error invoking remote method 'files:saveAudio': Error: …"), so a native
 * save that threw `HTTP 409` lost the backend's `detail` — the code the
 * renderer localizes and the reason the user needs (#2616). Main encodes the
 * status and body into the message after a readable first line; the renderer
 * decodes it and builds the same `ApiError` a direct `fetch` would.
 */

const MARKER = '\nVS_DOWNLOAD_FAILURE:';
/** Bodies are error payloads; bound what crosses IPC. */
export const MAX_FAILURE_BODY_CHARS = 16_384;

export interface DownloadFailure {
  status: number;
  statusText: string;
  body: string;
}

export function encodeDownloadFailure(failure: DownloadFailure): string {
  const body = failure.body.slice(0, MAX_FAILURE_BODY_CHARS);
  return (
    `Could not download the file (HTTP ${failure.status})` +
    MARKER +
    JSON.stringify({ status: failure.status, statusText: failure.statusText, body })
  );
}

export function decodeDownloadFailure(error: unknown): DownloadFailure | null {
  const message = error instanceof Error ? error.message : typeof error === 'string' ? error : '';
  const at = message.indexOf(MARKER);
  if (at < 0) return null;
  try {
    const parsed: unknown = JSON.parse(message.slice(at + MARKER.length));
    if (!parsed || typeof parsed !== 'object') return null;
    const { status, statusText, body } = parsed as Record<string, unknown>;
    if (typeof status !== 'number' || !Number.isInteger(status)) return null;
    return {
      status,
      statusText: typeof statusText === 'string' ? statusText : '',
      body: typeof body === 'string' ? body : '',
    };
  } catch {
    return null;
  }
}

/** Drop Electron's "Error invoking remote method '<channel>': Error: " wrapper. */
export function stripIpcErrorPrefix(message: string): string {
  return message.replace(/^Error invoking remote method '[^']*':\s*(?:[A-Za-z]*Error:\s*)?/, '');
}
