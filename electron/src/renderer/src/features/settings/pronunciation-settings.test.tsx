import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';

const mock = vi.hoisted(() => ({ api: vi.fn(), fetch: vi.fn() }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mock.api,
  apiFetch: mock.fetch,
  describeError: String,
}));
vi.mock('@/lib/local-export', () => ({ saveLocalFile: vi.fn() }));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
// A native <select> stands in for the base-ui one, which jsdom cannot drive.
vi.mock('@/components/ui/select', () => ({
  Select: ({
    items,
    value,
    onValueChange,
  }: {
    items: { value: string; label: string }[];
    value: string;
    onValueChange: (value: string) => void;
  }) => (
    <select
      role="combobox"
      value={value}
      onChange={(event) => onValueChange(event.target.value)}
    >
      <option value="" />
      {items.map((item) => (
        <option key={item.value} value={item.value}>
          {item.label}
        </option>
      ))}
    </select>
  ),
  SelectTrigger: () => null,
  SelectContent: () => null,
  SelectItem: () => null,
  SelectValue: () => null,
}));

import { PronunciationSettings } from './pronunciation-settings';

afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

const row = { id: 'e1', term: 'x', replacement: 'y', type: 'respelling', language: '*', enabled: true };

function setup(extra: Record<string, unknown> = {}) {
  mock.api.mockImplementation((path: string) => {
    if (path in extra) return Promise.resolve(extra[path]);
    // The language dictionary lists from `/pronunciation` with no voice query.
    if (path === '/pronunciation') return Promise.resolve([row]);
    if (path === '/profiles')
      return Promise.resolve([
        { id: 'A', name: 'Voice A' },
        { id: 'B', name: 'Voice B' },
      ]);
    if (path.startsWith('/pronunciation?')) return Promise.resolve([row]);
    return Promise.resolve({ imported: 1, entries: [] });
  });
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return render(
    <QueryClientProvider client={client}>
      <PronunciationSettings />
    </QueryClientProvider>,
  );
}

const posts = (path: string) =>
  mock.api.mock.calls.filter(
    ([p, init]) => p === path && (init as RequestInit | undefined)?.method === 'POST',
  );

it('never retargets a pending import to another voice', async () => {
  const { container } = setup();
  fireEvent.click(screen.getByRole('button', { name: 'pronunciation.scope_voice' }));
  await screen.findByRole('option', { name: 'Voice A' });
  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'A' } });
  await screen.findByText('x → y');
  const file = { text: async () => JSON.stringify({ entries: [] }) };
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [file] } });
  await screen.findByText('pronunciation.import_replace_prompt');

  fireEvent.change(screen.getAllByRole('combobox')[0], { target: { value: 'B' } });
  await waitFor(() =>
    expect(screen.queryByText('pronunciation.import_replace_prompt')).toBeNull(),
  );
  expect(posts('/pronunciation/import')).toHaveLength(0);
});

it('does not save a voice entry for every voice when no voice is chosen', async () => {
  setup();
  fireEvent.click(screen.getByRole('button', { name: 'pronunciation.scope_voice' }));
  fireEvent.change(screen.getByLabelText('pronunciation.term'), { target: { value: 'word' } });
  fireEvent.keyDown(screen.getByLabelText('pronunciation.lang_label'), { key: 'Enter' });
  await new Promise((resolve) => setTimeout(resolve, 20));
  expect(posts('/pronunciation')).toHaveLength(0);
});

it('imports a PLS lexicon through the same flow and says what was left out', async () => {
  const entries = [{ term: 'GIF', replacement: 'JH IH1 F', type: 'cmu', language: 'en', enabled: true }];
  const { container } = setup({ '/pronunciation/parse-pls': { entries, skipped: 2 } });
  await screen.findByText('x → y');
  const lexicon = '<lexicon xmlns="http://www.w3.org/2005/01/pronunciation-lexicon"/>';
  const file = { name: 'azure.pls', text: async () => lexicon };
  fireEvent.change(container.querySelector('input[type=file]')!, { target: { files: [file] } });
  await screen.findByText('pronunciation.import_replace_prompt');
  expect(JSON.parse(String(posts('/pronunciation/parse-pls')[0][1]!.body))).toEqual({ text: lexicon });

  fireEvent.click(screen.getByRole('button', { name: 'common.no' }));
  await screen.findByText('pronunciation.import_done pronunciation.import_skipped');
  const sent = JSON.parse(String(posts('/pronunciation/import')[0][1]!.body));
  expect(sent).toMatchObject({ entries, replace: false });
});

it('exports the dictionary as a PLS lexicon', async () => {
  const { saveLocalFile } = await import('@/lib/local-export');
  mock.fetch.mockResolvedValue({ text: async () => '<lexicon/>' });
  setup();
  await screen.findByText('x → y');
  fireEvent.click(screen.getByRole('button', { name: 'pronunciation.export_pls' }));
  await waitFor(() => expect(saveLocalFile).toHaveBeenCalled());
  expect(mock.fetch).toHaveBeenCalledWith('/pronunciation/export?format=pls');
  expect(vi.mocked(saveLocalFile).mock.calls[0][1]).toBe('pronunciation-dictionary.pls');
});
