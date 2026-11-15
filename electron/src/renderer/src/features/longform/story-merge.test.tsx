import { fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import { beforeEach, expect, it, vi } from 'vitest';
import i18n from '@/i18n';

vi.mock('@/components/waveform-player', () => ({ WaveformPlayer: () => null }));
vi.mock('./story-preview', () => ({ previewStoryLine: vi.fn() }));
vi.mock('./story-stems', () => ({ StoryStems: () => null }));
vi.mock('@/components/pipeline-failure', () => ({ PipelineFailure: () => null }));

import { StoryEditor } from './story-editor';
import { previewStoryLine } from './story-preview';
import {
  blankLongformDraft,
  editLongform,
  flushLongformSessionPersistence,
  longformSession,
  renderBody,
  useLongformSession,
  type Draft,
  type Line,
} from './longform-session';

const profiles = [
  { id: 'actor', name: 'Ava' },
  { id: 'guest', name: 'Ben' },
];
const line = (id: string, text: string, extra: Partial<Line> = {}): Line => ({
  id,
  text,
  character: 'mara',
  profileId: null,
  ...extra,
});
const draftWith = (lines: Line[]): Draft => ({
  ...blankLongformDraft(),
  voice: 'actor',
  cast: [{ id: 'mara', name: 'Mara', profileId: 'actor' }],
  lines,
});
function renderedChapters(draft: Draft) {
  const body = renderBody('stories', draft);
  if (!('chapters' in body)) throw new Error('Expected Stories render plan');
  return body.chapters;
}
function Editor({ disabled = false }: { disabled?: boolean }) {
  const session = useLongformSession();
  return (
    <StoryEditor
      draft={session.drafts.stories}
      profiles={profiles}
      disabled={disabled}
      onChange={(patch) => editLongform('stories', patch)}
    />
  );
}
function mergeButton(index = 0) {
  return within(
    screen.getAllByRole('toolbar', { name: i18n.t('stories.lineActions') })[index],
  ).getByRole('button', { name: i18n.t('stories.mergeNext') });
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.clear();
  editLongform('stories', blankLongformDraft());
});

it('preserves whitespace, blank lines and repeated merges without changing the surviving id', () => {
  editLongform(
    'stories',
    draftWith([
      line('first', ' First. '),
      line('blank', ''),
      line('second', ' Second.\n\nThird. '),
    ]),
  );
  render(<Editor />);
  fireEvent.click(mergeButton());
  fireEvent.click(mergeButton());
  expect(longformSession.state.drafts.stories.lines).toEqual([
    line('first', ' First. \n\n Second.\n\nThird. '),
  ]);
});

it('does not turn incomplete directives across two old lines into a new direction', () => {
  editLongform(
    'stories',
    draftWith([line('first', 'First [emotion:'), line('second', 'quiet]Second.')]),
  );
  render(<Editor />);
  fireEvent.click(mergeButton());
  const spans = renderedChapters(longformSession.state.drafts.stories)[0].spans;
  expect(spans.map((span) => span.text)).toEqual(['First [emotion:', 'quiet]Second.']);
  expect(spans.every((span) => !('emotion' in span))).toBe(true);
});

it('prevents merging during a line audition even before the parent marks the editor busy', async () => {
  vi.mocked(previewStoryLine).mockImplementation(
    (_draft, _line, signal) =>
      new Promise((_resolve, reject) => {
        signal.addEventListener('abort', () => reject(new DOMException('Stopped', 'AbortError')));
      }),
  );
  const draft = draftWith([line('first', 'First.'), line('second', 'Second.')]);
  editLongform('stories', draft);
  render(<Editor />);
  fireEvent.click(screen.getAllByRole('button', { name: i18n.t('stories.preview') })[0]);
  expect(mergeButton()).toHaveAttribute('aria-disabled', 'true');
  fireEvent.click(mergeButton());
  expect(longformSession.state.drafts.stories.lines).toEqual(draft.lines);
  fireEvent.click(screen.getByRole('button', { name: i18n.t('common.stop') }));
  await waitFor(() => expect(mergeButton()).toBeEnabled());
});

it('merges basic prose through the editor, persists the draft and renders one spoken span', () => {
  editLongform(
    'stories',
    draftWith([
      line('chapter', '# Opening'),
      line('first', 'First.'),
      line('second', 'Second.'),
      line('end', '# Ending'),
      line('tail', 'Last.'),
    ]),
  );
  render(<Editor />);
  fireEvent.click(mergeButton(1));
  expect(longformSession.state.drafts.stories.lines).toEqual([
    line('chapter', '# Opening'),
    line('first', 'First.\nSecond.'),
    line('end', '# Ending'),
    line('tail', 'Last.'),
  ]);
  flushLongformSessionPersistence();
  const saved = JSON.parse(localStorage.getItem('voicestudio.longform.v1')!).stories;
  expect(saved.lines).toEqual(longformSession.state.drafts.stories.lines);
  expect(renderedChapters(saved)).toEqual([
    {
      title: 'Opening',
      spans: [{ voice_id: 'actor', text: 'First.\nSecond.', pause_ms_after: 0, speed: null }],
    },
    {
      title: 'Ending',
      spans: [{ voice_id: 'actor', text: 'Last.', pause_ms_after: 0, speed: null }],
    },
  ]);
});

