import { cleanup, render, screen } from '@testing-library/react';
import { type ReactElement } from 'react';
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
        <Player />
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
  const tree = (attempt: number) => (
    <LazyBoundary key={attempt} fallback={<i data-testid="fallback" />}>
      <Player />
    </LazyBoundary>
  );
  const { rerender } = render(tree(0));
  expect(await screen.findByTestId('fallback')).toBeTruthy();
  rerender(tree(1)); // a new key is the retry
  expect(await screen.findByTestId('ok')).toBeTruthy();
  expect(load).toHaveBeenCalledTimes(2);
});

it('mounts the loaded component in one commit, without suspending', async () => {
  // A suspended tree's DOM is built before React commits it; a media element in
  // it would fetch its source detached and again once mounted (#3943).
  let resolve!: (module: { default: () => ReactElement }) => void;
  const Player = retryableLazy<() => ReactElement>(
    () => new Promise((done) => (resolve = done)),
    <i data-testid="placeholder" />,
  );
  render(<Player />); // no <Suspense>: suspending here would throw
  expect(screen.getByTestId('placeholder')).toBeTruthy();
  resolve({ default: () => <b data-testid="ok" /> });
  expect(await screen.findByTestId('ok')).toBeTruthy();
  expect(screen.queryByTestId('placeholder')).toBeNull();
  cleanup();
  render(<Player />); // already loaded: renders on the first pass
  expect(screen.getByTestId('ok')).toBeTruthy();
});
