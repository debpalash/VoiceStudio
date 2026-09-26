/**
 * Web-platform gap fills for the OLDEST WebView we claim to support.
 *
 * `tauri.conf.json` (and the `tauri.macos.conf.json` overlay that actually
 * ships) declare `minimumSystemVersion: "13.3"`, which is Safari/WKWebView
 * **16.4** — the floor the frontend stack has really required all along
 * (#1268). Anything newer than that is not present at runtime on a supported
 * machine, and because our entry chunk touches these during the first React
 * render, a single missing method is not a degraded feature: it throws
 * mid-render, the tree unmounts, and the user gets a dead window with no
 * backend ever started (#1245).
 *
 * This module must be imported for side effects as the FIRST thing in
 * `main.jsx`, before any app chunk loads. `test/webCompat.test.js` keeps the
 * list honest: it fails CI if app code reaches for a post-16.4 API that is not
 * filled in here.
 *
 * The fills below all predate 16.4, so on macOS they are now dead weight — but
 * they are feature-detected no-ops, and macOS is no longer the only floor that
 * matters: Linux ships whatever WebKitGTK the host distro has (≥ 2.44 on Ubuntu
 * 24.04+, older on LTS derivatives), and that is the version we cannot pin.
 * Windows is evergreen WebView2 and clears everything. Keep them.
 */

/**
 * `AbortSignal.timeout(ms)` — Safari 16.0. Used by the backend health poll on
 * the very first render, so its absence took the whole app down on Monterey.
 */
export function installAbortSignalTimeout() {
  if (typeof AbortSignal === 'undefined' || typeof AbortController === 'undefined') return;
  if (typeof AbortSignal.timeout === 'function') return;

  AbortSignal.timeout = function timeout(ms) {
    const controller = new AbortController();
    setTimeout(() => {
      // The spec aborts with a DOMException named TimeoutError, which is how
      // callers tell "we gave up" apart from "the user cancelled". Pass it:
      // `abort(reason)` has been supported since Safari 15.4, so it IS
      // honoured on our 15.6 floor. Do not "simplify" this to a bare
      // `controller.abort()` — that would silently turn every TimeoutError
      // into an AbortError and break exactly the distinction it exists for.
      let reason;
      try {
        reason = new DOMException('signal timed out', 'TimeoutError');
      } catch {
        reason = new Error('signal timed out');
        reason.name = 'TimeoutError';
      }
      controller.abort(reason);
    }, ms);
    return controller.signal;
  };
}

export function installWebCompat() {
  installAbortSignalTimeout();
}

installWebCompat();
