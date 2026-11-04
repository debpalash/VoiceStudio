import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({
  api: vi.fn(),
  status: { stage: 'setup_required' } as Record<string, unknown>,
}));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('@/hooks/use-backend-status', () => ({ useBackendStatus: () => mock.status }));
vi.mock('@shared/utils/analytics', () => ({
  analyticsAvailable: () => true,
  capture: vi.fn(),
  capturePageview: vi.fn(),
  disableAnalytics: vi.fn(),
  enableAnalytics: vi.fn(async () => {}),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { AnalyticsConsent, AnalyticsRuntime, SetupAnalyticsConsent } from './analytics-consent';
import { capture, enableAnalytics } from '@shared/utils/analytics';
import { readSetupAnalytics, rememberSetupAnalytics } from '@/lib/setup-analytics';
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  localStorage.clear();
  mock.status = { stage: 'setup_required' };
});
function mount() {
  const client = new QueryClient();
  render(
    <QueryClientProvider client={client}>
      <AnalyticsConsent />
    </QueryClientProvider>,
  );
  return client;
}
it.each([true, false])(
  'persists only an explicit %s choice and marks it prompted',
  async (enabled) => {
    mock.api.mockResolvedValue({ available: true, prompted: false, opted_in: false });
    const client = mount();
    const yes = await screen.findByRole('button', { name: 'consent.yes' });
    const no = screen.getByRole('button', { name: 'consent.no' });
    expect(yes.className).toBe(no.className);
    expect(mock.api).toHaveBeenCalledTimes(1);
    fireEvent.click(enabled ? yes : no);
    await waitFor(() =>
      expect(client.getQueryData(['privacy', 'analytics'])).toMatchObject({
        prompted: true,
        opted_in: enabled,
      }),
    );
    expect(mock.api).toHaveBeenLastCalledWith('/api/settings/analytics', {
      method: 'PUT',
      body: JSON.stringify({ enabled }),
    });
    expect(screen.queryByRole('button', { name: 'consent.yes' })).not.toBeInTheDocument();
  },
);
it('leaves consent unprompted and retryable after a failed write', async () => {
  mock.api
    .mockResolvedValueOnce({ available: true, prompted: false, opted_in: false })
    .mockRejectedValue(new Error('offline'));
  const client = mount();
  fireEvent.click(await screen.findByRole('button', { name: 'consent.no' }));
  await screen.findByRole('alert');
  expect(client.getQueryData(['privacy', 'analytics'])).toMatchObject({
    prompted: false,
    opted_in: false,
  });
  expect(screen.getByRole('button', { name: 'consent.yes' })).toBeEnabled();
});
it('does not ask again after an earlier choice', async () => {
  mock.api.mockResolvedValue({ available: true, prompted: true, opted_in: false });
  mount();
  await waitFor(() => expect(mock.api).toHaveBeenCalledTimes(1));
  expect(screen.queryByRole('button', { name: 'consent.yes' })).not.toBeInTheDocument();
});

function runtime() {
  const client = new QueryClient();
  const view = render(
    <QueryClientProvider client={client}>
      <AnalyticsRuntime />
    </QueryClientProvider>,
  );
  return { client, view };
}
const failedSetup = {
  stage: 'setup_required',
  failure: {
    code: 'EXEC_REFUSED',
    stage: 'setup_required',
    program: 'python.exe',
    antivirus: ['Secret AV'],
    blockedFiles: ['private.dll'],
  },
};

it('asks before setup with equal buttons and starts nothing until yes', () => {
  render(<SetupAnalyticsConsent />);
  const yes = screen.getByRole('button', { name: 'consent.yes' });
  const no = screen.getByRole('button', { name: 'consent.no' });
  expect(yes.className).toBe(no.className);
  expect(enableAnalytics).not.toHaveBeenCalled();
  fireEvent.click(no);
  expect(readSetupAnalytics()).toEqual({ enabled: false, settled: false });
  expect(enableAnalytics).not.toHaveBeenCalled();
  expect(screen.queryByRole('button', { name: 'consent.yes' })).not.toBeInTheDocument();
});

it('does not ask when analytics is switched off for this machine', () => {
  render(<SetupAnalyticsConsent analyticsDisabled />);
  expect(screen.queryByTestId('setup-analytics-consent')).not.toBeInTheDocument();
});

it('sends a failed setup with only the code and stage after a yes', async () => {
  rememberSetupAnalytics({ enabled: true, settled: false });
  mock.status = failedSetup;
  runtime();
  await waitFor(() => expect(capture).toHaveBeenCalledTimes(1));
  const [event, props] = vi.mocked(capture).mock.calls[0]!;
  expect(event).toBe('setup_failed');
  expect(props).toMatchObject({ stage: 'setup_required', error_class: 'EXEC_REFUSED' });
  expect(JSON.stringify(props)).not.toMatch(/python\.exe|Secret AV|private\.dll/);
});

it.each([
  ['unanswered', undefined, undefined],
  ['declined', { enabled: false, settled: false }, undefined],
  ['kill-switched', { enabled: true, settled: false }, true],
  ['settled by the backend', { enabled: true, settled: true }, undefined],
])('sends nothing when consent is %s', async (_name, choice, analyticsDisabled) => {
  if (choice) rememberSetupAnalytics(choice);
  mock.status = { ...failedSetup, analyticsDisabled };
  runtime();
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(enableAnalytics).not.toHaveBeenCalled();
  expect(capture).not.toHaveBeenCalled();
});

it('hands an early answer to the backend once', async () => {
  rememberSetupAnalytics({ enabled: false, settled: false });
  mock.status = { stage: 'ready' };
  mock.api.mockResolvedValue({ available: true, prompted: false, opted_in: false });
  runtime();
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/api/settings/analytics', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false }),
    }),
  );
  expect(readSetupAnalytics()).toEqual({ enabled: false, settled: true });
});

