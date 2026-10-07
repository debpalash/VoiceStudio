import { clearComparison } from './comparison-state';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mock = vi.hoisted(() => ({ generate: vi.fn(), warning: vi.fn() }));
vi.mock('sonner', () => ({ toast: { warning: mock.warning } }));
vi.mock('@/lib/api/generate', () => ({ generateClone: mock.generate }));
vi.mock('@/hooks/use-tts-readiness', () => ({ useTtsReadiness: () => null }));
vi.mock('@/hooks/use-profiles', () => ({
  useProfiles: () => ({
    data: [
      { id: 'a', name: 'Alpha', language: 'French', seed: 0 },
      { id: 'b', name: 'Beta', language: 'English', seed: 12 },
    ],
  }),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/waveform-player', () => ({
  WaveformPlayer: ({ source }: { source: string }) => <div data-testid={source} />,
}));
import { CompareVoices } from './compare-voices';
import { cloneSettingsStore } from '@/lib/store/clone-settings';
afterEach(() => {
  cleanup();
  clearComparison();
  vi.clearAllMocks();
});
function mount() {
  const view = render(
    <QueryClientProvider client={new QueryClient()}>
      <CompareVoices />
    </QueryClientProvider>,
  );
  fireEvent.change(screen.getByRole('textbox', { name: 'compare.test_phrase' }), {
    target: { value: 'Shared phrase' },
  });
  fireEvent.click(screen.getAllByRole('button', { name: 'Alpha' })[0]);
  fireEvent.click(screen.getAllByRole('button', { name: 'Beta' })[1]);
  return view;
}
it('generates both selected voices sequentially with one text and preserves editor settings', async () => {
  const settings = cloneSettingsStore.state;
  mock.generate.mockResolvedValue({ blob: new Blob(['audio']) });
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'compare.compare_btn' }));
  await screen.findByTestId('compare-1');
  expect(mock.generate).toHaveBeenCalledTimes(2);
  expect(mock.generate.mock.calls[0][0]).toMatchObject({
    text: 'Shared phrase',
    profileId: 'a',
    language: 'French',
    seed: 0,
  });
  expect(mock.generate.mock.calls[1][0]).toMatchObject({
    text: 'Shared phrase',
    profileId: 'b',
    language: 'English',
    seed: 12,
  });
  expect(cloneSettingsStore.state).toBe(settings);
});
it('does not start the second voice after leaving the comparison', async () => {
  let finish!: (value: { blob: Blob }) => void;
  mock.generate.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = mount();
  fireEvent.click(screen.getByRole('button', { name: 'compare.compare_btn' }));
  view.unmount();
  expect(mock.generate.mock.calls[0][1].signal.aborted).toBe(true);
  finish({ blob: new Blob() });
  await waitFor(() => expect(mock.generate).toHaveBeenCalledTimes(1));
});

it('keeps completed comparison text, voices and playback across remounts', async () => {
  mock.generate.mockResolvedValue({ blob: new Blob(['audio']) });
  const first = mount();
  fireEvent.click(screen.getByRole('button', { name: 'compare.compare_btn' }));
  await screen.findByTestId('compare-1');
  first.unmount();
  render(
    <QueryClientProvider client={new QueryClient()}>
      <CompareVoices />
    </QueryClientProvider>,
  );
  expect(screen.getByRole('textbox', { name: 'compare.test_phrase' })).toHaveValue('Shared phrase');
  expect(screen.getByTestId('compare-0')).toBeInTheDocument();
  expect(screen.getByTestId('compare-1')).toBeInTheDocument();
  expect(mock.generate).toHaveBeenCalledTimes(2);
});

it('discloses dropped speech for each affected comparison side and stays quiet otherwise', async () => {
  mock.generate
    .mockResolvedValueOnce({ blob: new Blob(['a']), dropped: null })
    .mockResolvedValueOnce({
      blob: new Blob(['b']),
      dropped: { count: 1, text: 'The missing sentence.' },
    });
  mount();
  fireEvent.click(screen.getByRole('button', { name: 'compare.compare_btn' }));
  await screen.findByTestId('compare-1');
  expect(screen.getByTestId('compare-0')).toBeInTheDocument();
  expect(mock.warning).toHaveBeenCalledTimes(1);
  expect(mock.warning).toHaveBeenCalledWith(
    'tts.droppedChunksWithText',
    expect.objectContaining({ description: 'compare.voice_b · Beta' }),
  );
});

it('does not announce dropped speech from a cancelled comparison', async () => {
  let finish!: (value: unknown) => void;
  mock.generate.mockImplementation(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  const view = mount();
  fireEvent.click(screen.getByRole('button', { name: 'compare.compare_btn' }));
  view.unmount();
  finish({ blob: new Blob(), dropped: { count: 2, text: '' } });
  await new Promise((resolve) => setTimeout(resolve, 0));
  expect(mock.warning).not.toHaveBeenCalled();
});
