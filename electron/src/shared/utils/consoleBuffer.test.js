import { beforeEach, describe, expect, it } from 'vitest';
import { clearFrontendLogs, getFrontendLogs, installConsoleCapture } from './consoleBuffer';

describe('consoleBuffer', () => {
  // installConsoleCapture() wraps console.* exactly once per page load (a
  // module-level `installed` guard) — it must NOT be re-installed or have
  // console.warn restored between tests, or later tests run against the
  // un-wrapped original. Install once; only the ring buffer resets per test.
  installConsoleCapture();

  beforeEach(() => {
    clearFrontendLogs();
  });

  it('captures an ordinary warning', () => {
    console.warn('something genuinely worth seeing');
    expect(getFrontendLogs().some((l) => l.msg.includes('something genuinely worth seeing'))).toBe(
      true,
    );
  });

  it("#975: filters Tauri's benign IPC-fallback warning out of the captured buffer", () => {
    console.warn(
      'IPC custom protocol failed, Tauri will now use the postMessage interface instead',
    );
    expect(getFrontendLogs().some((l) => l.msg.includes('IPC custom protocol failed'))).toBe(false);
  });

  it('does not filter a different warning that merely mentions IPC', () => {
    // Prefix match, not a substring match — only Tauri's exact known message
    // is suppressed; anything else that happens to mention "IPC" is not.
    console.warn('some other IPC warning entirely');
    expect(getFrontendLogs().some((l) => l.msg.includes('some other IPC warning entirely'))).toBe(
      true,
    );
  });
});
