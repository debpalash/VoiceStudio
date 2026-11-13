import { act, cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import '@/i18n';
import { ApiError } from '@/lib/api/client';
import type { CallRecord, ReadinessItem } from '@/lib/api/calls';
import type { Profile } from '@/lib/api/types';
import { FakeEventSource } from '@/test/fake-event-source';
import { CallsPage } from './calls-page';

const api = vi.hoisted(() => ({ json: vi.fn() }));
vi.mock('@/lib/api/client', async (importOriginal) => ({
  ...(await importOriginal<typeof import('@/lib/api/client')>()),
  apiJson: api.json,
}));
const profiles = vi.hoisted(() => ({ data: [] as Profile[] }));
vi.mock('@/hooks/use-profiles', () => ({ useProfiles: () => ({ data: profiles.data }) }));
vi.mock('@tanstack/react-router', () => ({
  Link: ({ to, params, activeProps: _activeProps, children, ...props }: any) => (
    <a
      href={params ? to.replace(/\$(\w+)/, (_: string, key: string) => params[key]) : to}
      {...props}
    >
      {children}
    </a>
  ),
}));
vi.mock('@/components/app-shell/workspace-header', () => ({
  WorkspaceHeader: ({ children }: { children: React.ReactNode }) => <header>{children}</header>,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

const READY: ReadinessItem[] = (
  ['credentials', 'tunnel', 'number', 'llm', 'asr', 'voice'] as const
).map((id) => ({ id, ok: true, detail: '' }));
const call = (patch: Partial<CallRecord> = {}): CallRecord => ({
  id: 'c1',
  direction: 'outbound',
  to_masked: '+1 ••• ••• 0199',
  status: 'ringing',
  created_at: 1_790_000_000,
  started_at: null,
  ended_at: null,
  duration_s: null,
  profile_id: 'mine',
  brief: 'Book a table for 2',
  outcome: null,
  summary: null,
  transcript: [],
  ...patch,
});

let server: {
  missing: boolean;
  readiness: ReadinessItem[];
  calls: CallRecord[];
  detail: CallRecord;
};
const requests = () =>
  api.json.mock.calls
    .filter(([, init]) => init?.method && init.method !== 'GET')
    .map(([path, init]) => ({ path, method: init.method, body: JSON.parse(init.body) }));

function renderPage() {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <CallsPage />
    </QueryClientProvider>,
  );
}

beforeEach(() => {
  server = { missing: false, readiness: READY, calls: [], detail: call() };
  profiles.data = [
    { id: 'mine', name: 'My voice', kind: 'clone', verified_own_voice: 1 },
    { id: 'someone', name: 'Borrowed voice', kind: 'clone', verified_own_voice: 0 },
    { id: 'robot', name: 'Designed narrator', kind: 'design' },
  ] as Profile[];
  api.json.mockReset();
  api.json.mockImplementation(async (path: string, init?: RequestInit) => {
    if (server.missing) throw new ApiError(404, 'Not Found');
    const method = init?.method ?? 'GET';
    if (path === '/calls/readiness') return server.readiness;
    if (path === '/calls/settings')
      return {
        from_number: '+15550000000',
        disclosure_template: "Hi, this is {name}'s AI assistant.",
        user_name: 'Sam',
        inbound_mode: 'greeting',
        inbound_brief: '',
        max_concurrent: 1,
        record_calls: false,
      };
    if (path.startsWith('/calls?limit=')) return { calls: server.calls };
    if (path === '/calls' && method === 'POST') return { call: server.detail };
    if (/^\/calls\/[^/]+$/.test(path)) return server.detail;
    if (/\/(say|takeover|hangup)$/.test(path)) return { ok: true };
    throw new Error(`unexpected ${method} ${path}`);
  });
  vi.stubGlobal('matchMedia', (query: string) => ({
    matches: query.includes('1440') || query.includes('1024'),
    addEventListener() {},
    removeEventListener() {},
  }));
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
});
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it('explains an outdated backend instead of failing when the Calls API is missing', async () => {
  server.missing = true;
  renderPage();
  expect(
    await screen.findByRole('heading', { name: 'Calls need the latest backend' }),
  ).toBeVisible();
  expect(screen.getByRole('link', { name: 'Set up Twilio' })).toHaveAttribute(
    'href',
    '/integrations/twilio',
  );
  expect(screen.queryByRole('form', { name: 'New call' })).not.toBeInTheDocument();
});

it('gates calling on readiness and links every failing check to its fix', async () => {
  server.readiness = READY.map((item) =>
    item.id === 'credentials' || item.id === 'llm'
      ? { ...item, ok: false, detail: 'Missing' }
      : item,
  );
  renderPage();
  const banner = await screen.findByRole('region', { name: 'Finish setup to place calls' });
  expect(within(banner).getByText('4 of 6 ready')).toBeVisible();
  expect(
    within(banner).getByRole('link', { name: 'Fix: Twilio account connected' }),
  ).toHaveAttribute('href', '/integrations/twilio');
  expect(
    within(banner).getByRole('link', { name: 'Fix: Language model for the conversation' }),
  ).toHaveAttribute('href', '/settings/models/llm');
  expect(screen.getByRole('button', { name: /^Call/ })).toBeDisabled();
  expect(screen.getByText('Finish the setup steps above to place calls.')).toBeVisible();
});

it('offers only verified or designed voices', async () => {
  renderPage();
  const picker = await screen.findByRole('combobox', { name: 'Voice' });
  expect(picker).toHaveTextContent('My voice');
  fireEvent.click(picker);
  const options = await screen.findAllByRole('option');
  expect(options.map((option) => option.textContent)).toEqual([
    expect.stringContaining('My voice'),
    expect.stringContaining('Designed narrator'),
  ]);
  expect(screen.queryByText('Borrowed voice')).not.toBeInTheDocument();
});

it('prefills a workflow call without bypassing confirmation', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  render(<QueryClientProvider client={client}>
    <CallsPage initialDraft={{ to: '+15550100199', brief: 'Ask about opening hours' }} onCanvas={() => {}} />
  </QueryClientProvider>);
  expect(await screen.findByLabelText('Phone number')).toHaveValue('+15550100199');
  expect(screen.getByDisplayValue('Ask about opening hours')).toBeVisible();
  expect(screen.getByRole('button', { name: 'Canvas' })).toBeVisible();
  expect(requests()).toEqual([]);
});

it('validates the number and asks for confirmation before dialling', async () => {
  renderPage();
  const number = await screen.findByLabelText('Phone number');
  fireEvent.change(number, { target: { value: '555 010 0199' } });
  fireEvent.blur(number);
  expect(screen.getByText(/Start with \+ and the country code/)).toBeVisible();
  fireEvent.change(number, { target: { value: '+44 20 7946 0958' } });
  expect(screen.getByText('Calling United Kingdom')).toBeVisible();
  fireEvent.change(number, { target: { value: '+1 (555) 010-0199' } });
  expect(screen.queryByText(/^Calling /)).not.toBeInTheDocument();
  fireEvent.click(screen.getByRole('button', { name: 'Book a restaurant' }));
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'Call +15550100199' })).toBeEnabled(),
  );

  // Clearing the line while the switch is on must not opt out of the disclosure.
  fireEvent.change(screen.getByDisplayValue("Hi, this is Sam's AI assistant."), {
    target: { value: '   ' },
  });
  fireEvent.click(screen.getByRole('button', { name: 'Call +15550100199' }));
  const dialog = await screen.findByRole('dialog', { name: 'Call +15550100199 now?' });
  expect(requests()).toEqual([]);
  // Focus lands on Cancel, so a held Enter cannot dial.
  await waitFor(() => expect(within(dialog).getByRole('button', { name: 'Cancel' })).toHaveFocus());
  fireEvent.click(within(dialog).getByRole('button', { name: 'Call now' }));
  // The unedited template is left to the backend, which fills {name} itself;
  // the form previews it with the user's name and marks it as uninterruptible.
  expect(screen.getByText(/can't be interrupted/)).toBeInTheDocument();
  await waitFor(() => expect(requests()).toHaveLength(1));
  expect(requests()[0]).toEqual({
    path: '/calls',
    method: 'POST',
    body: {
      to: '+15550100199',
      brief: expect.stringContaining('Book a table for 2 people'),
      profile_id: 'mine',
      max_minutes: 5,
    },
  });
  // The live view opens on the new call and follows its events.
  await waitFor(() => expect(FakeEventSource.latest().url).toBe('/api/calls/c1/events'));
});

