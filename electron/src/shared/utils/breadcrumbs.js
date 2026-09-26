/**
 * breadcrumbs — local-only ring of recent UI actions for bug reports.
 *
 * The cheapest repro-step generator there is: when a report goes out, the
 * last ~20 action names ride along as a "Recent actions" section so the
 * maintainer sees "switched engine → started dub → export failed" instead
 * of guessing.
 *
 * Privacy rules (stricter than the scrubber):
 *   - action NAMES only — never text content, file names, paths, or URLs
 *   - callers pass fixed strings like 'generate:start' or 'view:settings';
 *     anything dynamic must be from a closed set (mode names, engine ids)
 * Lives in memory only — never persisted, never sent anywhere except inside
 * a report body the user reviews on github.com.
 */

const MAX = 20;
const ring = [];

export function addBreadcrumb(action) {
  if (!action) return;
  const now = Date.now();
  const last = ring[ring.length - 1];
  // Collapse immediate repeats (a re-render storm must not flush the ring).
  if (last && last.action === action && now - last.t < 2000) {
    last.t = now;
    return;
  }
  ring.push({ t: now, action: String(action).slice(0, 60) });
  if (ring.length > MAX) ring.shift();
}

export function getBreadcrumbs() {
  return ring.slice();
}

/** "12:03:05 view:dub" lines, oldest first — ready for the report body. */
export function formatBreadcrumbs() {
  return ring.map((b) => `${new Date(b.t).toLocaleTimeString('en-GB')} ${b.action}`).join('\n');
}

export function clearBreadcrumbs() {
  ring.length = 0;
}
