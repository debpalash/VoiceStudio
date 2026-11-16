import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubMediaPreview } from './dub-media-preview';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
afterEach(cleanup);
it('attaches the playable URL immediately and surfaces failures with a retry', () => {
  const { container } = render(
    <DubMediaPreview src="/api/dub/preview-video/job?lang=en" kind="video" />,
  );
  const video = container.querySelector('video')!;
  expect(video.getAttribute('src')).toContain('lang=en');
  expect(video.controls).toBe(true);
  expect(video.preload).toBe('metadata');
  fireEvent.error(video);
  expect(screen.getByRole('alert')).toBeTruthy();
  expect(screen.getByRole('button', { name: 'common.retry' })).toBeTruthy();
});
it('plays the complete audio directly without creating another dub', () => {
  const { container } = render(
    <DubMediaPreview src="/api/dub/download-audio/job?lang=en&preserve_bg=false" kind="audio" />,
  );
  expect(container.querySelector('audio')?.getAttribute('src')).toContain('preserve_bg=false');
  expect(container.querySelector('video')).toBeNull();
});
it('keeps playback exclusive between the video and the complete dub', () => {
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const { container } = render(
    <>
      <DubMediaPreview src="/video" kind="video" />
      <DubMediaPreview src="/audio" kind="audio" sourceKey="dub-complete" />
    </>,
  );
  fireEvent.play(container.querySelector('video')!);
  fireEvent.play(container.querySelector('audio')!);
  expect(pause).toHaveBeenCalledTimes(1);
  pause.mockRestore();
});
it('seeks to a transcript range and stops at its end', async () => {
  const { requestPlaybackRange } = await import('@/lib/audio/playback-clock');
  const play = vi.spyOn(HTMLMediaElement.prototype, 'play').mockResolvedValue();
  const pause = vi.spyOn(HTMLMediaElement.prototype, 'pause').mockImplementation(() => {});
  const { container } = render(<DubMediaPreview src="/video" kind="video" />);
  const video = container.querySelector('video')!;
  Object.defineProperty(video, 'readyState', { value: 4 });
  Object.defineProperty(video, 'duration', { value: 100 });
  const { act, waitFor } = await import('@testing-library/react');
  act(() => requestPlaybackRange('dub-preview', 7, 9));
  await waitFor(() => expect(video.currentTime).toBe(7));
  expect(play).toHaveBeenCalled();
  video.currentTime = 9.1;
  fireEvent.timeUpdate(video);
  expect(pause).toHaveBeenCalled();
  play.mockRestore();
  pause.mockRestore();
});
