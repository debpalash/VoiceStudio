// Shared browser-download helpers.
//
// Used by every "save this file" path that runs outside the Tauri desktop
// shell (browser dev mode + the Docker web-server build). In Tauri we use the
// native save dialog instead; calling that dialog when no Tauri runtime is
// present throws "Cannot read properties of undefined (reading 'invoke')"
// (issue #256), so callers must guard on isTauri and route here otherwise.
import i18next from 'i18next';
import { apiFetch } from '../api/client';

/**
 * Parse a download filename out of a Content-Disposition header.
 * Returns null when the header is absent or unparseable.
 */
export function parseFilenameFromContentDisposition(header) {
  if (!header) return null;
  const utf8 = header.match(/filename\*=(?:UTF-8|utf-8)''([^;]+)/i);
  if (utf8) {
    try {
      return decodeURIComponent(utf8[1].trim().replace(/^"|"$/g, ''));
    } catch {
      /* fall through to the plain match */
    }
  }
  const plain = header.match(/filename="?([^";]+)"?/i);
  return plain ? plain[1].trim() : null;
}

/**
 * Fetch `url` and trigger a standard browser blob download via a temporary
 * <a download> element. Prefers the server-provided Content-Disposition
 * filename, falling back to `fallbackName`. Returns the filename used.
 *
 * `deps` lets tests inject fetch/document/url without a real DOM + network.
 * The default fetch is `apiFetch`, so backend downloads carry the LAN-share
 * PIN / remote API-key headers (a raw fetch would 401 under remote-backend).
 */
export async function browserDownload(url, fallbackName, deps = {}) {
  const _fetch = deps.fetch ?? apiFetch;
  const doc = deps.document ?? globalThis.document;
  const urlApi = deps.url ?? globalThis.URL;

  const response = await _fetch(url);
  if (!response.ok) {
    const body =
      typeof response.json === 'function' ? await response.json().catch(() => null) : null;
    if (body?.detail?.code === 'dub_background_unavailable')
      throw new Error(i18next.t('dubIntegrity.backgroundUnavailable'));
    throw new Error('Download failed');
  }

  const serverName = parseFilenameFromContentDisposition(
    response.headers?.get?.('content-disposition'),
  );
  const finalName = serverName || fallbackName || 'download';

  const blob = await response.blob();
  const localUrl = urlApi.createObjectURL(blob);
  const a = doc.createElement('a');
  a.href = localUrl;
  a.download = finalName;
  doc.body.appendChild(a);
  a.click();
  doc.body.removeChild(a);
  urlApi.revokeObjectURL(localUrl);
  return finalName;
}
