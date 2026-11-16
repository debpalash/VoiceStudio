import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { DubIssuesPanel } from './dub-issues';
import { dubCueIssues } from './dub-issue-state';
import type { DubSession } from './dub-session';
vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, args?: Record<string, unknown>) =>
      args ? key + ' ' + JSON.stringify(args) : key,
  }),
}));
afterEach(cleanup);
const session = {
  jobId: 'job',
  inputType: 'video',
  segments: [
    {
      id: '992',
      start: 1524.95,
      end: 1525,
      text: 'Right?',
      text_original: 'Right?',
      profile_id: 'voice',
    },
    {
      id: '993',
      start: 1525,
      end: 1525.836,
      text: 'For it to have attributes',
      text_original: 'For it to have attributes',
      profile_id: 'voice',
    },
  ],
} as DubSession;
it('exposes the exact cue and time, with locate and reversible merge actions', () => {
  const focus = vi.fn(),
    merge = vi.fn();
  render(<DubIssuesPanel session={session} disabled={false} onFocus={focus} onMerge={merge} />);
  expect(screen.getByRole('alert')).toBeTruthy();
  expect(screen.getByText('«Right?»')).toBeTruthy();
  expect(screen.getByText(/25:24\.950/)).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'dub.issue_locate' }));
  expect(focus).toHaveBeenCalledWith('992');
  fireEvent.click(screen.getByRole('button', { name: 'dub.issue_merge_next' }));
  expect(merge).toHaveBeenCalledWith('992', 'next');
});
it('does not suggest merging different voices or speakers', () => {
  const different = {
    ...session,
    segments: [session.segments[0], { ...session.segments[1], profile_id: 'other' }],
  };
  expect(dubCueIssues(different)[0].direction).toBeUndefined();
});
it('allows short subtitle-only cues and intentional blank intervals', () => {
  expect(dubCueIssues({ ...session, subtitleOnly: true })).toEqual([]);
  expect(dubCueIssues({ ...session, segments: [{ ...session.segments[0], text: '' }] })).toEqual(
    [],
  );
});
it('distinguishes blocking errors from quality warnings and provides solutions', () => {
  render(
    <DubIssuesPanel
      session={{
        ...session,
        segments: [
          ...session.segments,
          {
            ...session.segments[1],
            id: 'fast',
            start: 1600,
            end: 1601,
            fit_status: { status: 'fits', audio_rate: 2, auto_adjusted: true },
          },
        ],
      }}
      disabled={false}
      onFocus={vi.fn()}
      onMerge={vi.fn()}
    />,
  );
  expect(screen.getByText('dub.issue_blocking')).toBeTruthy();
  expect(screen.getByText('dub.issue_warning')).toBeTruthy();
  expect(screen.getByText('dub.issue_fast_solution')).toBeTruthy();
  expect(screen.getByText('dub.issue_short_solution')).toBeTruthy();
});
