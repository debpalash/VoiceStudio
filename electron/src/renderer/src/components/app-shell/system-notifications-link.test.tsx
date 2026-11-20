import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, test, vi } from 'vitest';
import type { UpdateState } from '../../../../preload/index.d';
import { apiJson } from '@/lib/api/client';

/**
 * A notification's `link` action goes straight to `openExternal`, which hands
 * the string to the OS. The target is backend JSON, so it must be validated as
 * web/mail before it is acted on — the main-process allowlist refuses the same
 * schemes, and this keeps the refusal at the point that decided to act.
 */
const mocks = vi.hoisted(() => ({
  navigate: vi.fn(),
  openExternal: vi.fn(async () => {}),
  toastError: vi.fn(),
  state: {
    status: 'idle',
    currentVersion: '0.5.2',
    channel: 'stable',
    progress: 0,
  } as UpdateState,
}));

vi.mock('@tanstack/react-router', () => ({
  useNavigate: () => mocks.navigate,
  Link: ({ to, children }: { to: string; children: unknown }) => (
    <a href={to}>{children as never}</a>
  ),
}));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({
    files: { openExternal: mocks.openExternal },
    updates: {
      getState: vi.fn(async () => mocks.state),
      download: vi.fn(async () => ({})),
      install: vi.fn(async () => {}),
      onState: () => () => {},
    },
  }),
}));
vi.mock('@/lib/api/client', () => ({
  apiJson: vi.fn(async () => ({ notifications: [] })),
  apiFetch: vi.fn(),
}));
vi.mock('sonner', () => ({ toast: { error: mocks.toastError } }));
vi.mock('./download-activity', () => ({
  useDownloadActivity: () => ({ items: [], active: 0, pending: null }),
  DownloadActivityList: () => null,
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { SystemNotifications } from './system-notifications';

function renderWithTarget(target: string) {
  vi.mocked(apiJson).mockResolvedValueOnce({
    notifications: [
      {
        id: 'release-note',
        level: 'info',
        title: 'A release note',
        message: 'Read more',
        action: { type: 'link', target },
      },
    ],
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <SystemNotifications enabled />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  mocks.openExternal.mockClear();
  mocks.navigate.mockClear();
  mocks.toastError.mockClear();
});
afterEach(() => cleanup());

async function clickAction() {
  fireEvent.click(await screen.findByRole('button', { name: /Release notes|A release note/ }));
  const action = (await screen.findByText('A release note')).closest('button');
  fireEvent.click(action!);
}

test('opens an https link target in the browser', async () => {
  renderWithTarget('https://github.com/debpalash/VoiceStudio/releases');
  await clickAction();
  expect(mocks.openExternal).toHaveBeenCalledWith('https://github.com/debpalash/VoiceStudio/releases');
});

test.each([
  ['a file URL', 'file:///etc/passwd'],
  ['a Windows path', 'C:\\Windows\\System32\\calc.exe'],
  ['a POSIX path', '/etc/passwd'],
  ['a javascript URL', 'javascript:alert(1)'],
  ['a custom scheme', 'voicestudio://run'],
])('does not act on %s', async (_label, target) => {
  renderWithTarget(target);
  await clickAction();
  expect(mocks.openExternal).not.toHaveBeenCalled();
  expect(mocks.navigate).not.toHaveBeenCalled();
  expect(mocks.toastError).toHaveBeenCalledWith('common.link_blocked');
});
