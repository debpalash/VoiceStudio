import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  bridge: null as null | { app?: Record<string, unknown> },
  get: vi.fn(),
  set: vi.fn(),
  toastError: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { error: mock.toastError } }));

vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { CloseToTraySettings } from './close-to-tray-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
  mock.bridge = null;
});

const withBridge = () => {
  mock.bridge = { app: { getCloseToTray: mock.get, setCloseToTray: mock.set } };
};

it('is hidden in the browser UI, which has no window to hide', () => {
  const { container } = render(<CloseToTraySettings />);
  expect(container).toBeEmptyDOMElement();
});

it('shows the saved choice and saves a change through the main process', async () => {
  withBridge();
  mock.get.mockResolvedValue(false);
  mock.set.mockResolvedValue(true);
  render(<CloseToTraySettings />);
  const toggle = await screen.findByRole('switch', { name: 'settings.close_to_tray' });
  expect(toggle).not.toBeChecked();
  expect(screen.getByText('settings.close_to_tray_desc')).toBeTruthy();
  fireEvent.click(toggle);
  await waitFor(() => expect(mock.set).toHaveBeenCalledWith(true));
  await waitFor(() => expect(toggle).toBeChecked());
});

it('keeps the confirmed value and tells the user when the main process cannot save', async () => {
  withBridge();
  mock.get.mockResolvedValue(true);
  mock.set.mockRejectedValue(new Error('untrusted'));
  render(<CloseToTraySettings />);
  const toggle = await screen.findByRole('switch', { name: 'settings.close_to_tray' });
  expect(toggle).toBeChecked();
  fireEvent.click(toggle);
  await waitFor(() => expect(toggle).toBeChecked());
  expect(mock.set).toHaveBeenCalledWith(false);
  await waitFor(() =>
    expect(mock.toastError).toHaveBeenCalledWith('settings.close_to_tray_failed'),
  );
  expect(toggle).toBeChecked();
});
