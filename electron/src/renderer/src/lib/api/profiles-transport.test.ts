import { QueryClient } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
import { listProfiles } from './profiles';

afterEach(() => {
  vi.unstubAllGlobals();
});

it('aborts a stalled profiles transport when its managed query is cancelled', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  let signal: AbortSignal | undefined;
  vi.stubGlobal(
    'fetch',
    vi.fn((_url: string, init?: RequestInit) => {
      signal = init?.signal ?? undefined;
      return new Promise<Response>((_resolve, reject) => {
        signal?.addEventListener('abort', () =>
          reject(new DOMException('Cancelled', 'AbortError')),
        );
      });
    }),
  );
  try {
    const pending = client
      .fetchQuery({ queryKey: ['profiles'], queryFn: listProfiles })
      .catch((error: unknown) => error);
    expect(signal).toBeDefined();
    await client.cancelQueries({ queryKey: ['profiles'] });
    await pending;
    expect(signal?.aborted).toBe(true);
    vi.stubGlobal(
      'fetch',
      vi.fn(async () => new Response('[]', { status: 200 })),
    );
    await expect(
      client.fetchQuery({ queryKey: ['profiles'], queryFn: listProfiles }),
    ).resolves.toEqual([]);
  } finally {
    client.clear();
  }
});
