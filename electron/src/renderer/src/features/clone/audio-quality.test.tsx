import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { afterEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({ api: vi.fn(), seek: vi.fn() }));
vi.mock('@/lib/api/client', () => ({ apiJson: mocks.api }));
vi.mock('@/lib/audio/playback-clock', () => ({
  requestPlaybackSeek: mocks.seek,
}));
vi.mock('@/components/media-clock', () => ({
  formatClock: (value: number) => String(value),
}));
vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
import { AudioQuality } from './audio-quality';
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
function mount(path = '12345678.wav') {
  return render(
    <QueryClientProvider client={new QueryClient()}>
      <AudioQuality audioPath={path} />
    </QueryClientProvider>,
  );
}
it('checks only on request, seeks to warnings, and dismisses without changing audio', async () => {
  mocks.api.mockResolvedValue({
    truncated: false,
    warnings: [{ kind: 'silence', start: 2, end: 4 }],
  });
  mount();
  expect(mocks.api).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText('audioQuality.check'));
  fireEvent.click(await screen.findByText('2–4: audioQuality.silence'));
  expect(mocks.api).toHaveBeenCalledWith(
    '/audio/12345678/quality',
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  expect(mocks.seek).toHaveBeenCalledWith('output', 2);
  fireEvent.click(screen.getByText('common.close'));
  expect(screen.queryByText('audioQuality.advisory')).toBeNull();
});
it('reports a limited scan without promising clean audio', async () => {
  mocks.api.mockResolvedValue({ truncated: true, warnings: [] });
  mount();
  fireEvent.click(screen.getByText('audioQuality.check'));
  expect(await screen.findByText('audioQuality.truncated')).toBeInTheDocument();
});
it('handles analysis failure and rejects arbitrary paths', async () => {
  mocks.api.mockRejectedValue(new Error('unavailable'));
  mount();
  fireEvent.click(screen.getByText('audioQuality.check'));
  expect(await screen.findByText('audioQuality.failed')).toBeInTheDocument();
  cleanup();
  mount('../private.wav');
  expect(screen.queryByText('audioQuality.check')).toBeNull();
});
