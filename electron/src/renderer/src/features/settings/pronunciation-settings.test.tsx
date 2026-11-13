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

function setup() {
  mock.api.mockImplementation((path: string) => {
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
