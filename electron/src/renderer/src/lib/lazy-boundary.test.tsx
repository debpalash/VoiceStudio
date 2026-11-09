import { cleanup, render, screen } from '@testing-library/react';
import { Suspense, type ReactElement } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { LazyBoundary, retryableLazy } from './lazy-boundary';

afterEach(cleanup);

it('shows the fallback when the chunk fails and keeps siblings mounted', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  const Player = retryableLazy<() => null>(() => Promise.reject(new Error('chunk')));
  render(
    <div>
      <p>shell</p>
      <LazyBoundary fallback={<i data-testid="fallback" />}>
        <Suspense fallback={null}>
          <Player />
        </Suspense>
      </LazyBoundary>
    </div>,
  );
  expect(await screen.findByTestId('fallback')).toBeTruthy();
  expect(screen.getByText('shell')).toBeTruthy();
});

it('requests the chunk again on the next mount after a failure', async () => {
  vi.spyOn(console, 'error').mockImplementation(() => {});
  vi.spyOn(console, 'warn').mockImplementation(() => {});
  const load = vi
    .fn()
    .mockRejectedValueOnce(new Error('chunk'))
    .mockResolvedValue({ default: () => <b data-testid="ok" /> });
  const Player = retryableLazy<() => ReactElement>(load);
  const tree = () => (
    <LazyBoundary fallback={<i data-testid="fallback" />}>
      <Suspense fallback={null}>
        <Player />
      </Suspense>
    </LazyBoundary>
  );
  render(tree());
  expect(await screen.findByTestId('ok')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});