it.each([
  [
    '[voice:guest][emotion:quiet]First.',
    'Second.',
    [
      { voice_id: 'guest', text: 'First.', emotion: 'quiet', speed: 0.8 },
      { voice_id: 'actor', text: 'Second.', speed: 0.8 },
    ],
  ],
  [
    '[slow][spell]ABC',
    'Second.',
    [
      { voice_id: 'actor', text: 'A B C', speed: 0.85 },
      { voice_id: 'actor', text: 'Second.', speed: 0.8 },
    ],
  ],
  [
    '[emotion:quiet]First.',
    '[emotion:loud]Second.',
    [
      { voice_id: 'actor', text: 'First.', emotion: 'quiet', speed: 0.8 },
      { voice_id: 'actor', text: 'Second.', emotion: 'loud', speed: 0.8 },
    ],
  ],
  [
    'First. [pause 10s]',
    '[pause 10s] Second.',
    [
      { voice_id: 'actor', text: 'First.', speed: 0.8 },
      { voice_id: 'actor', text: 'Second.', speed: 0.8 },
    ],
  ],
])('keeps voice, emotion, prosody and pause scopes when merging %s', (first, second, expected) => {
  const source = [
    line('first', first, { profileId: 'actor', speed: 0.8 }),
    line('second', second, { profileId: 'actor', speed: 0.8 }),
  ];
  editLongform('stories', draftWith(source));
  const before = renderedChapters(longformSession.state.drafts.stories)[0].spans;
  render(<Editor />);
  fireEvent.click(mergeButton());
  const merged = longformSession.state.drafts.stories;
  expect(merged.lines).toHaveLength(1);
  expect(merged.lines[0]).toMatchObject({ id: 'first', profileId: 'actor', speed: 0.8 });
  expect(merged.lines[0].text).toBe(`${first}\n[voice:]${second}`);
  flushLongformSessionPersistence();
  const saved = JSON.parse(localStorage.getItem('voicestudio.longform.v1')!).stories;
  const spans = renderedChapters(saved)[0].spans;
  expect(spans.filter((span) => span.text).map(({ pause_ms_after, ...span }) => span)).toEqual(
    expected,
  );
  expect(spans.reduce((total, span) => total + span.pause_ms_after, 0)).toBe(
    before.reduce((total, span) => total + span.pause_ms_after, 0),
  );
});

it.each([{ character: 'someone-else' }, { profileId: 'actor' }, { speed: 1 }])(
  'keeps distinct explicit line controls separate: %j',
  (difference) => {
    const draft = draftWith([line('first', 'First.'), line('second', 'Second.', difference)]);
    editLongform('stories', draft);
    render(<Editor />);
    expect(mergeButton()).toHaveAttribute('aria-disabled', 'true');
    fireEvent.click(mergeButton());
    expect(longformSession.state.drafts.stories.lines).toEqual(draft.lines);
  },
);

it('does not merge a chapter marker, across a chapter marker, or the final line', () => {
  const draft = draftWith([line('a', 'First.'), line('h', '# Other'), line('b', 'Second.')]);
  editLongform('stories', draft);
  render(<Editor />);
  expect(mergeButton(0)).toHaveAttribute('aria-disabled', 'true');
  expect(
    within(screen.getAllByRole('toolbar')[1]).queryByRole('button', {
      name: i18n.t('stories.mergeNext'),
    }),
  ).toBeNull();
  expect(mergeButton(2)).toHaveAttribute('aria-disabled', 'true');
});

it('locks merging while the editor is busy and keeps the button keyboard focusable when idle', () => {
  const draft = draftWith([line('first', 'First.'), line('second', 'Second.')]);
  editLongform('stories', draft);
  const view = render(<Editor disabled />);
  expect(mergeButton()).toBeDisabled();
  fireEvent.click(mergeButton());
  expect(longformSession.state.drafts.stories.lines).toEqual(draft.lines);
  view.rerender(<Editor />);
  mergeButton().focus();
  expect(mergeButton()).toHaveFocus();
  fireEvent.click(mergeButton(), { detail: 0 });
  expect(longformSession.state.drafts.stories.lines).toHaveLength(1);
});
