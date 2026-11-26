import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubNarrationPanel } from './dub-narration-panel';
import type { DubSession } from './dub-session';
const toggle = vi.hoisted(() => vi.fn());
const native = vi.hoisted(() => ({ save: vi.fn(), reveal: vi.fn() }));
vi.mock('@/lib/native-save', () => ({ saveNativeData: native.save }));
vi.mock('@/components/bridge', () => ({
  getBridge: () => ({ files: { revealPath: native.reveal } }),
}));
vi.mock('./dub-session', () => ({ setDubNarrationEnabled: toggle }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (k: string) => k }) }));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});
const segments = [
  {
    id: 'a',
    start: 0,
    end: 4,
    text: 'Hello world.',
    text_original: 'Hello world.',
    profile_id: 'voice',
  },
];
const session = {
  jobId: 'job',
  filename: 'test.srt',
  phase: 'editing',
  target: 'English',
  sourceLang: 'en',
  segments,
  tracks: [],
  recovery: null,
} as unknown as DubSession;
it('offers consolidation as an explicit step without generating audio', () => {
  render(<DubNarrationPanel session={session} code="en" disabled={false} />);
  fireEvent.click(screen.getByRole('button', { name: 'dub.narration_prepare' }));
  expect(toggle).toHaveBeenCalledWith(true);
});
it('locks changes during generation and permits returning from an empty block script', () => {
  const { rerender } = render(<DubNarrationPanel session={session} code="en" disabled />);
  expect(
    screen.getByRole('button', { name: 'dub.narration_prepare' }).hasAttribute('disabled'),
  ).toBe(true);
  rerender(
    <DubNarrationPanel
      session={{
        ...session,
        segments: [],
        narration: {
          enabled: true,
          language: 'en',
          captions: segments,
          blocks: segments,
          sourceKey: '',
        },
      }}
      code="en"
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.narration_use_captions' }));
  expect(toggle).toHaveBeenCalledWith(false);
});

it('shows the confirmed script destination and can reveal its folder', async () => {
  native.save.mockResolvedValue({ canceled: false, path: '/chosen/script.dub.json' });
  native.reveal.mockResolvedValue(undefined);
  render(
    <DubNarrationPanel
      session={{
        ...session,
        narration: {
          enabled: true,
          language: 'en',
          captions: segments,
          blocks: segments,
          sourceKey: '',
        },
      }}
      code="en"
      disabled={false}
    />,
  );
  fireEvent.click(screen.getByRole('button', { name: 'dub.narration_save' }));
  expect(await screen.findByText('/chosen/script.dub.json')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'dub.export_show_folder' }));
  expect(native.reveal).toHaveBeenCalledWith('/chosen/script.dub.json');
});
