import { beforeEach, describe, expect, it, vi } from 'vitest';

const { toastErrorWithReport } = vi.hoisted(() => ({
  toastErrorWithReport: vi.fn(),
}));

vi.mock('./errorToast', () => ({ toastErrorWithReport }));

import { installGlobalErrorHandlers } from './globalErrorHandlers';

function dispatchUnhandledRejection(reason) {
  const event = new Event('unhandledrejection');
  Object.defineProperty(event, 'reason', { value: reason });
  window.dispatchEvent(event);
}

function dispatchError({ message, filename, error }) {
  const event = new Event('error');
  Object.defineProperty(event, 'message', { value: message });
  Object.defineProperty(event, 'filename', { value: filename });
  Object.defineProperty(event, 'error', { value: error });
  window.dispatchEvent(event);
}

/** An Error whose stack is entirely extension frames, as in #1901. */
function extensionError(message) {
  const err = new Error(message);
  err.stack = [
    `TypeError: ${message}`,
    '    at Y (chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js:1:761)',
    '    at E (chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js:1:1442)',
  ].join('\n');
  return err;
}

beforeEach(() => {
  toastErrorWithReport.mockClear();
});

describe('global unhandled rejection reporting', () => {
  it('ignores a named AbortError while still surfacing a real rejection', () => {
    installGlobalErrorHandlers();

    const cancelled = new DOMException('BodyStreamBuffer was aborted', 'AbortError');
    dispatchUnhandledRejection(cancelled);
    expect(toastErrorWithReport).not.toHaveBeenCalled();

    const failure = new Error('waveform request failed');
    dispatchUnhandledRejection(failure);
    expect(toastErrorWithReport).toHaveBeenCalledOnce();
    expect(toastErrorWithReport.mock.calls[0][1]).toBe(failure);
  });
});

/**
 * A browser extension throws into the page's own error channel, so the toast
 * offered "Report this bug" for code that is not ours. #1901 is one such
 * report: the stack is entirely
 * `chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js` with
 * no VoiceStudio frame in it, and the maintainer had no way to tell from the
 * message ("Cannot read properties of undefined") that it was not a real bug.
 *
 * The message-based IGNORE_PATTERNS cannot help here — an extension's TypeError
 * reads exactly like one of ours — and the existing `Script error.` entry only
 * covers the opaque cross-origin case. An extension's script is not opaque, so
 * it arrives with a full stack and goes straight through.
 *
 * consoleBuffer still records these into Settings → Logs → Frontend; what is
 * suppressed is the offer to file them against this project.
 */