it('lets a choice the backend already holds win over the early answer', async () => {
  rememberSetupAnalytics({ enabled: true, settled: false });
  mock.status = { stage: 'ready' };
  mock.api.mockResolvedValue({ available: true, prompted: true, opted_in: false });
  runtime();
  await waitFor(() => expect(readSetupAnalytics()).toEqual({ enabled: false, settled: true }));
  expect(mock.api).toHaveBeenCalledTimes(1);
});

it('honors the kill switch for a user who opted in earlier', async () => {
  mock.status = { stage: 'ready', analyticsDisabled: true };
  mock.api.mockResolvedValue({ available: true, prompted: true, opted_in: true });
  const { client } = runtime();
  await waitFor(() => expect(client.getQueryData(['privacy', 'analytics'])).toBeTruthy());
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(enableAnalytics).not.toHaveBeenCalled();
  expect(capture).not.toHaveBeenCalled();
});

it('answers the wizard before saving, so it cannot race the early answer', async () => {
  rememberSetupAnalytics({ enabled: true, settled: false });
  mock.status = { stage: 'ready' };
  let finishSave = () => {};
  mock.api.mockImplementation((_path: string, init?: { method?: string }) =>
    init?.method === 'PUT'
      ? new Promise((resolve) => {
          finishSave = () => resolve({});
        })
      : Promise.resolve({ available: true, prompted: false, opted_in: false }),
  );
  const { client } = runtime();
  await waitFor(() =>
    expect(client.getQueryData(['privacy', 'analytics'])).toMatchObject({ prompted: true }),
  );
  finishSave();
});

it('asks again and stays off when saving the early answer fails', async () => {
  rememberSetupAnalytics({ enabled: true, settled: false });
  mock.status = { stage: 'ready' };
  let failSave = () => {};
  mock.api.mockImplementation((_path: string, init?: { method?: string }) =>
    init?.method === 'PUT'
      ? new Promise((_resolve, reject) => {
          failSave = () => reject(new Error('offline'));
        })
      : Promise.resolve({ available: true, prompted: false, opted_in: false }),
  );
  const { client } = runtime();
  await waitFor(() => expect(readSetupAnalytics()).toEqual({ enabled: true, settled: true }));
  failSave();
  await waitFor(() => expect(readSetupAnalytics()).toEqual({ enabled: true, settled: false }));
  await waitFor(() =>
    expect(client.getQueryData(['privacy', 'analytics'])).toMatchObject({
      opted_in: false,
      prompted: false,
    }),
  );
  expect(vi.mocked(mock.api).mock.calls.filter(([, init]) => init?.method === 'PUT')).toHaveLength(1);
});
