/**
 * openExternal — open a URL in the user's default browser.
 *
 * Electron delegates to its validated main-process bridge. Browser builds
 * fall back to a normal window.open call.
 */

/**
 * Open an external URL in the system default browser.
 * @param {string} url — the URL to open
 */
export async function openExternal(url: string) {
  if (window.voicestudio) {
    try {
      await window.voicestudio.files.openExternal(url);
      return;
    } catch (err) {
      console.warn('[openExternal] Electron opener failed, falling back:', err);
    }
  }
  // Fallback for browser dev mode
  window.open(url, '_blank', 'noopener,noreferrer');
}
