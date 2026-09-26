/**
 * globalErrorHandlers — last-resort surfacing for uncaught failures.
 *
 * consoleBuffer already records `window.onerror` / `unhandledrejection`
 * into the Settings → Logs → Frontend ring; this adds the user-visible
 * half: a throttled error toast with a "Report this bug" action
 * (utils/errorToast.jsx) so async failures outside any ErrorBoundary or
 * wired call site still have a path to a GitHub issue.
 *
 * Throttled per message (one toast per 30s) and filtered against known
 * benign noise — a render-loop bug must not bury the user in toasts.
 */
import i18next from 'i18next';
import { toastErrorWithReport } from './errorToast';

const THROTTLE_MS = 30_000;
const lastShown = new Map();

// Browser/webview noise that is not actionable by the user and must never
// produce a report prompt.
const IGNORE_PATTERNS = [
  /ResizeObserver loop/i,
  /AbortError/i,
  /Loading chunk \d+ failed/i, // transient on dev-server restarts
  /Script error\.?$/i, // opaque cross-origin errors carry no info
];

// A browser extension injected into the page throws in the page's own error
// channel, so `window.onerror` cannot tell it apart from ours by message alone
// — and the message-based list above cannot help, because an extension's
// TypeError reads exactly like one of ours. #1901 is the result: a report filed
// against VoiceStudio whose stack is entirely
// `chrome-extension://…/executors/200.js`, with no frame of ours in it.
//
// `Script error.` above already covers the opaque cross-origin case. An
// extension's script is not opaque, so it arrives with a full stack and slips
// straight through to the "Report this bug" action.
const EXTENSION_URL = /\b(?:chrome|moz|safari-web|safari|ms-browser)-extension:\/\//i;

// Whether a stack line is a FRAME rather than the header. V8 writes
// "TypeError: <message>" first and then "    at fn (url:1:2)"; JSC and
// SpiderMonkey write "fn@url:1:2" with no header at all. Both frame shapes are
// recognised, and anything else is the header — which matters because a message
// can contain a URL of its own, in either direction: an extension error reading
// "Failed to fetch https://example.com" would otherwise report the message's
// URL as its origin and escape the filter, and one of OUR errors quoting a
// `chrome-extension://` URL would otherwise be suppressed as an extension's.
// The JSC alternative is anchored: a frame is `fn@url`, and a function name has
// no spaces, so the `@` must be reachable from the line start through
// non-whitespace only. Unanchored, a V8 HEADER whose message happens to read
// `... user@chrome-extension://...` matched as a JSC frame and its message URL
// was taken as the throw site -- the same false positive one layer down.
// JSC additionally labels three frame kinds with a SPACE in them —
// `global code@url`, `eval code@url` and `module code@url`. A bare `\S*`
// before the `@` therefore skips exactly the top-level frame that an injected
// extension script throws from, so originUrl() found no frame, returned '',
// and the extension's error still offered "Report this bug" — #1901 unfixed
// on WKWebView, which is the macOS desktop build and Safari. The three labels
// are enumerated rather than allowing spaces generally, because a general
// space would re-open the V8-header false positive the anchoring exists for.
const FRAME_LINE = /^\s*at\s|^\s*(?:(?:global|eval|module) code|[^\s@]*)@[a-z-]+:\/\//i;
const FRAME_URL = /[a-z-]+:\/\/[^\s)]+/i;

/** The URL the error came FROM, or '' when the origin cannot be established. */
function originUrl(error, filename) {
  // An ErrorEvent names the script directly, which beats parsing a stack.
  // Only `unhandledrejection` has to fall back to the stack.
  if (typeof filename === 'string' && filename) return filename;
  const stack = typeof error?.stack === 'string' ? error.stack : '';
  const frame = stack.split('\n').find((line) => FRAME_LINE.test(line));
  if (!frame) return '';
  // The FIRST frame only, and '' when it names no URL (a native or anonymous
  // throw site). Walking deeper to find one would attribute an error to a
  // frame that did not throw it — and since the only thing this decides is
  // whether to offer a report, an unknown origin has to mean "offer it".
  // Silencing one of our own bugs is worse than leaving noise in.
  const match = frame.match(FRAME_URL);
  return match ? match[0] : '';
}

function shouldShow(message, error, filename) {
  // Some browser streams (including WaveSurfer's BodyStreamBuffer) describe
  // normal cancellation without the word "AbortError" in the message. The
  // structured DOMException name is the reliable cancellation contract.
  if (error?.name === 'AbortError') return false;
  if (EXTENSION_URL.test(originUrl(error, filename))) return false;
  if (!message || IGNORE_PATTERNS.some((p) => p.test(message))) return false;
  const key = String(message).slice(0, 200);
  const now = Date.now();
  if ((lastShown.get(key) || 0) > now - THROTTLE_MS) return false;
  lastShown.set(key, now);
  return true;
}

function surface(message, error, filename) {
  if (!shouldShow(message, error, filename)) return;
  const err = error instanceof Error ? error : new Error(String(error ?? message));
  toastErrorWithReport(
    i18next.t('errors.unexpected', { message: String(message).slice(0, 140) }),
    err,
  );
}

let installed = false;

export function installGlobalErrorHandlers() {
  if (installed || typeof window === 'undefined') return;
  installed = true;
  window.addEventListener('error', (e) => {
    // `e.filename` is the script the throw came from — more reliable than
    // parsing a stack, and present even when the error object is not.
    surface(e?.error?.message || e.message, e.error, e?.filename);
  });
  window.addEventListener('unhandledrejection', (e) => {
    const r = e?.reason;
    surface(r?.message || String(r), r);
  });
}
