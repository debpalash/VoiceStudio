/**
 * Recognise failures from developer/performance tooling injected into the page.
 *
 * Chromium's detached performance monitor currently injects a minified,
 * anonymous script whose reportAllChanges timer reads a removed performance
 * entry. It runs in the renderer execution context, so the browser forwards the
 * error to window.onerror even though no VoiceStudio code appears in its stack.
 */
export function isInjectedPerformanceMonitorError(message, error, filename) {
  if (filename) return false;
  if (!/^Cannot read properties of undefined \(reading ['"]startTime['"]\)$/.test(message || '')) {
    return false;
  }
  const stack = typeof error?.stack === 'string' ? error.stack : '';
  return /\breportAllChanges \(<anonymous>:2:\d+\)/.test(stack);
}

const BENIGN_MESSAGE = [
  /\bAbortError\b/i,
  /ResizeObserver loop/i,
  /Loading chunk \d+ failed/i,
  /Script error\.?$/i,
];
const EXTENSION_URL = /\b(?:chrome|moz|safari-web|safari|ms-browser)-extension:\/\//i;
// A stack FRAME, not the header: V8 writes "    at fn (url)", while JSC and
// SpiderMonkey write "fn@url". JSC also labels top-level frames with a space
// ("global code@url", "eval code@url", "module code@url"), including top-level
// Safari/WebKit extension execution. These formats are separate from
// the Chromium stacks reported in #1901 and #2148.
// Those labels are listed explicitly; allowing spaces generally would let a V8
// header whose message contains "user@chrome-extension://..." pass as a frame.
const FRAME_LINE = /^\s*at\s|^\s*(?:(?:global|eval|module) code|[^\s@]*)@[a-z-]+:\/\//i;
const FRAME_URL = /[a-z-]+:\/\/[^\s)]+/i;

function errorOrigin(error, filename) {
  if (typeof filename === 'string' && filename) return filename;
  const stack = typeof error?.stack === 'string' ? error.stack : '';
  const frame = stack.split('\n').find((line) => FRAME_LINE.test(line));
  return frame?.match(FRAME_URL)?.[0] || '';
}

/** Normal browser/tooling events that must not be presented or counted as app faults. */
export function isBenignWindowError(message, error, filename) {
  if (error?.name === 'AbortError') return true;
  if (isInjectedPerformanceMonitorError(message, error, filename)) return true;
  if (!message || BENIGN_MESSAGE.some((pattern) => pattern.test(String(message)))) return true;
  return EXTENSION_URL.test(errorOrigin(error, filename));
}
