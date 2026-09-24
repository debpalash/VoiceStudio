import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { ReactNode } from 'react';
import '@/i18n';
import { ApiError } from '@/lib/api/client';
import {
  TwilioSetup,
  type CallsSettings,
  type ReadinessItem,
  type TwilioState,
} from './twilio-setup';

const api = vi.hoisted(() => ({ json: vi.fn(), fetch: vi.fn() }));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: api.json,
  apiFetch: api.fetch,
}));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({
    data: [
      { id: 'p1', name: 'Front desk', verified_own_voice: 0 },
      { id: 'p2', name: 'Me', verified_own_voice: 1 },
      { id: 'p3', name: 'Designed', kind: 'design', verified_own_voice: 0 },
    ],
  }),
}));
vi.mock('@/hooks/use-engines', () => ({
  useEngines: () => ({
    activeTts: { id: 'omnivoice', display_name: 'OmniVoice', available: true },
    data: {
      tts: {
        active: 'omnivoice',
        backends: [{ id: 'omnivoice', display_name: 'OmniVoice', available: true }],
      },
    },
  }),
}));
const router = vi.hoisted(() => ({ routesByPath: {} as Record<string, unknown> }));
vi.mock('@tanstack/react-router', () => ({ useRouter: () => router }));
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
  listener: { running: false, port: null, preferred_port: 3951, tunnel_target: null },
  calls: { active: 0, max_concurrent: 2, recent: [] },
  limits: { max_greeting_chars: 1000 },
};
const complete: TwilioState = {
  ...base,
  account_sid: `AC${'a'.repeat(32)}`,
  has_auth_token: true,
  public_base_url: 'https://x.trycloudflare.com',
  webhook_url: 'https://x.trycloudflare.com/integrations/twilio/voice',
  greeting: 'Hello caller',
  missing: [],
};
const SID = `AC${'b'.repeat(32)}`;

const notFound = () => new ApiError(404, 'Not Found');
let put: (path: string, body: Record<string, unknown>) => Promise<unknown>;
let calls: CallsSettings | null;
let readiness: ReadinessItem[] | null;

