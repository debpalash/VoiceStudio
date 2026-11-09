import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

type Prefs = { enabled: boolean; mode: string; prompt?: string };
const server = vi.hoisted(() => ({ prefs: undefined as Prefs | undefined }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/dictation-demo', () => ({ DictationDemo: () => null }));
vi.mock('@/lib/api/client', () => ({ apiJson: vi.fn() }));
vi.mock('@/hooks/use-native-dictation', () => ({
  dictationPreferencesKey: ['dictation-preferences'],
  nativeShortcutKey: ['native-shortcut'],
  useDictationPreferences: () => ({ data: server.prefs }),
}));
import { apiJson } from '@/lib/api/client';
import { ShortcutSettings } from './shortcut-settings';

function mount() {
  const client = new QueryClient();
  const page = () => (
    <QueryClientProvider client={client}>
      <ShortcutSettings />
    </QueryClientProvider>
  );
  const view = render(page());
  return { refresh: () => view.rerender(page()) };
}
const hintBox = () => screen.getByRole('textbox', { name: 'voicePanel.prompt_label' });
const saveBtn = () => screen.getByRole('button', { name: 'common.save' });
const typeHint = (value: string) => fireEvent.change(hintBox(), { target: { value } });

beforeEach(() => {
  server.prefs = { enabled: true, mode: 'hold', prompt: 'Okonkwo' };
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

describe('vocabulary hint row', () => {
  it('starts from the stored hint with Save inactive', () => {
    mount();
    expect(hintBox()).toHaveValue('Okonkwo');
    expect(saveBtn()).toBeDisabled();
  });

  it('sends nothing but the prompt when saved', async () => {
    vi.mocked(apiJson).mockResolvedValue({});
    mount();
    typeHint('Okonkwo, naïve');
    expect(saveBtn()).toBeEnabled();
    fireEvent.click(saveBtn());
    await waitFor(() => expect(apiJson).toHaveBeenCalled());
    const [url, init] = vi.mocked(apiJson).mock.calls[0];
    expect([url, init?.method]).toEqual(['/dictation/prefs', 'POST']);
    expect(JSON.parse(String(init?.body))).toStrictEqual({ prompt: 'Okonkwo, naïve' });
  });

  it('adopts a changed stored hint after the user undoes their edit', () => {
    const page = mount();
    typeHint('half-typed');
    typeHint('Okonkwo');
    server.prefs = { enabled: true, mode: 'hold', prompt: 'from another window' };
    page.refresh();
    expect(hintBox()).toHaveValue('from another window');
  });

  it('keeps an unsaved edit even if the stored hint changes underneath', () => {
    const page = mount();
    typeHint('my edit');
    server.prefs = { enabled: true, mode: 'hold', prompt: 'from another window' };
    page.refresh();
    expect(hintBox()).toHaveValue('my edit');
  });

  it('is read-only in effect while the request is in flight', async () => {
    let settle: () => void = () => {};
    vi.mocked(apiJson).mockReturnValue(new Promise((ok) => (settle = () => ok({}))));
    mount();
    typeHint('pending');
    fireEvent.click(saveBtn());
    await waitFor(() => expect(hintBox()).toBeDisabled());
    expect(saveBtn()).toBeDisabled();
    settle();
    await waitFor(() => expect(hintBox()).toBeEnabled());
  });

  it('reports a failed save and keeps what was typed', async () => {
    vi.mocked(apiJson).mockRejectedValue(new Error('boom'));
    mount();
    typeHint('lost?');
    fireEvent.click(saveBtn());
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('common.error');
    expect(hintBox()).toHaveValue('lost?');
  });
});
