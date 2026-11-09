import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';

const doubles = vi.hoisted(() => ({ jobs: vi.fn(), reveal: vi.fn() }));

vi.mock('../api/audiobook', () => ({ audiobookListJobs: doubles.jobs }));
vi.mock('../api/exports', () => ({ exportReveal: doubles.reveal }));
vi.mock('../api/hooks', () => ({
  useSystemInfo: () => ({ data: { outputs_dir: 'D:\\VoiceStudio\\renders\\' } }),
}));

import AudiobookRecovery from '../components/audiobook/AudiobookRecovery';

const labels = {
  'audiobook.recovery_title': 'unfinished book',
  'audiobook.untitled': 'no title',
  'audiobook.recovery_hint': 'keep completed work',
  'audiobook.resume': 'continue job',
  'audiobook.open_chapter_cache': 'show audio files',
  'audiobook.open_cache_failed': 'folder unavailable',
};
const translate = (key, args = {}) => {
  if (key === 'audiobook.recovery_progress') return `${args.done} ready out of ${args.total}`;
  if (key === 'audiobook.cache_location_hint') return `${args.manifestPath} | ${args.cachePath}`;
  return labels[key] ?? key;
};
const job = (id, created_at, extra = {}) => ({
  job_id: id,
  type: 'audiobook',
  status: 'cancelled',
  title: `Book ${id}`,
  total_chapters: 9,
  chapters_done: 4,
  created_at,
  ...extra,
});

describe('recovery card inventory', () => {
  beforeEach(() => {
    doubles.jobs.mockReset();
    doubles.reveal.mockReset();
  });

  it('explains a failed inventory load and retries on request', async () => {
    doubles.jobs.mockRejectedValueOnce(new Error('offline'));
    doubles.jobs.mockResolvedValueOnce({ jobs: [job('later', 5)] });
    render(<AudiobookRecovery t={translate} generating={false} onResume={vi.fn()} />);
    expect(await screen.findByText('audiobook.recovery_load_failed')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: 'audiobook.recovery_retry' }));
    expect(await screen.findByText('Book later')).toBeTruthy();
    expect(screen.queryByText('audiobook.recovery_load_failed')).toBeNull();
    expect(doubles.jobs).toHaveBeenCalledTimes(2);
  });

  it('selects the newest audiobook and replaces it after continuation', async () => {
    doubles.jobs
      .mockResolvedValueOnce({
        jobs: [job('early', 10), job('latest', 80), job('story', 99, { type: 'longform' })],
      })
      .mockResolvedValueOnce({ jobs: [job('replacement', 100, { chapters_done: 1 })] });
    const continueJob = vi.fn().mockResolvedValue(true);
    render(<AudiobookRecovery t={translate} generating={false} onResume={continueJob} />);

    expect(await screen.findByText('Book latest')).toBeInTheDocument();
    expect(screen.getByText('4 ready out of 9')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'continue job' }));

    await waitFor(() => expect(continueJob).toHaveBeenCalledWith('latest'));
    expect(await screen.findByText('Book replacement')).toBeInTheDocument();
    expect(doubles.jobs).toHaveBeenCalledTimes(2);
  });

  it('brings the original choice back when the follow-up inventory is unavailable', async () => {
    doubles.jobs.mockResolvedValueOnce({ jobs: [job('retryable', null)] });
    doubles.jobs.mockRejectedValueOnce(new Error('inventory offline'));
    render(
      <AudiobookRecovery
        t={translate}
        generating={false}
        onResume={vi.fn().mockResolvedValue(true)}
      />,
    );
    expect(await screen.findByText('Book retryable')).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'continue job' }));
    expect(await screen.findByText('Book retryable')).toBeInTheDocument();
  });

  it('shows both storage locations and reveals the shared chapter directory', async () => {
    doubles.jobs.mockResolvedValue({ jobs: [job('paths', 5)] });
    doubles.reveal.mockResolvedValue(undefined);
    render(<AudiobookRecovery t={translate} generating={false} onResume={vi.fn()} />);

    expect(await screen.findByText(/audiobook_paths\/resume\.json/)).toBeInTheDocument();
    expect(screen.getByText(/longform_cache/)).toBeInTheDocument();
    fireEvent.click(screen.getByRole('button', { name: 'show audio files' }));
    await waitFor(() =>
      expect(doubles.reveal).toHaveBeenCalledWith({
        path: 'D:\\VoiceStudio\\renders/longform_cache',
      }),
    );
  });

  it('has no recovery chrome when the server returns no audiobook work', async () => {
    doubles.jobs.mockResolvedValue({ jobs: [job('other', 3, { type: 'longform' })] });
    render(<AudiobookRecovery t={translate} generating={false} onResume={vi.fn()} />);
    await waitFor(() => expect(doubles.jobs).toHaveBeenCalledTimes(1));
    expect(screen.queryByText('unfinished book')).not.toBeInTheDocument();
  });
});
