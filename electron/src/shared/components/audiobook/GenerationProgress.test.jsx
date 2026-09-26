/**
 * #1321: a failed chapter used to render as a red row with the word "failed"
 * and nothing else. The reason existed only in the backend log, so the report
 * for this arrived as a raw traceback pasted from a log file — the user had no
 * way to see what the app already knew.
 */
import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';

import GenerationProgress from './GenerationProgress.jsx';

// `t` is a required prop (the panel takes the translator from its parent).
const t = (key, vars) => (vars ? `${key} ${JSON.stringify(vars)}` : key);

const CHAPTERS = [
  { title: 'Good chapter', status: 'done' },
  {
    title: 'Bad chapter',
    status: 'failed',
    error: 'the engine stopped without producing a result (StopIteration)',
  },
];

describe('GenerationProgress — failed chapter reason (#1321)', () => {
  it('shows the reason next to the failed chapter', () => {
    render(<GenerationProgress t={t} chapters={CHAPTERS} />);
    expect(screen.getByText(/the engine stopped without producing a result/i)).toBeTruthy();
  });

  it('puts the full reason in the row tooltip, since the row is ellipsised', () => {
    const { container } = render(<GenerationProgress t={t} chapters={CHAPTERS} />);
    const failedRow = container.querySelector('.status-failed');
    expect(failedRow?.getAttribute('title')).toBe(CHAPTERS[1].error);
  });

  it('renders a failed chapter with no reason without printing "undefined"', () => {
    const { container } = render(
      <GenerationProgress t={t} chapters={[{ title: 'Bad', status: 'failed' }]} />,
    );
    const failedRow = container.querySelector('.status-failed');
    expect(failedRow?.textContent).not.toMatch(/undefined/);
    // No reason → no tooltip, rather than an empty one.
    expect(failedRow?.getAttribute('title')).toBeNull();
  });

  it('leaves successful chapters untouched', () => {
    const { container } = render(<GenerationProgress t={t} chapters={CHAPTERS} />);
    const doneRow = container.querySelector('.status-done');
    expect(doneRow?.getAttribute('title')).toBeNull();
  });
});