function renderPage(state: TwilioState) {
  api.json.mockImplementation(async (path: string, init?: RequestInit) => {
    if (init?.method === 'PUT') return put(path, JSON.parse(String(init.body)));
    if (path.endsWith('/state')) return state;
    if (path === '/calls/settings') {
      if (calls) return calls;
      throw notFound();
    }
    if (path === '/calls/readiness') {
      if (readiness) return readiness;
      throw notFound();
    }
    throw new Error(`unexpected ${path}`);
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  const hero = ({ status, action }: { status?: ReactNode; action?: ReactNode }) => (
    <header data-testid="hero">
      {status}
      {action}
    </header>
  );
  return render(
    <QueryClientProvider client={client}>
      <TwilioSetup hero={hero} rail={<div>Generic rail</div>} />
    </QueryClientProvider>,
  );
}

const puts = () =>
  api.json.mock.calls
    .filter(([, init]) => init?.method === 'PUT')
    .map(([path, init]) => [path, JSON.parse(String(init.body))]);
async function loadedHero() {
  await screen.findByRole('heading', { name: 'Twilio account', level: 3 });
  return screen.getByTestId('hero');
}
const step = (name: string) =>
  screen.getByRole('heading', { name, level: 3 }).closest('section') as HTMLElement;

beforeEach(() => {
  vi.resetAllMocks();
  router.routesByPath = {};
  calls = null;
  readiness = null;
  put = async () => base;
});
afterEach(cleanup);

it('starts off with every step to do, a clear next action and no network beyond local state', async () => {
  renderPage(base);
  const hero = await loadedHero();
  expect(within(hero).getByRole('status')).toHaveTextContent('Not set up');
  expect(within(hero).getByRole('button', { name: /Continue setup/ })).toBeInTheDocument();
  expect(within(step('Twilio account')).getByText('To do')).toBeInTheDocument();
  expect(within(step('Public tunnel')).getByText('To do')).toBeInTheDocument();
  expect(within(step('Phone number')).getByText('Waiting')).toBeInTheDocument();
  expect(within(step('Voice and behavior')).getByText('To do')).toBeInTheDocument();
  expect(screen.getByText('No calls yet.')).toBeInTheDocument();
  expect(screen.getByText('Generic rail')).toBeInTheDocument();
  expect(api.fetch).not.toHaveBeenCalled();
  expect(puts()).toEqual([]);
});

it('labels select sentinels instead of showing raw values', async () => {
  renderPage(base);
  expect(await screen.findByRole('combobox', { name: 'Voice' })).toHaveTextContent('Default voice');
  expect(screen.getByRole('combobox', { name: 'Engine' })).toHaveTextContent(
    'Active engine (OmniVoice)',
  );
  expect(document.body.textContent).not.toContain('__default__');
});

it('marks voices verified as your own in the voice picker', async () => {
  renderPage(base);
  fireEvent.click(await screen.findByRole('combobox', { name: 'Voice' }));
  const own = await screen.findByRole('option', { name: /Me/ });
  expect(own).toHaveTextContent('Can call');
  expect(screen.getByRole('option', { name: /Designed/ })).toHaveTextContent('Can call');
  expect(screen.getByRole('option', { name: 'Front desk' })).not.toHaveTextContent('Can call');
});

it('saves only the account step, never echoes a stored token, and replaces it on request', async () => {
  put = async () => complete;
  renderPage({ ...base, has_auth_token: true });
  expect(await screen.findByText(/Saved on this computer/)).toBeInTheDocument();
  expect(screen.queryByLabelText('Auth Token')).toBeNull();
  fireEvent.change(screen.getByLabelText('Account SID'), { target: { value: SID } });
  fireEvent.change(screen.getByLabelText('Public tunnel URL'), {
    target: { value: 'https://draft.example' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Save and check' }));
  await waitFor(() => expect(puts()).toHaveLength(1));
  expect(puts()[0]).toEqual(['/api/integrations/twilio/config', { account_sid: SID }]);
  // Another step's unsaved draft survives this step's save.
  expect(screen.getByLabelText('Public tunnel URL')).toHaveValue('https://draft.example');

  fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
  fireEvent.change(screen.getByLabelText('Auth Token'), { target: { value: 'new-token' } });
  fireEvent.click(screen.getByRole('button', { name: 'Save and check' }));
  await waitFor(() => expect(puts()).toHaveLength(2));
  expect(puts()[1][1]).toMatchObject({ auth_token: 'new-token' });
});

it('removes the Auth Token with only the removal, turning calls off with it', async () => {
  put = async () => ({ ...complete, has_auth_token: false, enabled: false });
  renderPage({ ...complete, enabled: true, listener: { ...base.listener, running: true } });
  fireEvent.change(await screen.findByLabelText('Account SID'), { target: { value: 'bad' } });
  fireEvent.click(screen.getByRole('button', { name: 'Remove' }));
  await waitFor(() => expect(puts()).toHaveLength(1));
  expect(puts()[0][1]).toEqual({ auth_token: '', enabled: false });
  expect(screen.getByLabelText('Account SID')).toHaveValue('bad');
});

it('explains backend validation codes as fixes', async () => {
  put = async () => {
    throw new ApiError(400, 'bad', { detail: { code: 'invalid_public_url', message: 'x' } });
  };
  renderPage(base);
  fireEvent.change(await screen.findByLabelText('Public tunnel URL'), {
    target: { value: 'http://nope' },
  });
  fireEvent.click(within(step('Public tunnel')).getByRole('button', { name: 'Save' }));
  expect(await screen.findByRole('alert')).toHaveTextContent(
    'Use the https address your tunnel printed',
  );
});

it('shows exact tunnel commands for the gateway port, copies them and checks the gateway', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true });
  renderPage(base);
  const tunnel = await waitFor(() => step('Public tunnel'));
  expect(within(tunnel).getByText('cloudflared tunnel --url http://127.0.0.1:3951')).toBeVisible();
  fireEvent.click(within(tunnel).getByRole('button', { name: 'Copy Start the tunnel' }));
  await waitFor(() =>
    expect(copy).toHaveBeenCalledWith('cloudflared tunnel --url http://127.0.0.1:3951'),
  );
  fireEvent.click(within(tunnel).getByRole('button', { name: 'ngrok' }));
  expect(within(tunnel).getByText('ngrok http 127.0.0.1:3951')).toBeInTheDocument();
  expect(within(tunnel).getByText('Check whether the call gateway is listening.')).toBeVisible();
  fireEvent.click(within(tunnel).getByRole('button', { name: 'Check' }));
  expect(
    await within(tunnel).findByText(/The gateway starts when you turn on calls/),
  ).toBeInTheDocument();
});

it('does not count rejected or busy attempts as a working webhook', async () => {
  renderPage({
    ...complete,
    calls: {
      active: 0,
      max_concurrent: 2,
      recent: [
        { call: 'a', started_at: 1, ended_at: 1, outcome: 'rejected_signature', audio_seconds: 0 },
        { call: 'b', started_at: 1, ended_at: 1, outcome: 'busy', audio_seconds: 0 },
      ],
    },
  });
  await loadedHero();
  expect(within(step('Phone number')).getByText('To do')).toBeInTheDocument();
});

it('gives the webhook step a reason until the tunnel is saved, then a copyable URL', async () => {
  const copy = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, 'clipboard', { value: { writeText: copy }, configurable: true });
  const { unmount } = renderPage(base);
  expect(await screen.findByText(/Save your public tunnel URL in step 2/)).toBeInTheDocument();
  unmount();
  renderPage(complete);
  const number = await waitFor(() => step('Phone number'));
  expect(within(number).getByText('To do')).toBeInTheDocument();
  fireEvent.click(within(number).getByRole('button', { name: 'Copy Voice webhook URL' }));
  await waitFor(() => expect(copy).toHaveBeenCalledWith(complete.webhook_url));
});

it('turns on from Ready with only the switch and reports what is missing', async () => {
  put = async () => {
    throw new ApiError(400, 'Complete the setup first', {
      detail: { code: 'incomplete', missing: ['public_base_url', 'greeting'] },
    });
  };
  renderPage(complete);
  const hero = await loadedHero();
  expect(within(hero).getByRole('status')).toHaveTextContent('Ready');
  fireEvent.click(within(hero).getByRole('button', { name: /Turn on calls/ }));
  expect(
    await screen.findByText('Complete these fields first: Public tunnel URL, Greeting'),
  ).toBeInTheDocument();
  expect(puts()).toEqual([['/api/integrations/twilio/config', { enabled: true }]]);
});

it('shows Live with recent calls and turns off with only the switch', async () => {
  put = async () => complete;
  renderPage({
    ...complete,
    enabled: true,
    listener: { running: true, port: 3950, tunnel_target: 'http://127.0.0.1:3950' },
    calls: {
      active: 1,
      max_concurrent: 2,
      recent: [
        {
          call: 'a',
          started_at: 1_700_000_000,
          ended_at: 1,
          outcome: 'completed',
          audio_seconds: 3.2,
        },
        {
          call: 'b',
          started_at: 1_700_000_000,
          ended_at: 1,
          outcome: 'rejected_signature',
          audio_seconds: 0,
        },
        {
          call: 'c',
          started_at: 1_700_000_000,
          ended_at: null,
          outcome: 'something_new',
          audio_seconds: 0,
        },
      ],
    },
  });
  const hero = await loadedHero();
  expect(within(hero).getByRole('status')).toHaveTextContent('Live');
  expect(screen.getByText('Listening on http://127.0.0.1:3950')).toBeInTheDocument();
  expect(screen.getByText('1 of 2 lines in use')).toBeInTheDocument();
  expect(screen.getByText('Completed')).toBeInTheDocument();
  expect(screen.getByText('Rejected: invalid signature')).toBeInTheDocument();
  expect(screen.getByText('Error')).toBeInTheDocument();
  expect(within(step('Phone number')).getByText('Done')).toBeInTheDocument();
  fireEvent.change(screen.getByLabelText('Account SID'), { target: { value: 'not-a-sid' } });
  fireEvent.click(within(hero).getByRole('button', { name: 'Turn off calls' }));
  await waitFor(() =>
    expect(puts()).toEqual([['/api/integrations/twilio/config', { enabled: false }]]),
  );
});

it('explains why the preview is unavailable and plays it only when asked', async () => {
  const createObjectURL = vi.fn(() => 'blob:preview');
  Object.assign(URL, { createObjectURL, revokeObjectURL: vi.fn() });
  api.fetch.mockResolvedValue({ blob: async () => new Blob(['RIFF'], { type: 'audio/wav' }) });
  renderPage({ ...base, voice_id: 'p1' });
  const button = await screen.findByRole('button', { name: /Play phone-quality preview/ });
  expect(button).toBeDisabled();
  expect(button).toHaveAccessibleDescription('Write a greeting in step 4 to preview it.');
  fireEvent.change(screen.getByLabelText('Greeting'), { target: { value: 'Hello caller' } });
  expect(button).toBeEnabled();
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

it('falls back cleanly without the Calls backend: agent mode and test calls explain why', async () => {
  renderPage(complete);
  const agent = await screen.findByRole('button', { name: 'AI agent' });
  expect(agent).toBeDisabled();
  expect(screen.getByText(/The AI agent comes with Calls/)).toBeInTheDocument();
  const callMe = screen.getByRole('button', { name: /Call me to test/ });
  expect(callMe).toBeDisabled();
  expect(callMe).toHaveAccessibleDescription('Test calls to your own phone come with Calls.');
  expect(screen.queryByRole('link', { name: /Open Calls/ })).toBeNull();
  expect(screen.queryByLabelText('Your Twilio number')).toBeNull();
  // Derived readiness: four checks, no language-model row.
  const checklist = screen.getByRole('heading', { name: 'Readiness' }).closest('section')!;
  expect(within(checklist).getAllByRole('listitem')).toHaveLength(4);
});

it('uses the Calls backend when present: readiness, number, agent mode and disclosure', async () => {
  router.routesByPath = { '/calls': {} };
  calls = {
    from_number: '',
    inbound_mode: 'greeting',
    disclosure_template: "Hi, this is {name}'s AI assistant calling on their behalf.",
  };
  readiness = [
    { id: 'credentials', ok: true },
    { id: 'llm', ok: false, detail: 'Start a local model' },
  ];
  put = async (path, body) => (path === '/calls/settings' ? { ...calls, ...body } : complete);
  renderPage(complete);
  expect(await screen.findByText('Start a local model')).toBeInTheDocument();
  expect(screen.getByText('Language model')).toBeInTheDocument();
  expect(screen.getByRole('link', { name: /Open Calls/ })).toHaveAttribute('href', '#/calls');
  expect(screen.queryByRole('button', { name: /Call me to test/ })).toBeNull();

  const number = screen.getByLabelText('Your Twilio number');
  fireEvent.change(number, { target: { value: '5551234' } });
  const numberSave = within(step('Phone number')).getByRole('button', { name: 'Save' });
  expect(numberSave).toBeDisabled();
  fireEvent.change(number, { target: { value: '+15551234567' } });
  fireEvent.click(numberSave);
  await waitFor(() =>
    expect(puts()).toContainEqual(['/calls/settings', { from_number: '+15551234567' }]),
  );

  fireEvent.click(screen.getByRole('button', { name: 'AI agent' }));
  expect(screen.getByLabelText('AI disclosure')).toHaveValue(
    "Hi, this is {name}'s AI assistant calling on their behalf.",
  );
  fireEvent.click(within(step('Voice and behavior')).getByRole('button', { name: 'Save' }));
  await waitFor(() =>
    expect(puts()).toContainEqual([
      '/calls/settings',
      {
        inbound_mode: 'agent',
        disclosure_template: "Hi, this is {name}'s AI assistant calling on their behalf.",
      },
    ]),
  );
});

it('adds the AI disclosure to the greeting when the Calls backend is absent', async () => {
  renderPage({ ...base, greeting: 'Hi there.' });
  fireEvent.click(await screen.findByRole('button', { name: 'Add to greeting' }));
  expect(screen.getByLabelText('Greeting')).toHaveValue(
    'This call uses an AI-generated voice. Hi there.',
  );
  expect(screen.getByRole('button', { name: 'Added to greeting' })).toBeDisabled();
});

it('keeps steps read-only while an update is in flight and keeps later edits', async () => {
  let finish: (value: TwilioState) => void = () => {};
  put = () => new Promise<TwilioState>((resolve) => (finish = resolve));
  renderPage({ ...base, greeting: 'Hello' });
  const greeting = await screen.findByLabelText('Greeting');
  fireEvent.click(screen.getByRole('button', { name: 'Save and check' }));
  await waitFor(() => expect(greeting).toBeDisabled());
  expect(screen.getByLabelText('Account SID')).toBeDisabled();
  finish({ ...base, greeting: 'Hello' });
  await waitFor(() => expect(greeting).toBeEnabled());
  fireEvent.change(greeting, { target: { value: 'Edited after save' } });
  expect(greeting).toHaveValue('Edited after save');
});

it('needs no greeting when the call agent answers incoming calls', async () => {
  calls = { from_number: '', inbound_mode: 'agent', disclosure_template: 'Hi.' };
  renderPage(base);
  await loadedHero();
  await waitFor(() =>
    expect(within(step('Voice and behavior')).getByText('Done')).toBeInTheDocument(),
  );
});
