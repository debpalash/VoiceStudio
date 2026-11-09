import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import '@/i18n';
import { GenerationProgress } from './generation-progress';

const NOW = 1_700_000_000_000;

beforeEach(() => {
  vi.useFakeTimers();
  vi.setSystemTime(NOW);
});
afterEach(() => vi.useRealTimers());

const chapters = [
  { title: 'One', status: 'rendering' },
  { title: 'Two', status: 'pending' },
];

it('shows per-chunk progress, time left and the realtime factor mid-chapter (#3417)', () => {
  render(
    <GenerationProgress
      chapters={chapters}
      assembling={false}
      stage="rendering"
      progress={{ fraction: 0.3, etaS: 3725, rtf: 0.45, chunkS: 6, receivedAt: NOW }}
      startedAt={NOW - 1_200_000}
      lastActivityAt={NOW}
    />,
  );
  expect(screen.getByText('30%')).toBeVisible();
  expect(screen.getByText(/~1:02:05 left/)).toBeVisible();
  expect(screen.getByText('0.45× realtime')).toBeVisible();
  expect(screen.queryByRole('alert')).toBeNull();
});

it('says it is estimating until a chunk has been timed', () => {
  render(
    <GenerationProgress
      chapters={chapters}
      assembling={false}
      stage="rendering"
      progress={{ fraction: 0.02, etaS: null, rtf: null, chunkS: null, receivedAt: NOW }}
      startedAt={NOW}
      lastActivityAt={NOW}
    />,
  );
  expect(screen.getByText(/Estimating time left/)).toBeVisible();
});

it('shows a stalled state with where to look when chunks stop landing', () => {
  render(
    <GenerationProgress
      chapters={chapters}
      assembling={false}
      stage="rendering"
      progress={{ fraction: 0.3, etaS: 600, rtf: 0.5, chunkS: 10, receivedAt: NOW - 900_000 }}
      startedAt={NOW - 2_000_000}
      lastActivityAt={NOW - 900_000}
    />,
  );
  const alert = screen.getByRole('alert');
  expect(alert).toHaveTextContent('No progress for 15:00');
  expect(alert).toHaveTextContent('Logs → Backend');
});

it('keeps the elapsed clock from the session across a remount', () => {
  render(
    <GenerationProgress
      chapters={chapters}
      assembling={false}
      startedAt={NOW - 3 * 3600_000}
      lastActivityAt={NOW}
    />,
  );
  expect(screen.getByText(/^3:00:00/)).toBeVisible();
});
