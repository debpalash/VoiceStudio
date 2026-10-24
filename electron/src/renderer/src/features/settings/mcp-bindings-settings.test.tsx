import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/lib/api/client', async (load) => {
  const actual = await load<typeof import('@/lib/api/client')>();
  return { ...actual, apiJson: mock.api };
});
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({ data: [{ id: 'morgan', name: 'Morgan' }], isPending: false }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));

import { McpBindingsSettings } from './mcp-bindings-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it('clears the bound voice and label when an existing client is saved with Default voice', async () => {
  mock.api.mockImplementation((_path: string, init?: RequestInit) =>
    Promise.resolve(
      init?.method === 'PUT'
        ? JSON.parse(String(init.body))
        : [
            {
              client_id: 'claude-code',
              label: 'Claude',
              profile_id: 'morgan',
              default_engine: null,
            },
          ],
    ),
  );
  render(
    <QueryClientProvider client={new QueryClient()}>
      <McpBindingsSettings />
    </QueryClientProvider>,
  );
  await screen.findByText('Morgan');

  fireEvent.change(screen.getByLabelText('settings.mcp_client_id'), {
    target: { value: 'claude-code' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'settings.mcp_add' }));

  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith(
      '/api/mcp/bindings',
      expect.objectContaining({ method: 'PUT' }),
    ),
  );
  const put = mock.api.mock.calls.find(([, init]) => init?.method === 'PUT');
  // null means "keep the stored value" to the backend; "" clears it.
  expect(JSON.parse(String(put?.[1]?.body))).toEqual({
    client_id: 'claude-code',
    label: '',
    profile_id: '',
  });
});
