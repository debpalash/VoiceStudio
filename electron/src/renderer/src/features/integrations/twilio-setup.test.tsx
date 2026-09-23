import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import '@/i18n';
import { ApiError } from '@/lib/api/client';
import { TwilioSetup, type TwilioState } from './twilio-setup';

const api = vi.hoisted(() => ({ json: vi.fn(), fetch: vi.fn() }));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: api.json,
  apiFetch: api.fetch,
}));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({ data: [{ id: 'p1', name: 'Front desk' }] }),
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    data: {
      tts: {
        active: 'omnivoice',
        backends: [{ id: 'omnivoice', display_name: 'OmniVoice', available: true }],
      },
    },
  }),
}));
const toast = vi.hoisted(() => ({ success: vi.fn(), error: vi.fn() }));
vi.mock('sonner', () => ({ toast }));

const base: TwilioState = {
  enabled: false,
  account_sid: '',
  has_auth_token: false,
  public_base_url: '',
  voice_id: '',
  engine: '',
  language: '',
  greeting: '',
  webhook_url: '',
  missing: ['account_sid', 'auth_token', 'public_base_url', 'greeting'],
  listener: { running: false, port: null, tunnel_target: null },
  calls: { active: 0, max_concurrent: 2, recent: [] },
  limits: { max_greeting_chars: 1000 },
};

