import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import i18n from '../i18n';
import en from '../i18n/locales/en.json';

const mocks = vi.hoisted(() => ({
  jobs: vi.fn(),
  resume: vi.fn(),
  reveal: vi.fn(),
}));

vi.mock('../api/audiobook', () => ({
  audiobookListJobs: mocks.jobs,
  audiobookResume: mocks.resume,
  audiobookPlan: vi.fn(),
  audiobookGenerate: vi.fn(),
  audiobookUploadCover: vi.fn(),
  audiobookPreviewChapter: vi.fn(),
  audiobookImport: vi.fn(),
}));
vi.mock('../api/exports', () => ({ exportReveal: mocks.reveal }));
vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'x', backends: [] } }),
}));
vi.mock('../api/generate', () => ({ audioUrl: (file) => `/audio/${file}` }));
vi.mock('../api/system', () => ({
  systemInfo: vi.fn().mockResolvedValue({ outputs_dir: '/data/outputs' }),
}));

import AudiobookRecovery from '../components/audiobook/AudiobookRecovery';
import AudiobookTab from '../pages/AudiobookTab';
import { useAppStore } from '../store';

function renderRecovery(onResume = vi.fn()) {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  return {
    onResume,
    ...render(
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}>
          <AudiobookRecovery t={i18n.t.bind(i18n)} generating={false} onResume={onResume} />
        </I18nextProvider>
      </QueryClientProvider>,
    ),
  };
}

describe('Audiobook recovery surface (#1911)', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.clearAllMocks();
    mocks.jobs.mockResolvedValue({
      jobs: [
        {
          job_id: 'job-safe-123',
          type: 'audiobook',
          status: 'cancelled',
          title: 'A Long Book',
          total_chapters: 30,
          chapters_done: 6,
          created_at: 1,
        },
      ],
    });
    mocks.reveal.mockResolvedValue({ success: true });
    useAppStore.getState().setLastOutput('');
    mocks.resume.mockResolvedValue({
      body: {
        getReader: () => {
          let sent = false;
          return {
            read: () => {
              if (sent) return Promise.resolve({ done: true });
              sent = true;
              return Promise.resolve({
                done: false,
                value: new TextEncoder().encode(
                  'data: {"type":"started","job_id":"fresh","chapters":1}\n\n' +
                    'data: {"type":"chapter","index":0,"total":1,"title":"One","cached":true}\n\n' +
                    'data: {"type":"done","output":"audiobook_fresh.m4b","cached_chapters":1,"failed_chapters":[]}\n\n',
                ),
              });
            },
            cancel: vi.fn().mockResolvedValue(undefined),
          };
        },
      },
    });
  });

  it('shows interrupted work and resumes the selected backend job', async () => {
    const onResume = vi.fn().mockResolvedValue(true);
    renderRecovery(onResume);

    expect(await screen.findByRole('heading', { name: en.audiobook.recovery_title })).toBeTruthy();
    expect(screen.getByText('A Long Book')).toBeTruthy();
    expect(screen.getByText('6 of 30 chapters are cached', { exact: false })).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: en.audiobook.resume }));
    expect(onResume).toHaveBeenCalledWith('job-safe-123');
    mocks.jobs.mockResolvedValue({ jobs: [] });
    await waitFor(() => expect(screen.queryByText('A Long Book')).toBeNull());
  });

  it('refreshes recovery inventory after an interrupted resumed render', async () => {
    const onResume = vi.fn().mockImplementation(async () => {
      mocks.jobs.mockResolvedValue({
        jobs: [
          {
            job_id: 'job-fresh-456',
            type: 'audiobook',
            status: 'cancelled',
            title: 'A Long Book (resumed)',
            total_chapters: 30,
            chapters_done: 9,
            created_at: 2,
          },
        ],
      });
      return true;
    });
    renderRecovery(onResume);

    fireEvent.click(await screen.findByRole('button', { name: en.audiobook.resume }));

    expect(await screen.findByText('A Long Book (resumed)')).toBeTruthy();
    expect(mocks.jobs).toHaveBeenCalledTimes(2);
  });

  it('restores the recovery card when the post-resume inventory refresh fails', async () => {
    const onResume = vi.fn().mockImplementation(async () => {
      mocks.jobs.mockRejectedValueOnce(new Error('inventory offline'));
      return true;
    });
    renderRecovery(onResume);

    fireEvent.click(await screen.findByRole('button', { name: en.audiobook.resume }));

    expect(await screen.findByText('A Long Book')).toBeTruthy();
    expect(mocks.jobs).toHaveBeenCalledTimes(2);
  });

  it('reveals the server-owned chapter cache through the existing safe seam', async () => {
    renderRecovery();

    fireEvent.click(await screen.findByRole('button', { name: en.audiobook.open_chapter_cache }));
    await waitFor(() =>
      expect(mocks.reveal).toHaveBeenCalledWith({ path: '/data/outputs/longform_cache' }),
    );
    expect(screen.getByText(/\/data\/outputs\/longform_cache/)).toBeTruthy();
    expect(screen.getByText(/\/data\/outputs\/audiobook_job-safe-123\/resume\.json/)).toBeTruthy();
  });

  it('stays out of the workspace when no resumable audiobook exists', async () => {
    mocks.jobs.mockResolvedValue({ jobs: [] });
    renderRecovery();

    await waitFor(() => expect(mocks.jobs).toHaveBeenCalledOnce());
    expect(screen.queryByRole('heading', { name: en.audiobook.recovery_title })).toBeNull();
  });

  it('wires Resume to the backend stream and restores a playable result', async () => {
    useAppStore.getState().setScript('# Different open draft\nDo not attach this as lyrics.');
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    render(
      <QueryClientProvider client={queryClient}>
        <I18nextProvider i18n={i18n}>
          <AudiobookTab profiles={[]} />
        </I18nextProvider>
      </QueryClientProvider>,
    );

    fireEvent.click(await screen.findByRole('button', { name: en.audiobook.resume }));
    await waitFor(() => expect(mocks.resume).toHaveBeenCalledOnce());
    expect(mocks.resume.mock.calls[0][0]).toBe('job-safe-123');
    expect(mocks.resume.mock.calls[0][1].signal).toBeInstanceOf(AbortSignal);
    await waitFor(() => expect(useAppStore.getState().lastOutput).toBe('audiobook_fresh.m4b'));
    expect(screen.getByText(en.audiobook.ready, { exact: false })).toBeTruthy();
    expect(useAppStore.getState().lastOutputScript).not.toContain('Different open draft');
  });
});
