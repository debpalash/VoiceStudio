import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import i18n from '@/i18n';
import {
  RenderDetails,
  clock,
  renderRecipe,
  speedLabel,
  voiceLabel,
  type RenderRecord,
} from './render-details';

const base: RenderRecord = { job_id: 'j', output: 'o.m4b', duration_s: 3725, chapters: 2 };

it('formats clocks and speed ranges', () => {
  expect(clock(undefined)).toBe('0:00');
  expect(clock(-4)).toBe('0:00');
  expect(clock(59.6)).toBe('1:00');
  expect(clock(3725)).toBe('1:02:05');
  expect(speedLabel(undefined)).toBe('');
  expect(speedLabel([Number.NaN])).toBe('');
  expect(speedLabel([1])).toBe('1.00×');
  expect(speedLabel([1.25, 0.9, 1])).toBe('0.90–1.25×');
});

it('builds a one-line recipe and tolerates malformed summaries', () => {
  const summary = {
    engine: 'omnivoice',
    voices: [
      { id: 'a', name: 'Mara' },
      { id: 'b', name: '' },
    ],
    speeds: [1],
  };
  expect(voiceLabel(summary)).toBe('Mara, b');
  expect(renderRecipe({ ...base, summary })).toBe('Mara, b · 1.00× · omnivoice · 1:02:05');
  expect(renderRecipe({ job_id: 'j', output: 'o' })).toBe('');
  const broken = { voices: 'x', speeds: 'y', options: 5 } as never;
  expect(renderRecipe({ ...base, summary: broken })).toBe('1:02:05');
  expect(() => render(<RenderDetails render={{ ...base, summary: broken }} />)).not.toThrow();
});

it('says so for renders made before details were recorded', () => {
  render(<RenderDetails render={base} />);
  expect(screen.getByText(i18n.t('projects.render_no_details'))).toBeInTheDocument();
});

it('lists known settings only, with translated booleans', () => {
  render(
    <RenderDetails
      render={{
        ...base,
        summary: {
          engine: 'eng',
          language: 'en',
          format: 'm4b',
          lines: 4,
          words: 20,
          options: { seed: 0, trim_edges: true, toString: 'x', unknown: 1 },
        },
      }}
    />,
  );
  const enabled = i18n.t('pronunciation.enabled');
  expect(
    screen.getByText(
      `${i18n.t('audiobook.seed')} 0 · ${i18n.t('audiobook.trim_edges')} ${enabled}`,
    ),
  ).toBeInTheDocument();
  expect(screen.getByText('M4B')).toBeInTheDocument();
  expect(screen.queryByText(/unknown/)).toBeNull();
});
