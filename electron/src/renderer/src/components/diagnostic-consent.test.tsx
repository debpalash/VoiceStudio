import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  state: { enabled: false, opted_in: false, available: true, prompted: false },
  capture: vi.fn(async () => {}),
  save: vi.fn(),
  read: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/hooks/use-backend-status', () => ({
  useBackendStatus: () => ({ stage: 'setup_required' }),
  isBackendReachable: () => false,
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    diagnostics: {
      getStatus: () => mock.read(),
      setConsent: (...args: unknown[]) => mock.save(...args),
      capture: mock.capture,
    },
  }),
}));
let DiagnosticConsent: typeof import('./diagnostic-consent').DiagnosticConsent;
let DiagnosticPrivacySetting: typeof import('./diagnostic-consent').DiagnosticPrivacySetting;
let captureDiagnosticException: typeof import('@/lib/diagnostic-reports').captureDiagnosticException;
let setDiagnosticConsent: typeof import('@/lib/diagnostic-reports').setDiagnosticConsent;

beforeEach(async () => {
  vi.resetModules();
  vi.clearAllMocks();
  mock.state = { enabled: false, opted_in: false, available: true, prompted: false };
  mock.read.mockImplementation(async () => mock.state);
  mock.save.mockImplementation(async (enabled: boolean) => {
    mock.state = { ...mock.state, enabled, opted_in: enabled, prompted: true };
    return mock.state;
  });
  ({ DiagnosticConsent, DiagnosticPrivacySetting } = await import('./diagnostic-consent'));
  ({ captureDiagnosticException, setDiagnosticConsent } = await import('@/lib/diagnostic-reports'));
});
afterEach(cleanup);

it('shows a failed initial status read and retries without choosing consent', async () => {
  mock.read.mockRejectedValueOnce(new Error('private startup detail'));
  render(<DiagnosticConsent />);
  expect(await screen.findByRole('alert')).toHaveTextContent('diagnostics.save_failed');
  expect(screen.queryByText('private startup detail')).not.toBeInTheDocument();
  expect(screen.queryByRole('button', { name: 'diagnostics.allow' })).not.toBeInTheDocument();
  captureDiagnosticException(new Error('private text'), 'renderer_crash', 'render');
  expect(mock.capture).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
  expect(await screen.findByRole('button', { name: 'diagnostics.allow' })).toBeEnabled();
  expect(screen.getByRole('button', { name: 'diagnostics.keep_off' })).toBeEnabled();
  expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  expect(mock.read).toHaveBeenCalledTimes(2);
  expect(mock.save).not.toHaveBeenCalled();
});

it('offers equal explicit choices with no backend and remembers No independently of analytics', async () => {
  localStorage.setItem('voicestudio.setup.analytics-consent.v1', JSON.stringify({ enabled: true }));
  const first = render(<DiagnosticConsent />);
  const yes = await screen.findByRole('button', { name: 'diagnostics.allow' });
  const no = screen.getByRole('button', { name: 'diagnostics.keep_off' });
  expect(yes.className).toBe(no.className);
  captureDiagnosticException(new Error('private text'), 'renderer_crash', 'render');
  expect(mock.capture).not.toHaveBeenCalled();
  expect(mock.save).not.toHaveBeenCalled();
  fireEvent.click(no);
  await waitFor(() => expect(mock.save).toHaveBeenCalledWith(false));
  await waitFor(() => expect(screen.queryByTestId('diagnostic-consent')).not.toBeInTheDocument());
  first.unmount();
  render(<DiagnosticConsent />);
  await waitFor(() => expect(screen.queryByTestId('diagnostic-consent')).not.toBeInTheDocument());
  localStorage.clear();
});

it('a failed Yes keeps collection off and the choice retryable', async () => {
  mock.save.mockRejectedValue(new Error('private failure detail'));
  render(<DiagnosticConsent />);
  fireEvent.click(await screen.findByRole('button', { name: 'diagnostics.allow' }));
  expect(await screen.findByRole('alert')).toHaveTextContent('diagnostics.save_failed');
  expect(screen.getByRole('button', { name: 'diagnostics.keep_off' })).toBeEnabled();
  captureDiagnosticException(new Error('private text'), 'renderer_crash', 'render');
  expect(mock.capture).not.toHaveBeenCalled();
  expect(screen.queryByText('private failure detail')).not.toBeInTheDocument();
});

it('Privacy withdraws collection immediately before its save completes', async () => {
  mock.state = { enabled: true, opted_in: true, available: true, prompted: true };
  await setDiagnosticConsent(true);
  let finish!: (value: typeof mock.state) => void;
  mock.save.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  render(<DiagnosticPrivacySetting />);
  const toggle = screen.getByRole('switch', { name: 'diagnostics.title' });
  await waitFor(() => expect(toggle).toBeChecked());
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  captureDiagnosticException(new Error('private text'), 'renderer_crash', 'render');
  expect(mock.capture).not.toHaveBeenCalled();
  finish({ enabled: false, opted_in: false, available: true, prompted: true });
  await waitFor(() => expect(toggle).toBeEnabled());
});

it('keeps an off control available after Yes while the backend is unavailable', async () => {
  await setDiagnosticConsent(true);
  render(<DiagnosticConsent />);
  const toggle = await screen.findByRole('switch', { name: 'diagnostics.title' });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).not.toBeChecked());
  expect(screen.queryByRole('button', { name: 'diagnostics.allow' })).not.toBeInTheDocument();
});

it('Retry persists a failed withdrawal instead of merely reading old consent', async () => {
  await setDiagnosticConsent(true);
  mock.save.mockClear().mockRejectedValueOnce(new Error('disk full'));
  render(<DiagnosticPrivacySetting />);
  const toggle = screen.getByRole('switch', { name: 'diagnostics.title' });
  await waitFor(() => expect(toggle).toBeChecked());
  fireEvent.click(toggle);
  await screen.findByRole('alert');
  fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
  await waitFor(() => expect(mock.save).toHaveBeenCalledTimes(2));
  expect(mock.save).toHaveBeenNthCalledWith(1, false);
  expect(mock.save).toHaveBeenNthCalledWith(2, false);
  await waitFor(() => expect(screen.queryByRole('alert')).not.toBeInTheDocument());
});
