import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.api }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { PauseFillSettings } from './pause-fill-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderSettings() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PauseFillSettings />
    </QueryClientProvider>,
  );
}

it('turns room tone on and off through the settings route', async () => {
  let mode = 'silence';
  mock.api.mockImplementation((_path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') mode = JSON.parse(String(init.body)).mode;
    return Promise.resolve({ mode, env_pinned: false });
  });
  renderSettings();
  const toggle = await screen.findByRole('switch', { name: 'pauseFill.room_tone' });
  await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled', 'true'));
  fireEvent.click(toggle);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/api/settings/pause-fill',
      expect.objectContaining({ method: 'PUT', body: JSON.stringify({ mode: 'room_tone' }) }),
    ),
  );
  await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
});

it('is locked while the environment variable decides', async () => {
  mock.api.mockResolvedValue({ mode: 'room_tone', env_pinned: true });
  renderSettings();
  expect(await screen.findByText('pauseFill.env_pinned')).toBeTruthy();
  expect(screen.getByRole('switch', { name: 'pauseFill.room_tone' })).toHaveAttribute(
    'aria-disabled',
    'true',
  );
});

it('Retry repeats a save that failed', async () => {
  let fail = true;
  mock.api.mockImplementation((_path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') {
      if (fail) return Promise.reject(new Error('offline'));
      return Promise.resolve({ mode: 'room_tone', env_pinned: false });
    }
    return Promise.resolve({ mode: 'silence', env_pinned: false });
  });
  renderSettings();
  const toggle = await screen.findByRole('switch', { name: 'pauseFill.room_tone' });
  await waitFor(() => expect(toggle).not.toHaveAttribute('aria-disabled', 'true'));
  fireEvent.click(toggle);
  expect(await screen.findByRole('alert')).toBeTruthy();
  fail = false;
  fireEvent.click(screen.getByRole('button', { name: 'common.retry' }));
  await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
  const puts = mock.api.mock.calls.filter(([, init]) => init?.method === 'PUT');
  expect(puts).toHaveLength(2);
  expect(puts[1][1].body).toBe(JSON.stringify({ mode: 'room_tone' }));
});
