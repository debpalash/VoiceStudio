import { act, cleanup, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

vi.mock('@/components/bridge', () => ({ getBridge: () => null }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { GithubStar } from './github-star';

const REFRESH_MS = 20 * 60 * 1000;

function mount() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(
    <QueryClientProvider client={client}>
      <GithubStar />
    </QueryClientProvider>,
  );
  return client;
}

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  vi.unstubAllEnvs();
});

it('keeps the preview badge offline when requested', async () => {
  vi.stubEnv('VITE_PREVIEW_OFFLINE', '1');
  const fetchCount = vi.fn();
  vi.stubGlobal('fetch', fetchCount);
  const client = mount();
  expect(screen.getByText('43,638')).toBeVisible();
  expect(fetchCount).not.toHaveBeenCalled();
  client.clear();
});

it('shows an exact live count and refreshes it every 20 minutes', async () => {
  const fetchCount = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ stargazers_count: 43_768 }) })
    .mockResolvedValueOnce({ ok: true, json: async () => ({ stargazers_count: 43_769 }) });
  vi.stubGlobal('fetch', fetchCount);
  vi.useFakeTimers();
  const client = mount();

  expect(screen.getByText('43,638')).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText('43,768')).toBeVisible();
  expect(fetchCount).toHaveBeenCalledOnce();
  expect(fetchCount.mock.calls[0][0]).toBe(
    'https://api.github.com/repos/debpalash/VoiceStudio',
  );
  expect(fetchCount.mock.calls[0][1]).toMatchObject({
    credentials: 'omit',
    referrerPolicy: 'no-referrer',
  });
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS - 2);
  });
  expect(fetchCount).toHaveBeenCalledOnce();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2);
  });
  expect(fetchCount).toHaveBeenCalledTimes(2);
  vi.useRealTimers();
  await waitFor(() => expect(screen.getByText('43,769')).toBeVisible());
  expect(screen.getByRole('link', { name: 'support.star_github' })).toHaveAttribute(
    'href',
    'https://github.com/debpalash/VoiceStudio',
  );
  client.clear();
});

it('keeps the last live count when a later request fails', async () => {
  const fetchCount = vi
    .fn()
    .mockResolvedValueOnce({ ok: true, json: async () => ({ stargazers_count: 45_001 }) })
    .mockRejectedValueOnce(new Error('offline'));
  vi.stubGlobal('fetch', fetchCount);
  vi.useFakeTimers();
  const client = mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText('45,001')).toBeVisible();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(REFRESH_MS + 1);
  });
  expect(fetchCount).toHaveBeenCalledTimes(2);
  expect(screen.getByText('45,001')).toBeVisible();
  client.clear();
});

it('shows the bundled count if the first request is offline', async () => {
  vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')));
  vi.useFakeTimers();
  const client = mount();
  await act(async () => {
    await vi.advanceTimersByTimeAsync(1);
  });
  expect(screen.getByText('43,638')).toBeVisible();
  client.clear();
});
