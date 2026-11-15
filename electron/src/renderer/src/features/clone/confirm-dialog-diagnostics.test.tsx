import { cleanup, render } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mocks = vi.hoisted(() => ({
  confirm: undefined as undefined | (() => unknown),
  capture: vi.fn(async () => {}),
  repair: vi.fn(),
  analytics: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('sonner', () => ({ toast: { error: vi.fn() } }));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ stage: 'ready' }),
  getBackendStatusSnapshot: () => ({ stage: 'ready' }),
}));
vi.mock('@/lib/repair-agent-events', () => ({ openRepairAgent: mocks.repair }));
vi.mock('@shared/utils/analytics', () => ({ captureException: mocks.analytics }));
// Keep the actual confirmation action and query mutation, without a portal.
vi.mock('@/components/ui/dialog', () => {
  const Container = ({ children }: { children: ReactNode }) => <div>{children}</div>;
  return {
    Dialog: Container,
    DialogClose: Container,
    DialogContent: Container,
    DialogDescription: Container,
    DialogFooter: Container,
    DialogHeader: Container,
    DialogTitle: Container,
  };
});
vi.mock('@/components/ui/button', () => ({
  Button: ({ onClick, children }: { onClick?: () => unknown; children: ReactNode }) => {
    if (children === 'confirm') mocks.confirm = onClick;
    return <button onClick={onClick}>{children}</button>;
  },
}));

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => (resolve = done));
  return { promise, resolve };
}

let removeListeners = () => {};
beforeEach(() => {
  vi.resetModules();
  mocks.capture.mockClear();
  mocks.repair.mockClear();
  mocks.analytics.mockClear();
  mocks.confirm = undefined;
  const listeners = vi.spyOn(window, 'addEventListener');
  removeListeners = () => {
    for (const [type, callback, options] of listeners.mock.calls)
      window.removeEventListener(type, callback, options);
  };
});
afterEach(() => {
  cleanup();
  removeListeners();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

async function fixture(initiallyOn: boolean) {
  let enabled = initiallyOn;
  let epoch = 1;
  const state = () => ({
    enabled,
    opted_in: enabled,
    available: true,
    prompted: true,
    grant_epoch: enabled ? epoch.toString(16).padStart(32, '0') : null,
  });
  Object.assign(window, {
    voicestudio: {
      diagnostics: {
        getStatus: async () => state(),
        setConsent: async (choice: boolean) => {
          if (choice && !enabled) epoch++;
          enabled = choice;
          return state();
        },
        capture: mocks.capture,
      },
    },
  });
  const diagnostics = await import('@/lib/diagnostic-reports');
  const recovery = await import('@/lib/global-error-recovery');
  recovery.installGlobalErrorRecovery();
  await diagnostics.refreshDiagnosticState();
  const { ConfirmDialog } = await import('./confirm-dialog');
  const { useClearHistory } = await import('@/hooks/use-history');
  const client = new QueryClient({ defaultOptions: { mutations: { retry: false } } });
  function ClearHistoryConfirmation() {
    const mutation = useClearHistory();
    return (
      <ConfirmDialog
        open
        onOpenChange={() => {}}
        title="history"
        description="clear"
        confirmLabel="confirm"
        onConfirm={async () => {
          await mutation.mutateAsync();
        }}
      />
    );
  }
  render(
    <QueryClientProvider client={client}>
      <ClearHistoryConfirmation />
    </QueryClientProvider>,
  );
  const dispatch = (promise: Promise<unknown>, reason: unknown) => {
    const event = new Event('unhandledrejection', { cancelable: true });
    Object.defineProperties(event, { promise: { value: promise }, reason: { value: reason } });
    window.dispatchEvent(event);
    return event;
  };
  const submit = () => {
    const promise = mocks.confirm!() as Promise<void>;
    // The fixture consumes rejection to avoid a runner-global unhandled error,
    // then dispatches the browser event with the exact action Promise identity.
    const outcome = promise.then(
      () => undefined,
      (error: unknown) => error,
    );
    return { promise, outcome };
  };
  return { diagnostics, dispatch, submit, client };
}

it.each(['off-on', 'regrant', 'same-grant'] as const)(
  'binds the actual history confirmation across a held HTTP error body: %s',
  async (transition) => {
    const f = await fixture(transition !== 'off-on');
    const body = deferred<string>();
    const entered = deferred<void>();
    const fetch = vi.fn(async () => {
      const response = new Response('', { status: 500 });
      response.text = () => {
        entered.resolve();
        return body.promise;
      };
      return response;
    });
    vi.stubGlobal('fetch', fetch);
    const old = f.submit();
    await entered.promise;
    expect(fetch).toHaveBeenCalledWith(expect.stringContaining('/history'), { method: 'DELETE' });
    if (transition !== 'same-grant') {
      await f.diagnostics.setDiagnosticConsent(false);
      await f.diagnostics.setDiagnosticConsent(true);
    }
    body.resolve('{"detail":"private held history failure"}');
    const reason = await old.outcome;
    f.dispatch(old.promise, reason);
    expect(mocks.repair).toHaveBeenCalledOnce();
    expect(mocks.capture).toHaveBeenCalledTimes(transition === 'same-grant' ? 1 : 0);
    expect(JSON.stringify(mocks.capture.mock.calls)).not.toContain('private held history failure');

    // A new action under the current grant is eligible, independent of the old unit.
    mocks.capture.mockClear();
    fetch.mockImplementation(
      async () => new Response('{"detail":"fresh failure"}', { status: 500 }),
    );
    const fresh = f.submit();
    f.dispatch(fresh.promise, await fresh.outcome);
    expect(mocks.capture).toHaveBeenCalledOnce();

    // Expected cancellation retains its ordinary local-only semantics.
    fetch.mockRejectedValueOnce(new DOMException('cancelled', 'AbortError'));
    const cancelled = f.submit();
    const event = f.dispatch(cancelled.promise, await cancelled.outcome);
    expect(event.defaultPrevented).toBe(false);
    expect(mocks.capture).toHaveBeenCalledOnce();
    f.client.clear();
  },
);

it('keeps an unknown promise local even when the same Error has an owned promise', async () => {
  const f = await fixture(true);
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => new Response('{"detail":"same failure"}', { status: 500 })),
  );
  const owned = f.submit();
  const reason = await owned.outcome;
  const unrelated = Promise.resolve();
  f.dispatch(unrelated, reason);
  expect(mocks.capture).not.toHaveBeenCalled();
  expect(mocks.repair).toHaveBeenCalledOnce();
  f.dispatch(owned.promise, reason);
  expect(mocks.capture).toHaveBeenCalledOnce();
  f.client.clear();
});

it.each(['off-on', 'regrant'] as const)(
  'preserves the first known promise owner when rewrapped after %s',
  async (transition) => {
    const f = await fixture(transition !== 'off-on');
    const { rendererTask } = await import('@/lib/global-error-recovery');
    let fail!: (error: Error) => void;
    const original = rendererTask(() => new Promise<void>((_, reject) => (fail = reject)));
    const outcome = original.catch((error: unknown) => error);
    await f.diagnostics.setDiagnosticConsent(false);
    await f.diagnostics.setDiagnosticConsent(true);
    const rewrapped = rendererTask(() => original);
    expect(rewrapped).toBe(original);
    fail(new Error('old owner after rewrap'));
    f.dispatch(rewrapped, await outcome);
    expect(mocks.repair).toHaveBeenCalledOnce();
    expect(mocks.capture).not.toHaveBeenCalled();
    f.client.clear();
  },
);
