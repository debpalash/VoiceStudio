import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({
  bridge: null as null | { terminal?: { status: unknown; addToPath: unknown } },
  status: vi.fn(),
  addToPath: vi.fn(),
  success: vi.fn(),
  error: vi.fn(),
}));

vi.mock('@/components/bridge', () => ({ getBridge: () => mock.bridge }));
vi.mock('sonner', () => ({ toast: { success: mock.success, error: mock.error } }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { TerminalCommandSettings } from './terminal-command-settings';

const command = '/home/user/.local/bin/voicestudio';
const status = (patch: object) => ({
  supported: true,
  linked: true,
  onPath: false,
  command,
  ...patch,
});

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function withBridge() {
  mock.bridge = { terminal: { status: mock.status, addToPath: mock.addToPath } };
}

it('changes shell profiles only when the user asks', async () => {
  withBridge();
  mock.status.mockResolvedValue(status({}));
  mock.addToPath.mockResolvedValue(status({ onPath: true }));
  render(<TerminalCommandSettings />);
  const add = await screen.findByRole('button', { name: 'settings.terminal_command' });
  expect(mock.addToPath).not.toHaveBeenCalled();
  fireEvent.click(add);
  await screen.findByText('settings.terminal_command_ready');
  expect(mock.addToPath).toHaveBeenCalledOnce();
  expect(mock.success).toHaveBeenCalledWith('settings.terminal_command_added');
});

it('explains a command owned by another installation instead of offering to replace it', async () => {
  withBridge();
  mock.status.mockResolvedValue(status({ linked: false }));
  render(<TerminalCommandSettings />);
  await screen.findByText('settings.terminal_command_conflict');
  expect(screen.getByRole('button', { name: 'settings.terminal_command' })).toBeDisabled();
});

it('is hidden where installers or stores own the command', async () => {
  withBridge();
  mock.status.mockResolvedValue(status({ supported: false }));
  const { container } = render(<TerminalCommandSettings />);
  await waitFor(() => expect(mock.status).toHaveBeenCalled());
  expect(container).toBeEmptyDOMElement();
  mock.bridge = null;
  cleanup();
  expect(render(<TerminalCommandSettings />).container).toBeEmptyDOMElement();
});

it('turns a filesystem failure into a message the user can act on', async () => {
  withBridge();
  mock.status.mockResolvedValue(status({}));
  mock.addToPath.mockRejectedValue(
    new Error("EACCES: permission denied, open '/home/user/.profile'"),
  );
  render(<TerminalCommandSettings />);
  fireEvent.click(await screen.findByRole('button', { name: 'settings.terminal_command' }));
  await waitFor(() => expect(mock.error).toHaveBeenCalledWith('settings.terminal_command_failed'));
  expect(screen.getByRole('button', { name: 'settings.terminal_command' })).toBeEnabled();
});
