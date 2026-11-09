import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  scan: vi.fn(),
  purge: vi.fn(() => new Promise(() => undefined)),
  toastError: vi.fn(),
}));

vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    maintenance: { scanUninstall: mock.scan, purgeUninstall: mock.purge },
  }),
}));
vi.mock('sonner', () => ({ toast: { error: mock.toastError } }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string) => (key === 'settings.uninstall_confirm_word' ? 'DELETE' : key),
  }),
}));

import { UninstallSettings, uninstallBytes } from './uninstall-settings';

const targets = [
  { key: 'data' as const, path: 'C:\\OmniVoice', size_bytes: 1024, exists: true, shared: false },
  { key: 'env' as const, path: 'C:\\VoiceStudio', size_bytes: 2048, exists: true, shared: false },
  {
    key: 'models' as const,
    path: 'C:\\Users\\pal\\.cache\\huggingface',
    size_bytes: 8192,
    exists: true,
    shared: true,
  },
];

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('keeps shared models out of the default purge and gates it on typed confirmation', async () => {
  mock.scan.mockResolvedValue(targets);
  render(<UninstallSettings />);
  await screen.findByText('C:\\OmniVoice');
  expect(uninstallBytes(targets, false)).toBe(3072);
  expect(uninstallBytes(targets, true)).toBe(11_264);

  fireEvent.click(screen.getByRole('button', { name: 'settings.uninstall' }));
  const confirm = await screen.findByRole('button', { name: 'settings.uninstall_confirm' });
  expect(confirm).toBeDisabled();
  fireEvent.change(screen.getByRole('textbox'), { target: { value: 'DELETE' } });
  expect(confirm).toBeEnabled();
  fireEvent.click(confirm);

  await waitFor(() => expect(mock.purge).toHaveBeenCalledWith(false));
});

it('passes the explicit shared-cache opt-in to native cleanup', async () => {
  mock.scan.mockResolvedValue(targets);
  render(<UninstallSettings />);
  await screen.findByText('C:\\OmniVoice');
  fireEvent.click(screen.getByRole('checkbox'));
  fireEvent.click(screen.getByRole('button', { name: 'settings.uninstall' }));
  fireEvent.change(await screen.findByRole('textbox'), { target: { value: 'delete' } });
  fireEvent.click(screen.getByRole('button', { name: 'settings.uninstall_confirm' }));
  await waitFor(() => expect(mock.purge).toHaveBeenCalledWith(true));
});

it('does not double-count a nested shared cache in the estimate', () => {
  const overlapping = [
    {
      key: 'data' as const,
      path: 'D:\\VoiceStudio',
      size_bytes: 10_000,
      exists: true,
      shared: true,
    },
    {
      key: 'models' as const,
      path: 'D:\\VoiceStudio\\models',
      size_bytes: 8_000,
      exists: true,
      shared: true,
    },
  ];
  expect(uninstallBytes(overlapping, false)).toBe(0);
  expect(uninstallBytes(overlapping, true)).toBe(10_000);
  // The same folder listed twice counts once, not zero times (#3512).
  const duplicate = [overlapping[0], { ...overlapping[0], key: 'env' as const, shared: false }];
  expect(uninstallBytes(duplicate, true)).toBe(10_000);
});