describe('errors thrown by a browser extension', () => {
  it('does not offer to report an error event from an extension script', () => {
    installGlobalErrorHandlers();

    dispatchError({
      message: "Cannot read properties of undefined (reading 'M_ID')",
      filename: 'chrome-extension://eppiocemhmnlbhjplcgkofciiegomcon/executors/200.js',
      error: extensionError("Cannot read properties of undefined (reading 'M_ID')"),
    });

    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('does not offer to report a rejection whose throw site is an extension', () => {
    installGlobalErrorHandlers();
    // No `filename` on an unhandledrejection, so the throw site has to come
    // off the stack.
    dispatchUnhandledRejection(extensionError('extension promise blew up'));

    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('reads the origin off the first FRAME, not off a URL in the message', () => {
    // Greptile: an extension error whose message carries a URL. Scanning the
    // whole stack matched the message's URL first, reported that as the origin,
    // and let the extension through the filter — the bug this PR exists to fix,
    // surviving inside the fix.
    installGlobalErrorHandlers();

    const err = new Error('Failed to fetch https://example.com/api');
    err.stack = [
      'TypeError: Failed to fetch https://example.com/api',
      '    at Y (chrome-extension://someid/executors/200.js:1:761)',
    ].join('\n');
    dispatchUnhandledRejection(err);

    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('still reports our error when the MESSAGE quotes an extension URL', () => {
    // CodeRabbit, the same defect from the other side and the worse half: one
    // of our own failures that happens to name a chrome-extension:// URL in
    // its text was suppressed as if an extension had thrown it.
    installGlobalErrorHandlers();

    const ours = new Error('blocked request to chrome-extension://someid/x.js');
    ours.stack = [
      'Error: blocked request to chrome-extension://someid/x.js',
      '    at loadAsset (http://tauri.localhost/assets/main-app.js:9:1)',
    ].join('\n');
    dispatchUnhandledRejection(ours);

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
    expect(toastErrorWithReport.mock.calls[0][1]).toBe(ours);
  });

  it('does not read a V8 header that happens to contain an @ URL as a frame', () => {
    // Greptile: the JSC frame alternative was unanchored, so a header reading
    // `... user@chrome-extension://...` matched as a frame and its message URL
    // became the origin. Same false positive as the message-URL case, one layer
    // down, and the earlier test missed it because its message had no `@`.
    installGlobalErrorHandlers();

    const ours = new Error('blocked request to user@chrome-extension://someid/x.js');
    ours.stack = [
      'Error: blocked request to user@chrome-extension://someid/x.js',
      '    at loadAsset (http://tauri.localhost/assets/main-app.js:9:1)',
    ].join('\n');
    dispatchUnhandledRejection(ours);

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
    expect(toastErrorWithReport.mock.calls[0][1]).toBe(ours);
  });

  it('still treats a real JSC frame as a frame', () => {
    // The anchor must not cost the Safari/Firefox stack shape, which has no
    // header line at all and puts the `@` right after the function name.
    installGlobalErrorHandlers();

    const err = new Error('jsc shaped stack');
    err.stack = 'Y@chrome-extension://someid/200.js:1:761';
    dispatchUnhandledRejection(err);

    expect(toastErrorWithReport).not.toHaveBeenCalled();
  });

  it('reports rather than guesses when the throw site names no URL', () => {
    // A native or anonymous first frame leaves the origin unknown. Walking
    // deeper to find a URL would attribute the error to a frame that did not
    // throw it, so an unknown origin means "offer the report" — leaving noise
    // in is cheaper than silencing one of ours.
    installGlobalErrorHandlers();

    const err = new Error('sort comparator exploded');
    err.stack = [
      'TypeError: sort comparator exploded',
      '    at Array.sort (<anonymous>)',
      '    at Y (chrome-extension://someid/inject.js:1:1)',
    ].join('\n');
    dispatchUnhandledRejection(err);

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
  });

  it('still reports our own error when an extension frame sits below it', () => {
    // The reason this filters on the THROW SITE and not on "any frame mentions
    // an extension": an extension that patches a built-in leaves its frame in
    // the middle of a stack whose fault is genuinely ours. Dropping those would
    // silence real bugs, which is worse than the noise it saves.
    installGlobalErrorHandlers();

    const ours = new Error('dub export failed');
    ours.stack = [
      'Error: dub export failed',
      '    at exportDub (http://tauri.localhost/assets/main-app.js:9:1)',
      '    at patched (chrome-extension://someid/inject.js:1:1)',
    ].join('\n');
    dispatchError({ message: ours.message, filename: undefined, error: ours });

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
    expect(toastErrorWithReport.mock.calls[0][1]).toBe(ours);
  });

  // WebKit labels its top-level frames "global code@…", "eval code@…" and
  // "module code@…" — the only frame labels that contain a space. The frame
  // matcher's anti-header anchoring rejected them, so on WKWebView (the macOS
  // desktop shell) and Safari an extension's error found no matching frame,
  // fell through with an unknown origin, and still offered the report — the
  // exact #1901 behaviour, unfixed on the browser engine that needs it most.
  it.each(['global code', 'eval code', 'module code'])(
    'suppresses an extension error thrown from a WebKit "%s" frame',
    (label) => {
      installGlobalErrorHandlers();

      // A distinct message per label: shouldShow() throttles by message text,
      // so reusing one would let the second and third cases pass on the
      // throttle rather than on the frame match they exist to prove.
      const err = new Error(`extension threw from ${label}`);
      err.stack = [
        `${label}@chrome-extension://someid/executors/200.js:1:9`,
        'promiseReactionJob@[native code]',
      ].join('\n');
      dispatchUnhandledRejection(err);

      expect(toastErrorWithReport).not.toHaveBeenCalled();
    },
  );

  it('still reports our own error thrown from a WebKit global frame', () => {
    // The label must not become a blanket mute: a top-level throw of OURS
    // carries the same shape and has to keep reaching the user.
    installGlobalErrorHandlers();

    const ours = new Error('webkit top-level render crash');
    ours.stack = ['global code@http://tauri.localhost/assets/main-app.js:9:1'].join('\n');
    dispatchUnhandledRejection(ours);

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
  });

  it('does not treat a V8 header mentioning an extension URL as a frame', () => {
    // Regression guard for the anchoring this change had to preserve: the
    // message text below contains "code@chrome-extension://", and reading it
    // as a frame would attribute one of our errors to an extension and mute it.
    installGlobalErrorHandlers();

    const ours = new Error('failed to load code@chrome-extension://someid/x.js');
    ours.stack = [
      'TypeError: failed to load code@chrome-extension://someid/x.js',
      '    at loadThing (http://tauri.localhost/assets/main-app.js:4:1)',
    ].join('\n');
    dispatchUnhandledRejection(ours);

    expect(toastErrorWithReport).toHaveBeenCalledOnce();
  });
});
