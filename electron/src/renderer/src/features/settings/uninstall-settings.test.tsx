import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import type { UninstallTarget } from '../../../../preload/index.d';

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
    t: (key: string, values?: { size?: string }) =>
      key === 'settings.uninstall_confirm_word'
        ? 'DELETE'
        : key === 'settings.uninstall_total'
          ? `${key}:${values?.size}`
          : key,
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
});

it('counts covered bytes only when their covering parent is selected', () => {
  const parent: UninstallTarget = {
    key: 'env',
    path: 'E:\\profile-label',
    size_bytes: 9,
    exists: true,
    shared: true,
  };
  const child: UninstallTarget = {
    key: 'models',
    path: 'D:\\model-label',
    size_bytes: 5,
    exists: true,
    shared: true,
    covered_by: [parent.path],
  };
  expect(uninstallBytes([parent, child], false)).toBe(0);
  expect(uninstallBytes([parent, child], true)).toBe(9);
  expect(uninstallBytes([child, parent], true)).toBe(9);
  expect(uninstallBytes([child], true)).toBe(5);
  expect(uninstallBytes([{ ...parent, exists: false }, child], true)).toBe(5);
  expect(uninstallBytes([parent, { ...child, covered_by: undefined }], true)).toBe(14);
  expect(uninstallBytes([child, { ...parent, shared: false }], false)).toBe(9);
});

it('updates the real checkbox estimate without counting the nested cache twice', async () => {
  const parent: UninstallTarget = {
    key: 'env',
    path: 'E:\\profile-label',
    size_bytes: 9 * 1024,
    exists: true,
    shared: true,
  };
  const child: UninstallTarget = {
    key: 'models',
    path: 'D:\\model-label',
    size_bytes: 5 * 1024,
    exists: true,
    shared: true,
    covered_by: [parent.path],
  };
  mock.scan.mockResolvedValue([parent, child]);
  render(<UninstallSettings />);
  await screen.findByText(child.path);
  expect(screen.getByText('settings.uninstall_total:0 B')).toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByText('settings.uninstall_total:9 KB')).toBeInTheDocument();
  expect(screen.queryByText('settings.uninstall_total:14 KB')).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('checkbox'));
  expect(screen.getByText('settings.uninstall_total:0 B')).toBeInTheDocument();
});
