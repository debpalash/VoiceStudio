import { afterEach, expect, it, vi } from 'vitest';
import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import en from '@/i18n/locales/en.json';
import type { DubSegment } from './dub-session';
import { TranscriptIssueNavigation } from './transcript-issue-navigation';

vi.mock('react-i18next', () => ({
  useTranslation: () => ({
    t: (key: string, values: Record<string, number> = {}) => {
      const message = en.dubWorkspace[key.split('.')[1] as keyof typeof en.dubWorkspace];
      return message.replace(/\{\{(\w+)\}\}/g, (_, name) => String(values[name]));
    },
  }),
}));

afterEach(cleanup);

const cue = (id: string, extra: Partial<DubSegment> = {}): DubSegment => ({
  id,
  start: 0,
  end: 1,
  text: id,
  text_original: id,
  ...extra,
});
const plan = (status: 'fits' | 'tight' | 'impossible'): DubSegment['plan'] => ({
  status,
  est_dur_s: 2,
  available_s: 1,
  est_overrun_s: 1,
});
const previous = () => screen.getByRole('button', { name: 'Previous issue' });
const next = () => screen.getByRole('button', { name: 'Next issue' });

it.each<Partial<DubSegment>>([
  { plan: plan('tight') },
  { plan: plan('impossible') },
  { fit_status: { status: 'overflows', overflow_s: 2 } },
  { translate_error: 'Translation failed' },
  { translate_degraded: 'fast' },
  { qc_flagged: true },
])('navigates to an existing transcript warning: %j', (warning) => {
  const onSelect = vi.fn();
  render(
    <TranscriptIssueNavigation
      segments={[cue('clean'), cue('issue', warning)]}
      selectedId={null}
      disabled={false}
      onSelect={onSelect}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 1');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenCalledWith('issue');
});

it('counts each flagged row once and wraps in transcript order', () => {
  const segments = [
    cue('first', { plan: plan('tight'), qc_flagged: true }),
    cue('clean'),
    cue('last', { fit_status: { status: 'overflows' } }),
  ];
  const onSelect = vi.fn();
  const view = render(
    <TranscriptIssueNavigation
      segments={segments}
      selectedId={null}
      disabled={false}
      onSelect={onSelect}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 2');
  fireEvent.click(previous());
  expect(onSelect).toHaveBeenLastCalledWith('last');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('first');
  view.rerender(
    <TranscriptIssueNavigation
      segments={segments}
      selectedId="last"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Issue 2 of 2');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('first');
  view.rerender(
    <TranscriptIssueNavigation
      segments={segments}
      selectedId="first"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  fireEvent.click(previous());
  expect(onSelect).toHaveBeenLastCalledWith('last');
});

it('advances from a resolved current issue without skipping and tolerates deletion', () => {
  const onSelect = vi.fn();
  const segments = [
    cue('first', { qc_flagged: true }),
    cue('resolved'),
    cue('last', { qc_flagged: true }),
  ];
  const view = render(
    <TranscriptIssueNavigation
      segments={segments}
      selectedId="resolved"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('last');
  fireEvent.click(previous());
  expect(onSelect).toHaveBeenLastCalledWith('first');
  view.rerender(
    <TranscriptIssueNavigation
      segments={[segments[0], segments[2]]}
      selectedId="resolved"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('first');
});

it('refreshes warnings when the transcript is replaced', () => {
  const onSelect = vi.fn();
  const view = render(
    <TranscriptIssueNavigation
      segments={[cue('old', { qc_flagged: true })]}
      selectedId="old"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  view.rerender(
    <TranscriptIssueNavigation
      segments={[cue('new', { plan: plan('tight') })]}
      selectedId="old"
      disabled={false}
      onSelect={onSelect}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 1');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('new');
});

it('finds a failed language after another translation succeeds', () => {
  const segments = [
    cue('translated', {
      translations: { de: 'Guten Tag' },
      translate_errors: { fr: 'French translation failed' },
    }),
  ];
  const onSelect = vi.fn();
  const props = { segments, selectedId: null, disabled: false, onSelect };
  const view = render(<TranscriptIssueNavigation {...props} language="de" />);
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 0');
  expect(next()).toBeDisabled();
  view.rerender(<TranscriptIssueNavigation {...props} language="fr" />);
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 1');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('translated');
  view.rerender(<TranscriptIssueNavigation {...props} language="de" />);
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 0');
  expect(next()).toBeDisabled();
});

it('ignores another language error while preserving legacy translation failures', () => {
  const onSelect = vi.fn();
  render(
    <TranscriptIssueNavigation
      segments={[
        cue('other-language', {
          translate_error: 'French failure',
          translate_errors: { fr: 'French failure' },
        }),
        cue('legacy', { translate_error: 'Legacy failure' }),
      ]}
      language="de"
      selectedId={null}
      disabled={false}
      onSelect={onSelect}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 1');
  fireEvent.click(next());
  expect(onSelect).toHaveBeenLastCalledWith('legacy');
});

it('does not invent warnings for unscored or successfully fitted segments', () => {
  render(
    <TranscriptIssueNavigation
      segments={[
        cue('unscored'),
        cue('fit', { plan: plan('fits') }),
        cue('stretched', { fit_status: { status: 'video_stretched' } }),
        cue('slowed', { fit_status: { status: 'audio_slowed' } }),
      ]}
      selectedId={null}
      disabled={false}
      onSelect={vi.fn()}
    />,
  );
  expect(screen.getByRole('status')).toHaveTextContent('Flagged segments: 0');
  expect(next()).toBeDisabled();
  expect(previous()).toBeDisabled();
});

it('disables navigation during generation or recovery', () => {
  const onSelect = vi.fn();
  render(
    <TranscriptIssueNavigation
      segments={[cue('issue', { qc_flagged: true })]}
      selectedId={null}
      disabled
      onSelect={onSelect}
    />,
  );
  fireEvent.click(next());
  fireEvent.click(previous());
  expect(next()).toBeDisabled();
  expect(previous()).toBeDisabled();
  expect(onSelect).not.toHaveBeenCalled();
});
