import { cleanup, render, screen } from '@testing-library/react';
import { type ReactElement, useLayoutEffect } from 'react';
import { createRoot } from 'react-dom/client';
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

it('shows a player whose module resolves between its render and its effect', async () => {
  let resolve!: (module: { default: () => ReactElement }) => void;
  const Player = retryableLazy<() => ReactElement>(
    () => new Promise((done) => (resolve = done)),
    <i data-testid="placeholder" />,
  );
  const host = document.createElement('div');
  document.body.append(host);
  const root = createRoot(host);
  const env = globalThis as { IS_REACT_ACT_ENVIRONMENT?: boolean };
  env.IS_REACT_ACT_ENVIRONMENT = false; // let passive effects run after microtasks
  try {
    root.render(<Player />);
    await new Promise((done) => setTimeout(done, 20)); // first instance is loading
    // The layout effect runs in the commit, before passive effects: the module
    // resolves after the second instance rendered but before its effect runs.
    const Resolve = () => {
      useLayoutEffect(() => resolve({ default: () => <b data-testid="ok" /> }), []);
      return null;
    };
    root.render(
      <>
        <Player />
        <Player />
        <Resolve />
      </>,
    );
    await new Promise((done) => setTimeout(done, 50));
    expect(host.querySelectorAll('[data-testid="ok"]').length).toBe(2);
    expect(host.querySelector('[data-testid="placeholder"]')).toBeNull();
  } finally {
    root.unmount();
    host.remove();
    env.IS_REACT_ACT_ENVIRONMENT = true;
  }
});
