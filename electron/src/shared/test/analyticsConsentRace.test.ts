import { expect, it, vi } from 'vitest';

it('cannot opt back in when SDK loading completes after consent was withdrawn', async () => {
  vi.resetModules();
  const sdk = {
    init: vi.fn(),
    opt_in_capturing: vi.fn(),
    opt_out_capturing: vi.fn(),
    reset: vi.fn(),
    captureException: vi.fn(),
  };
  let finish!: (value: { default: typeof sdk }) => void;
  const loading = new Promise<{ default: typeof sdk }>((resolve) => {
    finish = resolve;
  });
  const imported = vi.fn(() => loading);
  vi.doMock('posthog-js/dist/module.slim.no-external', imported);
  vi.doMock('posthog-js/dist/extension-bundles', () => ({ ErrorTrackingExtensions: {} }));
  const analytics = await import('../utils/analytics');
  const pending = analytics.enableAnalytics();
  await vi.waitFor(() => expect(imported).toHaveBeenCalled());
  analytics.disableAnalytics();
  finish({ default: sdk });
  await pending;
  expect(sdk.init).not.toHaveBeenCalled();
  expect(sdk.opt_in_capturing).not.toHaveBeenCalled();
  await analytics.enableAnalytics(); // A later explicit opt-in is still honored.
  expect(sdk.opt_in_capturing).toHaveBeenCalledOnce();
  vi.doUnmock('posthog-js/dist/module.slim.no-external');
  vi.doUnmock('posthog-js/dist/extension-bundles');
});
