/**
 * Copy text to the clipboard, resilient to non-secure (plain-HTTP) contexts.
 *
 * The async Clipboard API (`navigator.clipboard`) is only available in secure
 * contexts (https / localhost). On a LAN-shared instance opened over plain
 * `http://<ip>:<port>`, `navigator.clipboard` is `undefined`, so a bare
 * `navigator.clipboard.writeText(...)` throws
 * "Cannot read properties of undefined (reading 'writeText')". Fall back to a
 * hidden-textarea + `execCommand('copy')`, which works in non-secure contexts.
 *
 * @param {string} text
 * @returns {Promise<boolean>} whether the copy succeeded
 */
export async function copyText(text) {
  try {
    if (typeof navigator !== 'undefined' && navigator.clipboard && navigator.clipboard.writeText) {
      await navigator.clipboard.writeText(text);
      return true;
    }
  } catch {
    /* secure-context call failed (permissions, focus) — fall through */
  }
  try {
    const ta = document.createElement('textarea');
    ta.value = text;
    ta.setAttribute('readonly', '');
    ta.style.position = 'fixed';
    ta.style.top = '-1000px';
    ta.style.opacity = '0';
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand('copy');
    document.body.removeChild(ta);
    return ok;
  } catch {
    return false;
  }
}
