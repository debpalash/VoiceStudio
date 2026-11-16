import { afterEach, expect, it, vi } from 'vitest';
import { createRef } from 'react';
import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { DubSourcePanel } from './dub-source-panel';
import { dubSession } from './dub-session';
import { dubInputSelection } from './dub-input-selection';
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key, i18n: {language: 'en'} }) }));
vi.mock('@/features/clone/language-picker', () => ({ LanguagePicker: () => null }));
vi.mock('./dub-url-form', () => ({ DubUrlForm: () => null }));
afterEach(() => {
  cleanup();
  dubInputSelection.setState(() => ({ profileId: '', submitting: false }));
});
function setup(busy = false) {
  const onFile = vi.fn().mockResolvedValue(false),
    onSubtitles = vi.fn().mockResolvedValue(false);
  const props = {
    session: {
      ...dubSession.state,
      jobId: null,
      recovery: null,
      sourceLanguage: 'es',
      target: 'English',
    },
    busy,
    cancelling: false,
    inputRef: createRef<HTMLInputElement>(),
    removable: false,
    confirmRemove: false,
    onFile,
    onSubtitles,
    onUrl: vi.fn(),
    onRemove: vi.fn(),
  };
  return { ...render(<DubSourcePanel {...props} />), props, onFile, onSubtitles };
}
const next = () => fireEvent.click(screen.getByRole('button', { name: 'dub.guide_continue' }));
function pick(container: HTMLElement, kind: 'media' | 'srt') {
  const file = new File(
    [kind === 'media' ? 'video' : '1\n00:00:00,000 --> 00:00:01,000\nHello'],
    kind === 'media' ? 'video.mp4' : 'captions.srt',
    { type: kind === 'media' ? 'video/mp4' : 'application/x-subrip' },
  );
  fireEvent.change(
    container.querySelector(kind === 'media' ? 'input[accept^="audio"]' : 'input[accept^=".srt"]')!,
    { target: { files: [file] } },
  );
  return file;
}
function toSummary() {
  next();
  next();
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_summary' }));
}
it('stages video and target SRT, explains skipped work, then imports without requiring a voice', async () => {
  const { container, onFile, onSubtitles } = setup();
  const media = pick(container, 'media');
  const srt = pick(container, 'srt');
  expect(onFile).not.toHaveBeenCalled();
  expect(onSubtitles).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('radio', { name: /dub.flow_srt_target/ }));
  toSummary();
  expect(screen.getByText('dub.skip_asr_hint')).toBeTruthy();
  expect(screen.getByText('dub.srt_target_hint')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_import' }));
  await waitFor(() =>
    expect(onFile).toHaveBeenCalledWith(media, srt, '', {
      separateBackground: false,
      subtitlesInTarget: true,
      narrationWanted: false,
    }),
  );
  expect(dubInputSelection.state.media).toBe(media);
  expect(dubInputSelection.state.captions).toBe(srt);
});
it('requires an explicit SRT decision before preparation', () => {
  const { container, onFile } = setup();
  pick(container, 'media');
  expect(screen.getByRole('button', { name: 'dub.guide_continue' }).hasAttribute('disabled')).toBe(
    true,
  );
  fireEvent.click(screen.getByRole('radio', { name: /dub.import_srt/ }));
  expect(screen.getByRole('button', { name: 'dub.guide_continue' }).hasAttribute('disabled')).toBe(
    true,
  );
  expect(onFile).not.toHaveBeenCalled();
});
it('without SRT, skips its language question and confirms transcription plus optional background separation', async () => {
  const { container, onFile } = setup();
  const media = pick(container, 'media');
  fireEvent.click(screen.getByRole('radio', { name: /dub.transcribe/ }));
  next();
  fireEvent.click(screen.getByRole('radio', { name: /dub.flow_background_yes/ }));
  next();
  fireEvent.click(screen.getByRole('radio', { name: /dub.narration_prepare/ }));
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_summary' }));
  fireEvent.click(screen.getByRole('button', { name: 'dub.prepare_project' }));
  await waitFor(() =>
    expect(onFile).toHaveBeenCalledWith(media, undefined, '', {
      separateBackground: true,
      subtitlesInTarget: false,
      narrationWanted: true,
    }),
  );
});
it('imports subtitle-only material without media, separation or an initial voice', async () => {
  const { container, onSubtitles } = setup();
  const srt = pick(container, 'srt');
  fireEvent.click(screen.getByRole('radio', { name: /dub.flow_srt_original/ }));
  next();
  expect(screen.queryByRole('radio', { name: /dub.flow_background_yes/ })).toBeNull();
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_summary' }));
  fireEvent.click(screen.getByRole('button', { name: 'dub.flow_import' }));
  await waitFor(() =>
    expect(onSubtitles).toHaveBeenCalledWith(srt, '', {
      subtitlesInTarget: false,
      narrationWanted: false,
    }),
  );
});
it('keeps files and the current question across a panel remount', () => {
  const first = setup();
  const media = pick(first.container, 'media');
  const srt = pick(first.container, 'srt');
  first.unmount();
  setup();
  expect(dubInputSelection.state).toMatchObject({ media, captions: srt, step: 2 });
  expect(screen.getByRole('radio', { name: /dub.flow_srt_target/ })).toBeTruthy();
});
it('locks preparation decisions while a job is running', () => {
  const first = setup();
  pick(first.container, 'media');
  first.unmount();
  setup(true);
  expect(screen.getByRole('radio', { name: /dub.import_srt/ }).hasAttribute('disabled')).toBe(true);
});
it('shows the loaded project summary without next-import switches', () => {
  const { props, rerender } = setup();
  rerender(
    <DubSourcePanel
      {...props}
      session={{
        ...props.session,
        jobId: 'saved',
        filename: 'existing.mp4',
        segments: [],
        separateBackground: false,
        subtitleTargetLang: 'en',
      }}
    />,
  );
  expect(screen.getByText('existing.mp4')).toBeTruthy();
  expect(screen.queryByRole('radio')).toBeNull();
  expect(screen.queryByText('dub.next_srt_import')).toBeNull();
});
