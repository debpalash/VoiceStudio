import { fireEvent, render, screen, within } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import i18n from '@/i18n';

vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));

import { StoryEditor } from './story-editor';
import { blankLongformDraft, type Draft } from './longform-session';

const profiles = [
  { id: 'p1', name: 'Ava' },
  { id: 'p2', name: 'Ben' },
];

function draftWith(lines: Draft['lines'], cast: Draft['cast'] = []) {
  const draft = blankLongformDraft();
  draft.lines = lines;
  draft.cast = cast;
  return draft;
}

it('renders a chapter line as a compact heading without voice controls', () => {
  const draft = draftWith([
    { id: 'h', text: '# The Lamp', profileId: null },
    { id: 'l', text: 'It flickered.', profileId: null },
  ]);
  const { container } = render(
    <StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={vi.fn()} />,
  );
  const [heading, line] = Array.from(container.querySelectorAll('[data-slot="story-block"]'));
  expect(heading).toHaveAttribute('data-kind', 'heading');
  const title = within(heading as HTMLElement).getByRole('textbox', {
    name: i18n.t('stories.chapterHeading'),
  });
  expect(title).toHaveAttribute('rows', '1');
  // Chapter markers are not spoken: no speaker/voice/tone chips, no audition.
  expect(within(heading as HTMLElement).queryByRole('button', { name: /Speaker/ })).toBeNull();
  expect(
    within(heading as HTMLElement).queryByRole('button', { name: i18n.t('stories.preview') }),
  ).toBeNull();
  expect(line).toHaveAttribute('data-kind', 'line');
});

it('auto-sizes line text instead of a tall resizable box', () => {
  const draft = draftWith([{ id: 'l', text: 'One line.', profileId: null }]);
  render(<StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={vi.fn()} />);
  const input = screen.getByRole('textbox', { name: i18n.t('stories.linePlaceholder') });
  const classes = input.className.split(' ');
  expect(classes).toContain('field-sizing-content');
  expect(classes).toContain('resize-none');
  expect(classes).not.toContain('resize-y');
  expect(classes).not.toContain('min-h-24');
});

it('shows one chip row: speaker, effective voice, and speed only when overridden', () => {
  const draft = draftWith(
    [
      { id: 'a', text: 'Hello.', profileId: null, character: 'mara' },
      { id: 'b', text: 'Hi.', profileId: 'p2', speed: 1.25 },
    ],
    [{ id: 'mara', name: 'Mara', profileId: 'p1' }],
  );
  const { container } = render(
    <StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={vi.fn()} />,
  );
  const [first, second] = Array.from(
    container.querySelectorAll<HTMLElement>('[data-slot="story-block"]'),
  );
  // No stray "?" separator and no stacked disclosure rows.
  expect(first.textContent).not.toContain('?');
  expect(first.querySelector('details')).toBeNull();
  within(first).getByRole('button', {
    name: i18n.t('stories.speakerChip', { name: 'Mara' }),
  });
  // Inherited from Mara's cast voice.
  within(first).getByRole('button', { name: i18n.t('stories.voiceChip', { name: 'Ava' }) });
  const firstTone = within(first).getByRole('button', {
    name: i18n.t('stories.toneChip', { speed: '1.00×' }),
  });
  expect(firstTone.textContent).toBe('');

  within(second).getByRole('button', {
    name: i18n.t('stories.speakerChip', { name: i18n.t('stories.speakerNone') }),
  });
  within(second).getByRole('button', { name: i18n.t('stories.voiceChip', { name: 'Ben' }) });
  const secondTone = within(second).getByRole('button', {
    name: i18n.t('stories.toneChip', { speed: '1.25×' }),
  });
  expect(secondTone.textContent).toBe('1.25×');
});

it('names a removed voice profile as unavailable instead of a fallback voice', () => {
  const draft = draftWith(
    [
      { id: 'a', text: 'Hello.', profileId: 'gone' },
      { id: 'b', text: 'Hi.', profileId: null, character: 'mara' },
    ],
    [{ id: 'mara', name: 'Mara', profileId: 'also-gone' }],
  );
  const { container } = render(
    <StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={vi.fn()} />,
  );
  const unavailable = i18n.t('stories.voiceChip', { name: i18n.t('modelSettings.unavailable') });
  for (const block of container.querySelectorAll<HTMLElement>('[data-slot="story-block"]')) {
    within(block).getByRole('button', { name: unavailable });
  }
});

it('assigns a speaker from the chip popover', async () => {
  const onChange = vi.fn();
  const draft = draftWith(
    [{ id: 'a', text: 'Hello.', profileId: null }],
    [{ id: 'mara', name: 'Mara', profileId: null }],
  );
  render(<StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={onChange} />);
  fireEvent.click(
    screen.getByRole('button', {
      name: i18n.t('stories.speakerChip', { name: i18n.t('stories.speakerNone') }),
    }),
  );
  const choices = await screen.findByRole('group', { name: i18n.t('stories.speaker') });
  fireEvent.click(within(choices).getByRole('button', { name: 'Mara' }));
  expect(onChange).toHaveBeenCalledWith({
    lines: [{ id: 'a', text: 'Hello.', profileId: null, character: 'mara' }],
  });
});

it('keeps line actions in a labelled toolbar that appears on hover and keyboard focus', () => {
  const onChange = vi.fn();
  const draft = draftWith([
    { id: 'a', text: 'First.', profileId: null },
    { id: 'b', text: 'Second.', profileId: null },
  ]);
  render(<StoryEditor draft={draft} profiles={profiles} disabled={false} onChange={onChange} />);
  const toolbars = screen.getAllByRole('toolbar', { name: i18n.t('stories.lineActions') });
  expect(toolbars).toHaveLength(2);
  const classes = toolbars[1].className.split(' ');
  expect(classes).toContain('group-hover/block:opacity-100');
  expect(classes).toContain('group-focus-within/block:opacity-100');
  for (const key of ['preview', 'insertPause', 'moveUp', 'moveDown', 'removeLine'])
    within(toolbars[1]).getByRole('button', { name: i18n.t(`stories.${key}`) });
  expect(
    within(toolbars[0]).getByRole('button', { name: i18n.t('stories.moveUp') }),
  ).toBeDisabled();
  fireEvent.click(within(toolbars[1]).getByRole('button', { name: i18n.t('stories.moveUp') }));
  expect(onChange.mock.calls[0][0].lines.map((line: { id: string }) => line.id)).toEqual([
    'b',
    'a',
  ]);
});