function renderPage(state: TwilioState) {
  api.json.mockImplementation(async (path: string) => {
    if (path.endsWith('/state')) return state;
    throw new Error(`unexpected ${path}`);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <TwilioSetup />
    </QueryClientProvider>,
  );
}

beforeEach(() => vi.resetAllMocks());
afterEach(cleanup);

it('is off by default, warns about exposure and makes no network call beyond local state', async () => {
  renderPage(base);
  expect(await screen.findByRole('switch', { name: 'Answer calls' })).not.toBeChecked();
  expect(screen.getByText('Phone calls with Twilio')).toBeInTheDocument();
  expect(screen.getByText(/opens a separate local port/)).toBeInTheDocument();
  expect(screen.getByText('Not listening')).toBeInTheDocument();
  expect(screen.getByText('No calls yet.')).toBeInTheDocument();
  expect(api.json).toHaveBeenCalledTimes(1);
  expect(api.fetch).not.toHaveBeenCalled();
});

it('saves the form without echoing a stored token and reports missing fields', async () => {
  renderPage({ ...base, has_auth_token: true });
  const token = await screen.findByPlaceholderText('Saved. Leave empty to keep it.');
  expect(token).toHaveValue('');
  fireEvent.change(screen.getByPlaceholderText('AC…'), { target: { value: 'AC123' } });
  api.json.mockRejectedValueOnce(
    new ApiError(400, 'Complete the setup first', {
      detail: { code: 'incomplete', missing: ['public_base_url', 'greeting'] },
    }),
  );
  fireEvent.click(screen.getByRole('switch', { name: 'Answer calls' }));
  expect(
    await screen.findByText('Complete these fields first: Public tunnel URL, Greeting'),
  ).toBeInTheDocument();
  const [path, init] = api.json.mock.calls.find(([p]) => p.endsWith('/config'))!;
  expect(path).toBe('/api/integrations/twilio/config');
  const sent = JSON.parse(init.body);
  expect(sent).toMatchObject({ enabled: true, account_sid: 'AC123' });
  expect(sent).not.toHaveProperty('auth_token');
});

it('shows the webhook URL, tunnel target and localized call outcomes when enabled', async () => {
  renderPage({
    ...base,
    enabled: true,
    missing: [],
    webhook_url: 'https://x.trycloudflare.com/integrations/twilio/voice',
    listener: { running: true, port: 3950, tunnel_target: 'http://127.0.0.1:3950' },
    calls: {
      active: 1,
      max_concurrent: 2,
      recent: [
        {
          call: '…ab12',
          started_at: 1_700_000_000,
          ended_at: 1_700_000_004,
          outcome: 'completed',
          audio_seconds: 3.2,
        },
        {
          call: '…',
          started_at: 1_700_000_000,
          ended_at: 1_700_000_000,
          outcome: 'rejected_signature',
          audio_seconds: 0,
        },
        {
          call: '…',
          started_at: 1_700_000_000,
          ended_at: null,
          outcome: 'something_new',
          audio_seconds: 0,
        },
      ],
    },
  });
  expect(
    await screen.findByText('https://x.trycloudflare.com/integrations/twilio/voice'),
  ).toBeInTheDocument();
  expect(screen.getByText('Listening on http://127.0.0.1:3950')).toBeInTheDocument();
  expect(screen.getByText('1 of 2 lines in use')).toBeInTheDocument();
  expect(screen.getByText('Completed')).toBeInTheDocument();
  expect(screen.getByText('Rejected: invalid signature')).toBeInTheDocument();
  expect(screen.getByText('Error')).toBeInTheDocument();
  expect(screen.getByText('3.2 s audio')).toBeInTheDocument();
});

it('plays the phone-quality local test only when asked', async () => {
  const createObjectURL = vi.fn(() => 'blob:preview');
  Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
  api.fetch.mockResolvedValue({ blob: async () => new Blob(['RIFF'], { type: 'audio/wav' }) });
  renderPage({ ...base, greeting: 'Hello caller', voice_id: 'p1' });
  const button = await screen.findByRole('button', { name: 'Test locally' });
  expect(api.fetch).not.toHaveBeenCalled();
  fireEvent.click(button);
  await waitFor(() => expect(api.fetch).toHaveBeenCalledTimes(1));
  const [path, init] = api.fetch.mock.calls[0];
  expect(path).toBe('/api/integrations/twilio/test');
  expect(JSON.parse(init.body)).toEqual({ text: 'Hello caller', voice_id: 'p1', engine: '' });
  await waitFor(() =>
    expect(document.querySelector('audio')?.getAttribute('src')).toBe('blob:preview'),
  );
});

it('turns off with only the switch, so an invalid draft cannot block disabling', async () => {
  renderPage({ ...base, enabled: true, missing: [] });
  fireEvent.change(await screen.findByPlaceholderText('AC…'), { target: { value: 'not-a-sid' } });
  api.json.mockResolvedValueOnce({ ...base, enabled: false });
  fireEvent.click(screen.getByRole('switch', { name: 'Answer calls' }));
  await waitFor(() =>
    expect(api.json).toHaveBeenCalledWith('/api/integrations/twilio/config', {
      method: 'PUT',
      body: JSON.stringify({ enabled: false }),
    }),
  );
  expect(screen.getByPlaceholderText('AC…')).toHaveValue('not-a-sid');
});

it('removes the Auth Token without submitting an invalid draft, turning calls off with it', async () => {
  renderPage({ ...base, enabled: true, has_auth_token: true, missing: [] });
  fireEvent.change(await screen.findByPlaceholderText('AC…'), { target: { value: 'not-a-sid' } });
  const token = screen.getByPlaceholderText('Saved. Leave empty to keep it.');
  fireEvent.change(token, { target: { value: 'typed-but-unsaved' } });
  api.json.mockResolvedValueOnce({ ...base });
  fireEvent.click(screen.getByRole('button', { name: 'Remove Auth Token' }));
  await waitFor(() =>
    expect(api.json).toHaveBeenCalledWith('/api/integrations/twilio/config', {
      method: 'PUT',
      body: JSON.stringify({ auth_token: '', enabled: false }),
    }),
  );
  expect(screen.getByPlaceholderText('AC…')).toHaveValue('not-a-sid');
  // The typed token draft is cleared, so a later Save cannot restore it.
  await waitFor(() => expect(token).toHaveValue(''));
  api.json.mockResolvedValueOnce({ ...base });
  fireEvent.click(screen.getByRole('button', { name: 'Save' }));
  await waitFor(() => expect(api.json.mock.calls.at(-1)?.[1]?.body).toBeDefined());
  expect(JSON.parse(api.json.mock.calls.at(-1)![1].body)).not.toHaveProperty('auth_token');
});

it('keeps the form read-only while an update is in flight and keeps later edits', async () => {
  renderPage({ ...base, has_auth_token: true, greeting: 'Hello' });
  const greeting = await screen.findByPlaceholderText(
    'Thanks for calling. We will get back to you soon.',
  );
  let finish: (value: TwilioState) => void = () => {};
  api.json.mockImplementationOnce(() => new Promise<TwilioState>((resolve) => (finish = resolve)));
  fireEvent.click(screen.getByRole('button', { name: 'Remove Auth Token' }));
  await waitFor(() => expect(greeting).toBeDisabled());
  expect(screen.getByPlaceholderText('AC…')).toBeDisabled();
  finish({ ...base, greeting: 'Hello' });
  await waitFor(() => expect(greeting).toBeEnabled());
  fireEvent.change(greeting, { target: { value: 'Edited after removal' } });
  expect(greeting).toHaveValue('Edited after removal');
});
