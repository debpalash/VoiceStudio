import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({
  apiJson: vi.fn(),
  prefs: { enabled: true, mode: 'hold', prompt: 'gRPC' },
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/dictation-demo', () => ({ DictationDemo: () => null }));
vi.mock('@/lib/api/client', () => ({ apiJson: mock.apiJson }));
vi.mock('@/hooks/use-native-dictation', () => ({
  dictationPreferencesKey: ['prefs'],
  nativeShortcutKey: ['native-shortcut'],
  useDictationPreferences: () => ({ data: mock.prefs }),
}));
import { ShortcutSettings } from './shortcut-settings';
beforeEach(() => {
  mock.prefs = { enabled: true, mode: 'hold', prompt: 'gRPC' };
});
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const client = new QueryClient();
const tree = () => (
  <QueryClientProvider client={client}>
    <ShortcutSettings />
  </QueryClientProvider>
);
function renderSettings() {
  const view = render(tree());
  return {
    view,
    field: screen.getByRole('textbox', { name: 'voicePanel.prompt_label' }),
    save: screen.getByRole('button', { name: 'common.save' }),
  };
}
it('saves an edited vocabulary prompt and stays idle while unchanged', async () => {
  mock.apiJson.mockResolvedValue({});
  const { field, save } = renderSettings();
  expect(field).toHaveValue('gRPC');
  expect(save).toBeDisabled();
  fireEvent.change(field, { target: { value: 'gRPC, Kubernetes' } });
  expect(save).toBeEnabled();
  fireEvent.click(save);
  await waitFor(() => expect(mock.apiJson).toHaveBeenCalledTimes(1));
  const [path, init] = mock.apiJson.mock.calls[0];
  expect(path).toBe('/dictation/prefs');
  expect(JSON.parse(init.body)).toEqual({ prompt: 'gRPC, Kubernetes' });
});
it('follows the saved value again once an edit is reverted', () => {
  const { view, field } = renderSettings();
  fireEvent.change(field, { target: { value: 'gRPC, Kubernetes' } });
  fireEvent.change(field, { target: { value: 'gRPC' } });
  // Another window saves a new hint; the 10 s refetch must show it.
  mock.prefs = { ...mock.prefs, prompt: 'gRPC, Docker' };
  view.rerender(tree());
  expect(field).toHaveValue('gRPC, Docker');
});
it('locks the field while a save is in flight so typing is not lost', async () => {
  let finish: (value: unknown) => void = () => {};
  mock.apiJson.mockReturnValue(new Promise((resolve) => (finish = resolve)));
  const { field, save } = renderSettings();
  fireEvent.change(field, { target: { value: 'gRPC, Kubernetes' } });
  fireEvent.click(save);
  await waitFor(() => expect(field).toBeDisabled());
  finish({});
  await waitFor(() => expect(field).toBeEnabled());
});
it('reports a failed save next to the prompt', async () => {
  mock.apiJson.mockRejectedValue(new Error('offline'));
  const { field, save } = renderSettings();
  fireEvent.change(field, { target: { value: '' } });
  fireEvent.click(save);
  expect(await screen.findByRole('alert')).toHaveTextContent('common.error');
});
