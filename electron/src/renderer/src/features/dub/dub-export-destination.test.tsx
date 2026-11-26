import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DubExportPanel } from './dub-export-panel';
import type { DubSession } from './dub-session';
const mocks = vi.hoisted(() => ({
  save: vi.fn(),
  record: vi.fn(),
  complete: vi.fn(),
  reveal: vi.fn(),
}));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
vi.mock('@/lib/export-history', () => ({ saveExport: mocks.save }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { revealPath: mocks.reveal } }),
}));
vi.mock('./dub-session', () => ({
  setDubExportPreferences: vi.fn(),
  recordDubExport: mocks.complete,
  recordDubSavedFile: mocks.record,
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const session = {
  jobId: 'job',
  target: 'English',
  inputType: 'audio',
  tracks: ['en'],
  segments: [],
  exportOptions: { format: 'wav', preserveBg: false },
} as unknown as DubSession;
it('shows the confirmed path and can reveal the file in its folder', async () => {
  const path = '/Users/test/Exports/final.wav';
  mocks.reveal.mockResolvedValue(undefined);
  render(
    <DubExportPanel session={{ ...session, lastExport: { path, savedAt: 1 } }} disabled={false} />,
  );
  expect(screen.getByText(path)).toBeTruthy();
  fireEvent.click(screen.getByText('dub.export_show_folder'));
  expect(mocks.reveal).toHaveBeenCalledWith(path);
});
it.each([true, false])(
  'records a destination only after an uncanceled native save (canceled=%s)',
  async (canceled) => {
    mocks.save.mockResolvedValue({ canceled, path: canceled ? undefined : '/saved/dub.wav' });
    render(<DubExportPanel session={session} disabled={false} />);
    fireEvent.click(screen.getByText('dub.flow_save'));
    await waitFor(() => expect(mocks.save).toHaveBeenCalled());
    if (canceled) {
      expect(mocks.record).not.toHaveBeenCalled();
      expect(mocks.complete).not.toHaveBeenCalled();
    } else
      await waitFor(() => expect(mocks.record).toHaveBeenCalledWith(session, '/saved/dub.wav'));
  },
);
it('does not export the previous dub as a newly prepared narration script', () => {
  render(
    <DubExportPanel
      session={{
        ...session,
        narration: { enabled: true, language: 'en', captions: [], blocks: [], sourceKey: '' },
      }}
      disabled={false}
    />,
  );
  expect(screen.getByText('dub.flow_save').hasAttribute('disabled')).toBe(true);
});
it.each([
  ['txt', false],
  ['wav', true],
] as const)('a %s save completes the audio export step: %s', async (format, completes) => {
  mocks.save.mockResolvedValue({ canceled: false, path: '/saved/dub.' + format });
  render(
    <DubExportPanel
      session={
        {
          ...session,
          segments: [{ id: '1', start: 0, end: 1, text: 'Hi', translations: { en: 'Hi' } }],
          exportOptions: { format, preserveBg: false },
        } as unknown as DubSession
      }
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByText('dub.flow_save'));
  await waitFor(() => expect(mocks.record).toHaveBeenCalled());
  if (completes) expect(mocks.complete).toHaveBeenCalledWith(expect.anything(), 'en');
  else expect(mocks.complete).not.toHaveBeenCalled();
});
