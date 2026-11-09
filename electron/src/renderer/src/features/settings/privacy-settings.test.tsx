import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactNode } from 'react';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/lib/api/client', async (load) => {
  const actual = await load<typeof import('@/lib/api/client')>();
  return { ...actual, apiJson: mock.api };
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ children, className }: { children: ReactNode; className?: string }) => (
    <a className={className}>{children}</a>
  ),
}));

import { NetworkSettings } from './network-settings';
import { PrivacySettings } from './privacy-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderWith(node: ReactNode) {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
}

it('labels the translator status and its link plainly', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/system/info'
        ? { translate_provider: 'argos' }
        : path === '/api/settings/history-retention'
          ? { cap: 50, default: 50 }
          : {},
    ),
  );
  renderWith(<PrivacySettings showAnalytics={false} />);
  expect(await screen.findByText('privacy.translator_offline')).toBeInTheDocument();
  expect(screen.getByText('privacy.change_translator')).toBeInTheDocument();
});

it('enables Save for takes-to-keep only after the value changes', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(path === '/api/settings/history-retention' ? { cap: 50, default: 50 } : {}),
  );
  renderWith(<PrivacySettings showAnalytics={false} />);
  const input = await screen.findByRole('spinbutton', { name: 'settings.history_retention_cap' });
  await waitFor(() => expect(input).toHaveValue(50));
  const save = screen.getByRole('button', { name: 'common.save' });
  expect(save).toBeDisabled();
  fireEvent.change(input, { target: { value: '80' } });
  expect(save).toBeEnabled();
  fireEvent.change(input, { target: { value: '50' } });
  expect(save).toBeDisabled();
});

it('enables the proxy Save only after the proxy changes', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(path === '/system/info' ? { proxy_url: 'http://proxy:8080' } : {}),
  );
  renderWith(<NetworkSettings />);
  const input = await screen.findByRole('textbox', { name: 'settings.proxy' });
  await waitFor(() => expect(input).toHaveValue('http://proxy:8080'));
  const save = screen.getByRole('button', { name: 'common.save' });
  expect(save).toBeDisabled();
  fireEvent.change(input, { target: { value: 'http://other:8080' } });
  expect(save).toBeEnabled();
});
