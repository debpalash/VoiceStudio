import { describe, expect, it, vi } from 'vitest';

const openRepairAgent = vi.fn();
const captureException = vi.fn();
vi.mock('@/lib/repair-agent-events', () => ({ openRepairAgent }));
vi.mock('@shared/utils/analytics', () => ({ captureException }));

describe('global renderer error recovery', () => {
  it('hands uncaught faults to repair once while ignoring cancellation and foreign noise', async () => {
    const earlyCleanup = vi.fn();
    Object.assign(window, {
      __voicestudioEarlyFaults: [
        {
          kind: 'error',
          message: 'startup module failed',
          error: new Error('startup module failed'),
          filename: 'http://localhost/startup.js',
        },
      ],
      __voicestudioStopEarlyErrorCapture: earlyCleanup,
    });
    const { installGlobalErrorRecovery, runRendererTask } = await import('./global-error-recovery');
    installGlobalErrorRecovery();
    expect(earlyCleanup).toHaveBeenCalledOnce();
    expect(openRepairAgent).toHaveBeenCalledWith(
      expect.stringContaining('startup module failed'),
      true,
    );
    expect(captureException).toHaveBeenCalledWith(expect.any(Error), 'renderer:uncaught');
    const privatePath = 'C:\\Users\\pal\\secret\\renderer.ts';
    const failure = new Error(`boom at ${privatePath}`);

    window.dispatchEvent(
      new ErrorEvent('error', {
        message: failure.message,
        error: failure,
        filename: privatePath,
        cancelable: true,
      }),
    );
    const repeatedFailure = new ErrorEvent('error', {
      message: failure.message,
      error: failure,
      filename: privatePath,
      cancelable: true,
    });
    window.dispatchEvent(repeatedFailure);

    expect(openRepairAgent).toHaveBeenCalledTimes(2);
    expect(openRepairAgent).toHaveBeenCalledWith(
      expect.stringContaining('UNCAUGHT_RENDERER_ERROR'),
      true,
    );
    expect(openRepairAgent.mock.calls[1][0]).not.toContain('C:\\Users\\pal');
    expect(repeatedFailure.defaultPrevented).toBe(true);
    expect(captureException).toHaveBeenCalledWith(failure, 'renderer:uncaught');

    const cancelled = new Event('unhandledrejection');
    Object.defineProperty(cancelled, 'reason', {
      value: new DOMException('cancelled', 'AbortError'),
    });
    window.dispatchEvent(cancelled);
    window.dispatchEvent(
      new ErrorEvent('error', {
        message: 'extension failed',
        error: new Error('extension failed'),
        filename: 'chrome-extension://fixture/content.js',
      }),
    );
    expect(openRepairAgent).toHaveBeenCalledTimes(2);
    expect(cancelled.defaultPrevented).toBe(false);

    const ownFailure = new Error('own async failure');
    ownFailure.stack =
      'Error: own async failure\n    at run (http://localhost/app.js:1:1)\n    at chrome-extension://fixture/content.js:2:2';
    const ownRejection = new Event('unhandledrejection', { cancelable: true });
    Object.defineProperty(ownRejection, 'reason', { value: ownFailure });
    window.dispatchEvent(ownRejection);
    expect(openRepairAgent).toHaveBeenCalledTimes(3);
    expect(ownRejection.defaultPrevented).toBe(true);
    expect(captureException).toHaveBeenCalledWith(ownFailure, 'renderer:rejection');

    runRendererTask('Command action', () => Promise.reject(new Error('route failed')));
    await Promise.resolve();
    expect(openRepairAgent).toHaveBeenCalledTimes(4);
    expect(openRepairAgent.mock.calls[3][0]).toContain('Command action');
    expect(captureException).toHaveBeenCalledWith(expect.any(Error), 'renderer:task');

    runRendererTask('Immediate action', () => {
      throw new Error('sync failed');
    });
    expect(openRepairAgent).toHaveBeenCalledTimes(5);
  });
});
