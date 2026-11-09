import React from 'react';
import { beforeEach, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { I18nextProvider } from 'react-i18next';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import i18n from '../i18n';
import en from '../i18n/locales/en.json';

const api = vi.hoisted(() => ({
  plan: vi.fn(),
  generate: vi.fn(),
  uploadCover: vi.fn(),
  previewChapter: vi.fn(),
  importBook: vi.fn(),
  listJobs: vi.fn(),
  resume: vi.fn(),
}));

vi.mock('../api/engines', () => ({
  listEngines: vi.fn().mockResolvedValue({ tts: { active: 'none', backends: [] } }),
}));
vi.mock('../api/generate', () => ({
  audioUrl: (filename) => ['/audio', filename].join('/'),
}));
vi.mock('../api/audiobook', () => ({
  audiobookPlan: api.plan,
  audiobookGenerate: api.generate,
  audiobookUploadCover: api.uploadCover,
  audiobookPreviewChapter: api.previewChapter,
  audiobookImport: api.importBook,
  audiobookListJobs: api.listJobs,
  audiobookResume: api.resume,
}));

import AudiobookTab from '../pages/AudiobookTab';
import { useAppStore } from '../store';

const recoveredJob = 'recover-ab12cd34';
const completedFile = 'rebuilt-novel.m4b';

function recoveredStream() {
  let readCount = 0;
  return {
    body: {
      getReader: () => ({
        read: vi.fn(async () => {
          readCount += 1;
          if (readCount > 1) return { done: true, value: undefined };
          return {
            done: false,
            value: new TextEncoder().encode(
              'data: {"type":"started","chapters":1}\n\n' +
                'data: {"type":"chapter","index":0,"total":1,"title":"Recovered opening","cached":true}\n\n' +
                `data: {"type":"done","output":"${completedFile}"}\n\n`,
            ),
          };
        }),
        cancel: vi.fn().mockResolvedValue(undefined),
      }),
    },
  };
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  api.listJobs
    .mockResolvedValueOnce({
      jobs: [
        {
          job_id: recoveredJob,
          type: 'audiobook',
          status: 'cancelled',
          title: 'The Interrupted Atlas',
          chapters_done: 1,
          total_chapters: 1,
          created_at: 1_812_345_678,
        },
      ],
    })
    .mockResolvedValueOnce({ jobs: [] });
  api.resume.mockResolvedValue(recoveredStream());
  useAppStore.getState().setScript('# Unrelated draft\nThis remains open.');
  useAppStore.getState().setLastOutput('');
});

it('recovers a cancelled render without attaching the open manuscript', async () => {
  const client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client.setQueryData(['system-info'], { outputs_dir: '/tmp/voice-exports' });
  render(
    <QueryClientProvider client={client}>
      <I18nextProvider i18n={i18n}>
        <AudiobookTab profiles={[]} />
      </I18nextProvider>
    </QueryClientProvider>,
  );

  const resumeLabel = await screen.findByText(en.audiobook.resume);
  fireEvent.click(resumeLabel.closest('button'));

  await waitFor(() => expect(api.resume).toHaveBeenCalledTimes(1));
  expect(api.resume).toHaveBeenCalledWith(
    recoveredJob,
    expect.objectContaining({ signal: expect.any(AbortSignal) }),
  );
  await waitFor(() => expect(useAppStore.getState().lastOutput).toBe(completedFile));
  expect(useAppStore.getState().lastOutputScript).toBe('');
});
