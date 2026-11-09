import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubEmptyState } from './dub-empty-state';
import type { DubSession } from './dub-session';
import { DubSourceRow, formatFileSize } from './dub-source-row';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: 'en' } }),
}));

afterEach(cleanup);

const row = (overrides: Partial<Parameters<typeof DubSourceRow>[0]> = {}) => {
  const props = {
    filename: 'Screen Recording 2026-10-26 at 10.00.00.mov',
    inputType: 'video' as const,
    duration: 75,
    size: 12_400_000,
    disabled: false,
    removable: true,
    linkMode: false,
    confirmRemove: false,
    recovering: false,
    onChangeFile: vi.fn(),
    onToggleLink: vi.fn(),
    onRemove: vi.fn(),
    ...overrides,
  };
  render(<DubSourceRow {...props} />);
  return props;
};

it('keeps the file on one row with its full name, length and size', () => {
  row();
  const name = screen.getByText('Screen Recording 2026-10-26 at 10.00.00.mov');
  expect(name.getAttribute('title')).toBe('Screen Recording 2026-10-26 at 10.00.00.mov');
  expect(name.className).toContain('truncate');
  expect(screen.getByText('1:15 · 12 MB')).toBeTruthy();
  // One overflow trigger replaces the three wrapping text buttons.
  expect(screen.getAllByRole('button')).toHaveLength(1);
  expect(screen.getByRole('button', { name: 'dub.file_actions' })).toBeTruthy();
});

it('shows a missing audio track as a calm note on the file row', () => {
  row({ issue: 'no_audio' });
  expect(screen.getByRole('status').textContent).toBe('dub.input_issue_no_audio');
  expect(screen.queryByRole('alert')).toBeNull();
  expect(screen.queryByText('dub.copy_diagnostic')).toBeNull();
});

it('opens the file actions menu', async () => {
  const props = row();
  const trigger = screen.getByRole('button', { name: 'dub.file_actions' });
  trigger.focus();
  fireEvent.click(trigger);
  fireEvent.click(await screen.findByRole('menuitem', { name: 'dub.use_link' }));
  expect(props.onToggleLink).toHaveBeenCalledOnce();
});

it('formats sizes for the locale and hides unknown sizes', () => {
  expect(formatFileSize(undefined, 'en')).toBe('');
  expect(formatFileSize(0, 'en')).toBe('');
  expect(formatFileSize(2_500_000_000, 'en')).toBe('2.5 GB');
});

const session = (overrides: Partial<DubSession>): DubSession =>
  ({
    jobId: 'job',
    filename: 'clip.mov',
    inputType: 'video',
    phase: 'idle',
    segments: [],
    ...overrides,
  }) as DubSession;

it('replaces the welcome placeholder with the loaded source and its status', () => {
  const onChangeFile = vi.fn();
  render(
    <DubEmptyState
      session={session({ inputIssue: 'no_audio' })}
      busy={false}
      stage="dub.prep_preparing"
      onChangeFile={onChangeFile}
    />,
  );
  expect(screen.queryByText('dub.video_dubbing_studio')).toBeNull();
  expect(screen.queryByText('dub.supported_formats')).toBeNull();
  expect(screen.getByText('dub.input_issue_no_audio')).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'dub.change_file' }));
  expect(onChangeFile).toHaveBeenCalledOnce();
  expect(screen.getByText('dub.upload_transcribe').closest('[aria-current]')).toBeTruthy();
});

it('keeps the placeholder only while nothing is loaded', () => {
  render(
    <DubEmptyState
      session={session({ jobId: null, filename: '' })}
      busy={false}
      stage=""
      onChangeFile={vi.fn()}
    />,
  );
  expect(screen.getByText('dub.video_dubbing_studio')).toBeTruthy();
  expect(screen.queryByRole('list')).toBeNull();
});
