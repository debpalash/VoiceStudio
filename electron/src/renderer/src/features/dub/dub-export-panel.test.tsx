import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import type { DubSession } from './dub-session';
const mocks = vi.hoisted(() => ({ save: vi.fn(), api: vi.fn(), preferences: vi.fn() }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/components/bridge', () => ({ getBridge: () => ({ files: { saveAudio: mocks.save } }) }));
vi.mock('@/lib/api/client', () => ({
  apiJson: mocks.api,
  apiPath: (path: string) => '/api' + path,
  describeError: (error: unknown) => (error instanceof Error ? error.message : String(error)),
  errorFromResponse: vi.fn(),
}));
vi.mock('./dub-session', () => ({ setDubExportPreferences: mocks.preferences }));
vi.mock('@/components/pipeline-failure', () => ({
  PipelineFailure: ({ fallback }: { fallback: string }) => <p role="alert">{fallback}</p>,
}));
import { DubExportPanel } from './dub-export-panel';
const session = (overrides: Partial<DubSession> = {}) =>
  ({
    jobId: 'job',
    inputType: 'video',
    tracks: [],
    exportOptions: { format: 'txt' },
    segments: [
      { id: 'a', start: 0.25, end: 1.5, text: 'Edited & complete', text_original: 'Original' },
    ],
    ...overrides,
  }) as DubSession;
beforeEach(() => {
  vi.clearAllMocks();
  mocks.save.mockResolvedValue({ canceled: false, path: '/chosen/transcript.txt' });
  mocks.api.mockResolvedValue({ ok: true });
});
afterEach(cleanup);

it('offers plain text before a dub, syncs the complete edited transcript and opens native Save As', async () => {
  render(<DubExportPanel session={session()} disabled={false} />);
  expect(screen.getByRole('button', { name: 'transcriptions.export_plain' })).toHaveAttribute(
    'aria-pressed',
    'true',
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_save' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.api.mock.calls[0][0]).toBe('/dub/sync-transcript/job');
  expect(JSON.parse(mocks.api.mock.calls[0][1].body).segments[0].text).toBe('Edited & complete');
  expect(mocks.save).toHaveBeenCalledWith({
    url: '/api/dub/txt/job?lang=original',
    suggestedName: 'voicestudio-job-original-txt.txt',
  });
  expect(screen.queryByRole('switch', { name: 'exportModal.dual_subs' })).toBeNull();
});

it('uses the selected generated language without overwriting its persisted text', async () => {
  render(
    <DubExportPanel
      session={session({
        tracks: ['fr', 'es'],
        exportOptions: { format: 'txt', track: 'fr', dual: true },
      })}
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_save' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.save.mock.calls[0][0].url).toBe('/api/dub/txt/job?lang=fr');
  expect(mocks.api.mock.calls.some(([path]) => path.includes('sync-transcript'))).toBe(false);
});

it('keeps cancellation quiet, then lets the same transcript be saved on retry', async () => {
  mocks.save.mockResolvedValueOnce({ canceled: true });
  render(<DubExportPanel session={session()} disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_save' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledOnce());
  expect(mocks.api.mock.calls.some(([path]) => path === '/export/record')).toBe(false);
  expect(screen.queryByRole('alert')).toBeNull();
  await waitFor(() =>
    expect(screen.getByRole('button', { name: 'dub.flow_save' })).toBeEnabled(),
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_save' }));
  await waitFor(() => expect(mocks.save).toHaveBeenCalledTimes(2));
});

it.each([{ segments: [] }, { jobId: null }])(
  'does not export without a transcript/job: %j',
  (overrides) => {
    render(<DubExportPanel session={session(overrides)} disabled={false} />);
    expect(screen.getByRole('button', { name: 'dub.flow_save' })).toBeDisabled();
    expect(mocks.save).not.toHaveBeenCalled();
  },
);
