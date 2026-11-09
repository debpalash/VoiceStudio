import { cleanup, fireEvent, render, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn() }));

vi.mock('@/lib/api/client', async (load) => {
  const actual = await load<typeof import('@/lib/api/client')>();
  return { ...actual, apiJson: mock.api };
});
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

import { CredentialSettings } from './credential-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

function renderSettings() {
  mock.api.mockImplementation((path: string) => {
    if (path === '/api/settings/hf-token/state')
      return Promise.resolve({ active: null, sources: [] });
    return Promise.resolve({ ok: true });
  });
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={client}>
      <CredentialSettings />
    </QueryClientProvider>,
  );
  return client;
}

it('marks the HuggingFace token input with a placeholder', () => {
  renderSettings();
  const input = document.getElementById('hf-token-input') as HTMLInputElement;
  expect(input.placeholder).toBe('hf_…');
  expect(document.querySelector('label[for="hf-token-input"]')?.textContent).toBe(
    'settings.hf_token_input',
  );
});

it('saves each provider with one Save, enabled only after typing', async () => {
  const client = renderSettings();
  const invalidate = vi.spyOn(client, 'invalidateQueries');
  // One Save per provider: Google Cloud, DeepL, Microsoft Azure, AWS.
  expect(document.querySelectorAll('form button[type="submit"]')).toHaveLength(4 + 1);

  const deepL = document.querySelector<HTMLInputElement>(
    'input[aria-label="DeepL · settings.llmp_api_key"]',
  )!;
  const deepLSave = within(deepL.closest('form')!).getByRole('button', { name: 'common.save' });
  expect(deepLSave).toBeDisabled();
  expect(mock.api).not.toHaveBeenCalledWith('/system/set-env', expect.anything());

  fireEvent.change(deepL, { target: { value: '  deepl-secret  ' } });
  expect(deepLSave).toBeEnabled();
  fireEvent.click(deepLSave);
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/system/set-env', {
      method: 'POST',
      body: JSON.stringify({ key: 'DEEPL_API_KEY', value: 'deepl-secret' }),
    }),
  );
  // Untouched fields of the same provider are not overwritten with blanks.
  expect(mock.api).not.toHaveBeenCalledWith(
    '/system/set-env',
    expect.objectContaining({ body: expect.stringContaining('DEEPL_BASE_URL') }),
  );

  const microsoft = document.querySelector<HTMLInputElement>(
    'input[aria-label="Microsoft Azure · credentials.base_url"]',
  )!;
  fireEvent.change(microsoft, { target: { value: '  https://translator.example  ' } });
  fireEvent.click(within(microsoft.closest('form')!).getByRole('button', { name: 'common.save' }));
  await waitFor(() =>
    expect(mock.api).toHaveBeenCalledWith('/system/set-env', {
      method: 'POST',
      body: JSON.stringify({ key: 'MICROSOFT_BASE_URL', value: 'https://translator.example' }),
    }),
  );
  expect(invalidate).toHaveBeenCalledWith({ queryKey: ['translation-engines'] });
});

it('shows a valid and an invalid badge for tested tokens', async () => {
  mock.api.mockImplementation((path: string) =>
    Promise.resolve(
      path === '/api/settings/hf-token/state'
        ? {
            active: 'app',
            sources: [
              { source: 'app', set: true, whoami_ok: true, whoami_user: 'ada' },
              { source: 'env', set: true, whoami_ok: false },
            ],
          }
        : { ok: true },
    ),
  );
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const view = render(
    <QueryClientProvider client={client}>
      <CredentialSettings />
    </QueryClientProvider>,
  );
  await waitFor(() => expect(within(view.container).getByText('ada')).toBeTruthy());
  expect(within(view.container).getByText('settings.hf_token_whoami_failed')).toBeTruthy();
});
