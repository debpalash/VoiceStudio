// Generation takes: the Studio history rail's star / load-as-output actions.
import i18next from 'i18next';
import { describe, it, expect, vi, beforeAll } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import React from 'react';

import es from '../i18n/locales/es.json';
import WorkspaceHistory from './WorkspaceHistory';

beforeAll(() => {
  // LazyWaveform defers the real <WaveformPlayer> behind an IntersectionObserver;
  // a no-op stub keeps rows rendered without ever mounting the audio fetch.
  global.IntersectionObserver = class {
    observe() {}
    disconnect() {}
    unobserve() {}
  };
});

const takes = [
  {
    id: 'aa1',
    mode: 'clone',
    text: 'first take',
    audio_path: 'aa1.wav',
    starred: 0,
    created_at: 2,
  },
  {
    id: 'bb2',
    mode: 'design',
    text: 'second take',
    audio_path: 'bb2.wav',
    starred: 1,
    created_at: 1,
  },
];

const noop = () => {};

function renderRail(overrides = {}) {
  return render(
    <WorkspaceHistory
      history={takes}
      handleSaveHistoryAsProfile={noop}
      handleLockProfile={noop}
      handleNativeExport={noop}
      restoreHistory={noop}
      deleteHistory={noop}
      toggleStarHistory={noop}
      playTakeAsOutput={noop}
      {...overrides}
    />,
  );
}

describe('WorkspaceHistory takes actions', () => {
  it('star button reflects the starred state and calls the handler', () => {
    const toggleStarHistory = vi.fn();
    renderRail({ toggleStarHistory });

    const unstarred = screen.getByTestId('take-star-aa1');
    const starred = screen.getByTestId('take-star-bb2');
    expect(unstarred).toHaveAttribute('aria-pressed', 'false');
    expect(starred).toHaveAttribute('aria-pressed', 'true');

    fireEvent.click(unstarred);
    expect(toggleStarHistory).toHaveBeenCalledTimes(1);
    expect(toggleStarHistory.mock.calls[0][0].id).toBe('aa1');
  });

  it('load-as-output button hands the take to the player handler', () => {
    const playTakeAsOutput = vi.fn();
    renderRail({ playTakeAsOutput });

    fireEvent.click(screen.getByTestId('take-play-bb2'));
    expect(playTakeAsOutput).toHaveBeenCalledTimes(1);
    expect(playTakeAsOutput.mock.calls[0][0].id).toBe('bb2');
  });

  it('exposes text expansion and icon actions to keyboard and assistive technology', () => {
    renderRail();

    const expand = screen.getByRole('button', { name: 'first take' });
    expect(expand).toHaveAttribute('aria-expanded', 'false');
    fireEvent.click(expand);
    expect(expand).toHaveAttribute('aria-expanded', 'true');
    expect(screen.getAllByRole('button', { name: 'Export' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Load settings' })).toHaveLength(2);
    expect(screen.getAllByRole('button', { name: 'Delete' })).toHaveLength(2);
  });

  it('starred filter narrows the rail to starred takes only', () => {
    renderRail();

    fireEvent.click(screen.getByRole('button', { name: 'Starred' }));
    expect(screen.queryByTestId('take-star-aa1')).toBeNull();
    expect(screen.getByTestId('take-star-bb2')).toBeInTheDocument();
  });

  it('omits the takes actions when no handlers are passed (dub rail safety)', () => {
    renderRail({ toggleStarHistory: undefined, playTakeAsOutput: undefined });
    expect(screen.queryByTestId('take-star-aa1')).toBeNull();
    expect(screen.queryByTestId('take-play-aa1')).toBeNull();
  });
});

describe('WorkspaceHistory dub media previews', () => {
  const renderDubRail = (dubHistory) =>
    render(
      <WorkspaceHistory
        variant="dub"
        dubHistory={dubHistory}
        restoreDubHistory={noop}
        deleteHistory={noop}
      />,
    );

  it('shows extracted thumbnails for YouTube and uploaded video dubs', () => {
    renderDubRail([
      {
        id: 'youtube-job',
        filename: 'YouTube clip',
        segments_count: 3,
        duration: 12,
        job_data: JSON.stringify({ input_type: 'video' }),
      },
      {
        id: 'uploaded-video',
        filename: 'movie.mp4',
        segments_count: 2,
        duration: 8,
        job_data: JSON.stringify({ video_path: '/dubs/uploaded-video/original.mp4' }),
      },
    ]);

    expect(screen.getByTestId('dub-thumbnail-youtube-job')).toHaveAttribute(
      'src',
      expect.stringContaining('/dub/thumb/youtube-job'),
    );
    expect(screen.getByTestId('dub-thumbnail-uploaded-video')).toHaveAttribute(
      'src',
      expect.stringContaining('/dub/thumb/uploaded-video'),
    );
  });

  it('shows a waveform tile instead of a thumbnail for audio-only dubs', () => {
    renderDubRail([
      {
        id: 'audio-job',
        filename: 'interview.wav',
        segments_count: 4,
        duration: 30,
        job_data: JSON.stringify({ input_type: 'audio' }),
      },
    ]);

    expect(screen.getByTestId('dub-audio-waveform-audio-job')).toBeInTheDocument();
    expect(screen.queryByTestId('dub-thumbnail-audio-job')).toBeNull();
  });

  it('keeps malformed legacy job data renderable as a video row', () => {
    renderDubRail([
      {
        id: 'legacy-job',
        filename: 'legacy.mp4',
        segments_count: 1,
        duration: 5,
        job_data: '{not-json',
      },
    ]);

    expect(screen.getByTestId('dub-thumbnail-legacy-job')).toBeInTheDocument();
  });

  it('localizes Dub row metadata and icon actions', async () => {
    const localizedI18n = i18next.createInstance();
    await localizedI18n.init({
      lng: 'es',
      fallbackLng: false,
      resources: { es: { translation: es } },
    });
    render(
      <I18nextProvider i18n={localizedI18n}>
        <WorkspaceHistory
          variant="dub"
          dubHistory={[
            {
              id: 'localized-job',
              filename: 'pelicula.mp4',
              segments_count: 2,
              duration: 5,
              job_data: JSON.stringify({ input_type: 'video' }),
            },
          ]}
          restoreDubHistory={noop}
          deleteHistory={noop}
        />
      </I18nextProvider>,
    );

    expect(screen.getByText(/2 segmentos/)).toBeInTheDocument();
    expect(screen.getByText('Automático')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Eliminar' })).toBeInTheDocument();
  });
});