it('streams the live call and wires take-over, say and hang-up', async () => {
  server.calls = [call({ status: 'in_progress', started_at: Date.now() / 1000 })];
  server.detail = server.calls[0];
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Book a table for 2/ }));
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
  const source = FakeEventSource.latest();
  act(() => {
    source.open();
    source.emit({ type: 'agent_state', state: 'speaking' });
    source.emit({
      type: 'transcript',
      speaker: 'agent',
      text: 'Hi, a table for two?',
      final: true,
      t: 1,
    });
    source.emit({
      type: 'transcript',
      speaker: 'caller',
      text: 'Sure, what time',
      final: false,
      t: 3,
    });
  });
  const log = screen.getByRole('log', { name: 'Transcript' });
  expect(within(log).getByText('Hi, a table for two?')).toBeVisible();
  expect(within(log).getByText('Sure, what time')).toBeVisible();
  expect(document.querySelector('[data-agent-state=speaking]')).toHaveTextContent('Speaking');

  fireEvent.click(screen.getByRole('switch', { name: 'Take over' }));
  await waitFor(() =>
    expect(requests()).toContainEqual({
      path: '/calls/c1/takeover',
      method: 'POST',
      body: { enabled: true },
    }),
  );
  const say = await screen.findByLabelText('What to say next');
  fireEvent.change(say, { target: { value: 'Make it 8pm please' } });
  fireEvent.click(screen.getByRole('button', { name: 'Say' }));
  await waitFor(() =>
    expect(requests()).toContainEqual({
      path: '/calls/c1/say',
      method: 'POST',
      body: { text: 'Make it 8pm please' },
    }),
  );
  fireEvent.click(screen.getByRole('button', { name: 'Hang up' }));
  await waitFor(() =>
    expect(requests()).toContainEqual({ path: '/calls/c1/hangup', method: 'POST', body: {} }),
  );

  act(() => {
    source.emit({ type: 'outcome', outcome: 'booked', summary: 'Booked 8pm Friday.' });
    source.emit({ type: 'ended', duration_s: 64 });
  });
  expect(screen.queryByRole('button', { name: 'Hang up' })).not.toBeInTheDocument();
  expect(await screen.findByText('Booked 8pm Friday.')).toBeVisible();
  expect(screen.getAllByText('Booked').length).toBeGreaterThan(0);
  expect(source.readyState).toBe(FakeEventSource.CLOSED);
});

it('refreshes the finished record when a terminal status arrives without an ended event', async () => {
  server.calls = [call({ status: 'in_progress', started_at: Date.now() / 1000 })];
  server.detail = server.calls[0];
  renderPage();
  fireEvent.click(await screen.findByRole('button', { name: /Book a table for 2/ }));
  await waitFor(() => expect(FakeEventSource.instances.length).toBeGreaterThan(0));
  const detailFetches = () => api.json.mock.calls.filter(([path]) => path === '/calls/c1').length;
  const before = detailFetches();
  server.detail = call({
    status: 'completed',
    ended_at: Date.now() / 1000,
    duration_s: 20,
    outcome: 'done',
    summary: 'They open at 9am on Saturday.',
  });
  act(() => FakeEventSource.latest().emit({ type: 'status', status: 'completed' }));
  await waitFor(() => expect(detailFetches()).toBeGreaterThan(before));
  expect(await screen.findByText('They open at 9am on Saturday.')).toBeVisible();
  expect(screen.queryByRole('button', { name: 'Hang up' })).not.toBeInTheDocument();
});
