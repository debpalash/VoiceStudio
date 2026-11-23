import { act, cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
const mocks = vi.hoisted(() => ({
  watch: { pick: vi.fn(), scan: vi.fn(), enqueue: vi.fn(), stop: vi.fn() },
}));
vi.mock('@/components/bridge', () => ({ getBridge: () => mocks }));
vi.mock('react-i18next', () => ({ useTranslation: () => ({ t: (key: string) => key }) }));
import { WatchFolder } from './watch-folder';
const old = { name: 'old.mp4', size: 10, mtime: 1 };
const next = { name: 'new.mp4', size: 20, mtime: 2 };
beforeEach(() => {
  vi.useFakeTimers();
  mocks.watch.pick.mockResolvedValue({ token: 'selected', path: '/videos' });
  mocks.watch.scan.mockResolvedValue([old]);
  mocks.watch.enqueue.mockResolvedValue(undefined);
  mocks.watch.stop.mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.clearAllMocks();
});
const tick = () =>
  act(async () => {
    await vi.advanceTimersByTimeAsync(5000);
  });
const start = () =>
  act(async () => {
    fireEvent.click(screen.getByRole('button', { name: 'batch.watch_folder' }));
  });
it('skips existing files, settles new files twice and uses the current batch options', async () => {
  const onAdded = vi.fn();
  const view = render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={onAdded} />);
  await start();
  mocks.watch.scan.mockResolvedValue([old, next]);
  await tick();
  expect(mocks.watch.enqueue).not.toHaveBeenCalled();
  view.rerender(
    <WatchFolder langs={['fr']} voiceId="voice" preserveBg={false} onAdded={onAdded} />,
  );
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledWith({
    token: 'selected',
    entry: next,
    langs: ['fr'],
    voiceId: 'voice',
    preserveBg: false,
  });
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(1);
  expect(onAdded).toHaveBeenCalledTimes(1);
});
it('pauses an active watch folder while synthesis is unavailable', async () => {
  const onAdded = vi.fn();
  const view = render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={onAdded} />);
  await start();
  mocks.watch.scan.mockResolvedValue([old, next]);
  await tick();

  view.rerender(<WatchFolder langs={['es']} voiceId="" preserveBg disabled onAdded={onAdded} />);
  await tick();
  expect(mocks.watch.enqueue).not.toHaveBeenCalled();
  expect(screen.getByText('batch.watch_paused')).toBeTruthy();

  view.rerender(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={onAdded} />);
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(1);
});
it('does not ingest after a pause or stop arrives during scanning', async () => {
  render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={vi.fn()} />);
  await start();
  mocks.watch.scan.mockResolvedValue([old, next]);
  await tick();
  let resolve!: (entries: (typeof next)[]) => void;
  mocks.watch.scan.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  await tick();
  fireEvent.click(screen.getByRole('button', { name: 'batch.watch_pause' }));
  await act(async () => {
    resolve([old, next]);
  });
  expect(mocks.watch.enqueue).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole('button', { name: 'batch.watch_resume' }));
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(1);
  fireEvent.click(screen.getByRole('button', { name: 'batch.watch_stop' }));
  await tick();
  expect(mocks.watch.stop).toHaveBeenCalledWith('selected');
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(1);
});
it('revokes a folder selected after unmount', async () => {
  let resolve!: (value: object) => void;
  mocks.watch.pick.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const view = render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={vi.fn()} />);
  await start();
  view.unmount();
  await act(async () => {
    resolve({ token: 'late', path: '/videos' });
  });
  expect(mocks.watch.stop).toHaveBeenCalledWith('late');
  expect(mocks.watch.enqueue).not.toHaveBeenCalled();
});
it('retries a failed enqueue after settling again and stops on inaccessible folders', async () => {
  render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={vi.fn()} />);
  await start();
  mocks.watch.scan.mockResolvedValue([old, next]);
  mocks.watch.enqueue.mockRejectedValueOnce(new Error('temporary'));
  await tick();
  await tick();
  await tick();
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(2);
  mocks.watch.scan.mockRejectedValueOnce(new Error('moved'));
  await tick();
  expect(screen.getByRole('alert').textContent).toContain('batch.watch_failed');
  expect(screen.getByRole('alert').textContent).toContain('moved');
  expect(mocks.watch.stop).toHaveBeenCalledWith('selected');
});

it('defers a settled file if synthesis becomes unavailable during its confirming scan', async () => {
  const onAdded = vi.fn();
  const view = render(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={onAdded} />);
  await start();
  mocks.watch.scan.mockResolvedValue([old, next]);
  await tick();

  let finishScan!: (entries: (typeof next)[]) => void;
  mocks.watch.scan.mockImplementationOnce(
    () => new Promise((resolve) => { finishScan = resolve; }),
  );
  await tick();
  view.rerender(<WatchFolder langs={['es']} voiceId="" preserveBg disabled onAdded={onAdded} />);
  await act(async () => { finishScan([old, next]); });
  expect(mocks.watch.enqueue).not.toHaveBeenCalled();

  view.rerender(<WatchFolder langs={['es']} voiceId="" preserveBg onAdded={onAdded} />);
  await tick();
  expect(mocks.watch.enqueue).toHaveBeenCalledTimes(1);
  expect(onAdded).toHaveBeenCalledTimes(1);
});
