// Ring buffer for frontend console messages. Settings "Logs > Frontend" tab
// reads from here. Installed once in main.jsx so every console.* is captured
// without breaking the DevTools output.

const MAX = 500;
const buf = [];

// Tauri's own internal IPC fallback (#975): on some Windows configurations
// the custom-protocol IPC probe fails once at startup and Tauri logs this
// exact console.warn before silently — and successfully — falling back to
// postMessage + WebSocket. It's benign and fires at most once per launch,
// but as a captured console.warn it spuriously flips the Logs footer's
// Frontend pill to "1 warning" on every affected launch. Filtered at the
// capture source (not the display layer) so it never enters the ring
// buffer or a copied diagnostic dump either.
const BENIGN_WARNING_PREFIXES = ['IPC custom protocol failed'];

function push(level, args) {
  if (level === 'warn' && typeof args[0] === 'string') {
    if (BENIGN_WARNING_PREFIXES.some((p) => args[0].startsWith(p))) return;
  }
  const msg = Array.from(args)
    .map((a) => {
      if (a instanceof Error) return `${a.name}: ${a.message}${a.stack ? '\n' + a.stack : ''}`;
      if (typeof a === 'object') {
        try {
          return JSON.stringify(a);
        } catch {
          return String(a);
        }
      }
      return String(a);
    })
    .join(' ');
  buf.push({ t: Date.now(), level, msg });
  if (buf.length > MAX) buf.shift();
}

let installed = false;
export function installConsoleCapture() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  ['log', 'info', 'warn', 'error', 'debug'].forEach((level) => {
    const orig = console[level].bind(console);
    console[level] = (...args) => {
      try {
        push(level, args);
      } catch {}
      orig(...args);
    };
  });
  window.addEventListener('error', (e) => {
    push('error', [
      `[uncaught] ${e.message}`,
      e.filename ? `at ${e.filename}:${e.lineno}:${e.colno}` : '',
    ]);
  });
  window.addEventListener('unhandledrejection', (e) => {
    push('error', ['[unhandledrejection]', e.reason]);
  });
}

export function getFrontendLogs() {
  return buf.slice();
}

export function clearFrontendLogs() {
  buf.length = 0;
}
