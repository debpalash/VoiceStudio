import { expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@/i18n';
import {
  RenderDetails,
  clock,
  renderRecipe,
  speedLabel,
  type RenderRecord,
} from './render-details';

const record: RenderRecord = {
  job_id: 'j1',
  output: 'story_j1.mp3',
  type: 'story',
  duration_s: 3460,
  chapters: 9,
  summary: {
    engine: 'gpt-sovits',
    voices: [{ id: '8651c9f5', name: 'Jake (fine-tune)' }],
    language: 'English',
    format: 'mp3',
    lines: 18,
    words: 9322,
    speeds: [0.95],
    options: { line_gap_ms: 250, seed: 7 },
  },
};

it('formats durations and speed ranges', () => {
  expect(clock(3460)).toBe('57:40');
  expect(clock(3725)).toBe('1:02:05');
  expect(speedLabel([0.95])).toBe('0.95×');
  expect(speedLabel([1, 0.8])).toBe('0.80–1.00×');
  expect(speedLabel([])).toBe('');
});

it('gives a render a one-line recipe that tells it apart in a list', () => {
  expect(renderRecipe(record)).toBe('Jake (fine-tune) · 0.95× · gpt-sovits · 57:40');
  expect(renderRecipe({ job_id: 'old', output: 'x.mp3' })).toBe('');
  // A voice whose profile was deleted still identifies itself by id.
  expect(
    renderRecipe({ ...record, summary: { voices: [{ id: 'gone', name: '' }], speeds: [1] } }),
  ).toBe('gone · 1.00× · 57:40');
});

it('shows how the render was made, and says so plainly when an old render has no record', () => {
  const view = render(<RenderDetails render={record} />);
  expect(screen.getByText('Jake (fine-tune)')).toBeVisible();
  expect(screen.getByText('9 chapters · 18 lines · 9322 words')).toBeVisible();
  expect(screen.getByText('line gap ms 250 · seed 7')).toBeVisible();
  expect(screen.getByText('MP3')).toBeVisible();
  view.unmount();
  render(<RenderDetails render={{ job_id: 'old', output: 'x.mp3' }} />);
  expect(screen.getByText(/before details were recorded/i)).toBeVisible();
});

it('never throws on a malformed record from an older or hand-edited store', () => {
  const broken = {
    job_id: 'b',
    output: 'b.mp3',
    summary: { voices: 'v1', speeds: 'fast', options: 'x' },
  } as unknown as RenderRecord;
  expect(renderRecipe(broken)).toBe('');
  expect(() => render(<RenderDetails render={broken} />)).not.toThrow();
});
