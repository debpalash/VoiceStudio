import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { cleanup, render } from '@testing-library/react';
import { createElement } from 'react';

const openRepairAgent = vi.fn();
const captureException = vi.fn();
const diagnosticCapture = vi.fn(async () => {});
const diagnosticStatus = vi.fn(async () => ({
  enabled: true,
  opted_in: true,
  available: true,
  prompted: true,
  grant_epoch: 'a'.repeat(32),
}));
vi.mock('@/lib/repair-agent-events', () => ({ openRepairAgent }));
vi.mock('@shared/utils/analytics', () => ({ captureException }));
vi.mock('@/components/report-bug', () => ({ ReportBug: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

let removeCapturedListeners = () => {};
beforeEach(() => {
  vi.resetModules();
  openRepairAgent.mockClear();
  captureException.mockClear();
  diagnosticCapture.mockClear();
  diagnosticStatus.mockReset().mockResolvedValue({
    enabled: true,
    opted_in: true,
    available: true,
    prompted: true,
    grant_epoch: 'a'.repeat(32),
  });
  const registrations = vi.spyOn(window, 'addEventListener');
  removeCapturedListeners = () => {
    for (const [type, listener, options] of registrations.mock.calls)
      window.removeEventListener(type, listener, options);
  };
});
afterEach(() => {
  cleanup();
  removeCapturedListeners();
  vi.restoreAllMocks();
});

it.each(['off-on', 'regrant', 'same-grant'] as const)(
  'keeps a real unknown window timer fault local across %s',
  async (transition) => {
    const timerWindow = window;
    let timer: number | undefined;
    let enabled = transition !== 'off-on';
    let epoch = 1;
    const state = () => ({
      enabled,
      opted_in: enabled,
      available: true,
      prompted: true,
      grant_epoch: enabled ? epoch.toString(16).padStart(32, '0') : null,
    });
    Object.assign(timerWindow, {
      voicestudio: {
        diagnostics: {
          getStatus: async () => state(),
          setConsent: async (choice: boolean) => {
            if (choice && !enabled) epoch++;
            enabled = choice;
            return state();
          },
          capture: diagnosticCapture,
        },
      },
    });
    try {
      const recovery = await import('./global-error-recovery');
      const diagnostics = await import('./diagnostic-reports');
      recovery.installGlobalErrorRecovery();
      await diagnostics.refreshDiagnosticState();
      const delivered = new Promise<ErrorEvent>((resolve) =>
        timerWindow.addEventListener('error', resolve, { once: true }),
      );
      // Actual Window timer delivery, not a manually dispatched ErrorEvent.
      timer = timerWindow.setTimeout(() => {
        throw new Error(`earlier unknown timer ${transition}`);
      }, 0);
      if (transition !== 'same-grant') {
        await diagnostics.setDiagnosticConsent(false);
        await diagnostics.setDiagnosticConsent(true);
      }
      const event = await delivered;
      expect(event).toBeInstanceOf(timerWindow.ErrorEvent);
      expect(event.error.message).toBe(`earlier unknown timer ${transition}`);
      expect(event.defaultPrevented).toBe(true);
      expect(openRepairAgent).toHaveBeenCalledOnce();
      expect(captureException).toHaveBeenCalledWith(event.error, 'renderer:uncaught');
      expect(diagnosticCapture).not.toHaveBeenCalled();

      // Known new semantic work still reports under the current grant.
      recovery.runRendererTask('Fresh known renderer action', () => {
        throw new Error('new known action failure');
      });
      expect(diagnosticCapture).toHaveBeenCalledOnce();
      recovery.runRendererTask('Expected cancellation', () => {
        throw new DOMException('cancelled', 'AbortError');
      });
      expect(diagnosticCapture).toHaveBeenCalledOnce();
    } finally {
      if (timer !== undefined) timerWindow.clearTimeout(timer);
    }
  },
);

it('still reports an actual fresh synchronous React render failure', async () => {
  Object.assign(window, {
    voicestudio: { diagnostics: { getStatus: diagnosticStatus, capture: diagnosticCapture } },
  });
  const diagnostics = await import('./diagnostic-reports');
  await diagnostics.refreshDiagnosticState();
  const { ErrorBoundary } = await import('@/components/error-boundary');
  vi.spyOn(console, 'error').mockImplementation(() => {});
  function BrokenRender(): never {
    throw new Error('fresh synchronous render failure');
  }
  render(createElement(ErrorBoundary, null, createElement(BrokenRender)));
  expect(diagnosticCapture).toHaveBeenCalledOnce();
  expect(diagnosticCapture).toHaveBeenCalledWith(
    expect.objectContaining({ category: 'renderer_crash', stage: 'render' }),
    'a'.repeat(32),
  );
});

describe('global renderer error recovery', () => {
  it('does not replay an OFF-boot fault when Yes precedes the initial status response', async () => {
    type Status = Awaited<ReturnType<typeof diagnosticStatus>>;
    let finish!: (value: Status) => void;
    diagnosticStatus.mockReturnValueOnce(
      new Promise<Status>((resolve) => {
        finish = resolve;
      }),
    );
    const on = {
      enabled: true,
      opted_in: true,
      available: true,
      prompted: true,
      grant_epoch: 'a'.repeat(32),
    };
    Object.assign(window, {
      voicestudio: {
        diagnostics: {
          getStatus: diagnosticStatus,
          setConsent: vi.fn(async () => on),
          capture: diagnosticCapture,
        },
      },
      __voicestudioEarlyFaults: [
        {
          kind: 'error',
          message: 'early module fault',
          error: new Error('early module fault'),
          filename: 'http://localhost/startup.js',
        },
      ],
      __voicestudioStopEarlyErrorCapture: vi.fn(),
    });
    const recovery = await import('./global-error-recovery');
    const diagnostics = await import('./diagnostic-reports');
    recovery.installGlobalErrorRecovery();
    const initialRead = diagnostics.refreshDiagnosticState();
    await vi.waitFor(() => expect(diagnosticStatus).toHaveBeenCalledOnce());
    await diagnostics.setDiagnosticConsent(true);
    finish(on);
    // Await the actual original read, after the startup replay callback was registered.
    expect(await initialRead).toBe(false);
    expect(diagnosticCapture).not.toHaveBeenCalled();
    expect(openRepairAgent).toHaveBeenCalledOnce(); // Local recovery still runs.
    window.dispatchEvent(
      new ErrorEvent('error', {
        message: 'new live fault',
        error: new Error('new live fault'),
        cancelable: true,
      }),
    );
    expect(diagnosticCapture).not.toHaveBeenCalled();
    recovery.runRendererTask('Fresh known startup action', () => {
      throw new Error('new owned startup failure');
    });
    expect(diagnosticCapture).toHaveBeenCalledOnce();
    expect(diagnosticCapture).toHaveBeenCalledWith(
      expect.objectContaining({
        category: 'operation_failed',
        stage: 'other',
      }),
      'a'.repeat(32),
    );
  });

  it('does not lend another client’s new grant to faults queued before the initial response', async () => {
    type Status = Awaited<ReturnType<typeof diagnosticStatus>>;
    let finish!: (value: Status) => void;
    diagnosticStatus.mockReturnValueOnce(
      new Promise<Status>((resolve) => {
        finish = resolve;
      }),
    );
    const on = {
      enabled: true,
      opted_in: true,
      available: true,
      prompted: true,
      grant_epoch: 'a'.repeat(32),
    };
    Object.assign(window, {
      voicestudio: {
        diagnostics: {
          getStatus: diagnosticStatus,
          setConsent: vi.fn(async () => on),
          capture: diagnosticCapture,
        },
      },
      __voicestudioEarlyFaults: [
        {
          kind: 'error',
          message: 'OFF-boot fault',
          error: new Error('OFF-boot fault'),
          filename: 'http://localhost/startup.js',
        },
      ],
      __voicestudioStopEarlyErrorCapture: vi.fn(),
    });
    const recovery = await import('./global-error-recovery');
    const firstClient = await import('./diagnostic-reports');
    recovery.installGlobalErrorRecovery();
    const initialRead = firstClient.refreshDiagnosticState();
    await vi.waitFor(() => expect(diagnosticStatus).toHaveBeenCalledOnce());
    // A second module realm models another admin tab/window sharing the owner,
    // with independent renderer-local consent revisions.
    vi.resetModules();
    const otherClient = await import('./diagnostic-reports');
    await otherClient.setDiagnosticConsent(true);
    finish(on);
    expect(await initialRead).toBe(true);
    expect(diagnosticCapture).not.toHaveBeenCalled();
    expect(openRepairAgent).toHaveBeenCalledOnce();
  });

  it('keeps unowned faults local and reports known tasks while ignoring cancellation and foreign noise', async () => {
    Object.assign(window, {
      voicestudio: { diagnostics: { getStatus: diagnosticStatus, capture: diagnosticCapture } },
    });
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
    const diagnostics = await import('./diagnostic-reports');
    installGlobalErrorRecovery();
    await diagnostics.refreshDiagnosticState();
    expect(diagnosticStatus).toHaveBeenCalledOnce();
    expect(diagnosticCapture).not.toHaveBeenCalled();
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
    expect(diagnosticCapture).not.toHaveBeenCalled();
    // Another unowned fault stays local while the repair panel is throttled.
    window.dispatchEvent(
      new ErrorEvent('error', {
        message: failure.message,
        error: new Error(failure.message),
        filename: privatePath,
        cancelable: true,
      }),
    );
    expect(openRepairAgent).toHaveBeenCalledTimes(2);
    expect(diagnosticCapture).not.toHaveBeenCalled();
    expect(JSON.stringify(diagnosticCapture.mock.calls)).not.toContain(privatePath);

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
    expect(diagnosticCapture).not.toHaveBeenCalled();

    const ownFailure = new Error('own async failure');
    ownFailure.stack =
      'Error: own async failure\n    at run (http://localhost/app.js:1:1)\n    at chrome-extension://fixture/content.js:2:2';
    const ownRejection = new Event('unhandledrejection', { cancelable: true });
    Object.defineProperty(ownRejection, 'reason', { value: ownFailure });
    window.dispatchEvent(ownRejection);
    expect(openRepairAgent).toHaveBeenCalledTimes(3);
    expect(ownRejection.defaultPrevented).toBe(true);
    expect(captureException).toHaveBeenCalledWith(ownFailure, 'renderer:rejection');
    expect(diagnosticCapture).not.toHaveBeenCalled();

    runRendererTask('Command action', () => Promise.reject(new Error('route failed')));
    await Promise.resolve();
    expect(openRepairAgent).toHaveBeenCalledTimes(4);
    expect(openRepairAgent.mock.calls[3][0]).toContain('Command action');
    expect(captureException).toHaveBeenCalledWith(expect.any(Error), 'renderer:task');
    expect(diagnosticCapture).toHaveBeenCalledOnce();

    runRendererTask('Immediate action', () => {
      throw new Error('sync failed');
    });
    expect(openRepairAgent).toHaveBeenCalledTimes(5);
    expect(diagnosticCapture).toHaveBeenCalledTimes(2);
  });
});

it.each(['off-on', 'regrant', 'same-grant'] as const)(
  'keeps an event-triggered renderer task with its original consent: %s',
  async (transition) => {
    let enabled = false,
      grants = 0;
    const state = () => ({
      enabled,
      opted_in: enabled,
      available: true,
      prompted: true,
      grant_epoch: enabled ? (++grants).toString(16).padStart(32, '0') : null,
    });
    let acknowledged = state();
    Object.assign(window, {
      voicestudio: {
        diagnostics: {
          getStatus: async () => acknowledged,
          setConsent: async (choice: boolean) => {
            enabled = choice;
            acknowledged = state();
            return acknowledged;
          },
          capture: diagnosticCapture,
        },
      },
    });
    const recovery = await import('./global-error-recovery');
    const diagnostics = await import('./diagnostic-reports');
    await diagnostics.refreshDiagnosticState();
    if (transition !== 'off-on') await diagnostics.setDiagnosticConsent(true);
    let fail!: (reason: Error) => void;
    recovery.runRendererTask(
      'Earlier renderer task',
      () =>
        new Promise<void>((_, reject) => {
          fail = reject;
        }),
    );
    if (transition !== 'same-grant') {
      await diagnostics.setDiagnosticConsent(false);
      await diagnostics.setDiagnosticConsent(true);
    }
    fail(new Error('private old task failure'));
    await vi.waitFor(() => expect(captureException).toHaveBeenCalledOnce());
    expect(diagnosticCapture).toHaveBeenCalledTimes(transition === 'same-grant' ? 1 : 0);
    expect(openRepairAgent).toHaveBeenCalledOnce();
    diagnosticCapture.mockClear();
    recovery.runRendererTask('Fresh renderer task', () =>
      Promise.reject(new Error('fresh private failure')),
    );
    await vi.waitFor(() => expect(captureException).toHaveBeenCalledTimes(2));
    expect(diagnosticCapture).toHaveBeenCalledOnce();
    recovery.runRendererTask('Cancelled renderer task', () =>
      Promise.reject(new DOMException('expected cancellation', 'AbortError')),
    );
    await Promise.resolve();
    expect(captureException).toHaveBeenCalledTimes(2);
    expect(diagnosticCapture).toHaveBeenCalledOnce();
  },
);
