import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, expect, it, vi } from 'vitest';
import { apiFetch } from '@/lib/api/client';
import { blankLongformDraft } from './longform-session';
import { renderStoryStems } from './story-preview';
import { StoryStems } from './story-stems';

vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/api/client', async (load) => ({
  ...(await load<typeof import('@/lib/api/client')>()),
  apiFetch: vi.fn(),
}));
vi.mock('./story-preview', () => ({ renderStoryStems: vi.fn() }));
vi.mock('./story-inputs', () => ({ storyVoicesReady: () => true }));

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
});

it('does not retain audio object URLs when encoding is canceled partway through', async () => {
  const create = vi.spyOn(URL, 'createObjectURL').mockReturnValue('blob:stem');
  const audio = new Blob(['audio']);
  vi.mocked(renderStoryStems).mockResolvedValue([
    { character: 'a', blob: audio },
    { character: 'b', blob: audio },
  ]);
  vi.mocked(apiFetch)
    .mockResolvedValueOnce(new Response('audio'))
    .mockImplementationOnce((_path, options) => new Promise((_, reject) => {
      options?.signal?.addEventListener('abort', () => reject(options.signal?.reason), { once: true });
    }));

  render(<StoryStems draft={{ ...blankLongformDraft(), format: 'mp3' }} profiles={[]} disabled={false} />);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'stories.stems' })));
  expect(apiFetch).toHaveBeenCalledTimes(2);
  await act(async () => fireEvent.click(screen.getByRole('button', { name: 'common.stop' })));
  expect(create).not.toHaveBeenCalled();
});
