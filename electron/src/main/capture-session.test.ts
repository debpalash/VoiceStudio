// @vitest-environment node
import { afterEach, expect, it, vi } from 'vitest';
import { CaptureSession, type CaptureEvent } from './capture-session';
import type { OutputCommand } from './dictation-output';

function deferred<T>() {
  let resolve!: (value: T) => void;
  let reject!: (error: Error) => void;
  const promise = new Promise<T>((yes, no) => {
    resolve = yes;
    reject = no;
  });
  return { promise, resolve, reject };
}
function setup() {
  let id = 0;
  const request = vi.fn(async (command: OutputCommand): Promise<unknown> => {
    if (command.method === 'begin') return ++id;
    if (command.method === 'deliver') return 'inserted';
    return null;
  });
  const show = vi.fn();
  const hide = vi.fn();
  const capture = new CaptureSession({ request }, show, hide);
  const events: CaptureEvent[] = [];
  return { capture, request, show, hide, events };
}
afterEach(() => {
  vi.useRealTimers();
});

it('captures focus before showing and queues an early key release until recorder registration', async () => {
  const { capture, request, show, events } = setup();
  const pending = deferred<number>();
  request.mockReturnValueOnce(pending.promise);
  const started = capture.start('shortcut');
  capture.stop();
  expect(show).not.toHaveBeenCalled();
  pending.resolve(7);
  await started;
  capture.listen((event) => events.push(event));
  expect(events).toEqual([
    { session: 7, action: 'start' },
    { session: 7, action: 'stop' },
  ]);
  expect(request.mock.invocationCallOrder[0]).toBeLessThan(show.mock.invocationCallOrder[0]);
  await capture.cancel();
});
it('deduplicates key repeats and stop without recapturing another app', async () => {
  const { capture, request, events } = setup();
  capture.listen((event) => events.push(event));
  await Promise.all([capture.start('shortcut'), capture.start('shortcut')]);
  await capture.start('shortcut');
  capture.stop();
  capture.stop();
  expect(request.mock.calls.filter(([command]) => command.method === 'begin')).toHaveLength(1);
  expect(events.map((event) => event.action)).toEqual(['start', 'stop']);
  await capture.cancel();
});
it('rejects a late candidate after cancellation without waking the recorder', async () => {
  const { capture, request, show } = setup();
  const pending = deferred<number>();
  request.mockReturnValueOnce(pending.promise);
  const started = capture.start('shortcut');
  await capture.cancel();
  pending.resolve(4);
  await started;
  expect(show).not.toHaveBeenCalled();
  expect(request).toHaveBeenLastCalledWith({ method: 'reject', session: 4 });
});
it('requires acceptance and orders utterances before finish', async () => {
  const { capture, request } = setup();
  await capture.start('tray');
  await expect(capture.deliver(1, 0, 'early')).rejects.toThrow('accepted');
  await capture.accept(1);
  const first = capture.deliver(1, 0, 'Hello ');
  const second = capture.deliver(1, 1, 'again.');
  const finished = capture.finish(1);
  await Promise.all([first, second, finished]);
  expect(request.mock.calls.map(([command]) => command.method)).toEqual([
    'begin',
    'activate',
    'deliver',
    'deliver',
    'finish',
  ]);
});
it('never replays a delivery whose acknowledgement failed', async () => {
  const { capture, request } = setup();
  await capture.start('shortcut');
  await capture.accept(1);
  request.mockRejectedValueOnce(new Error('Lost acknowledgement'));
  await expect(capture.deliver(1, 0, 'Once')).rejects.toThrow('Lost acknowledgement');
  await expect(capture.deliver(1, 0, 'Once')).rejects.toThrow('Duplicate');
  await expect(capture.deliver(1, 1, 'Later')).rejects.toThrow('Lost acknowledgement');
  expect(request.mock.calls.filter(([command]) => command.method === 'deliver')).toHaveLength(1);
  await capture.cancel();
});
it('drops queued text on cancel and lets a running delivery finish before cleanup', async () => {
  const { capture, request } = setup();
  await capture.start('shortcut');
  await capture.accept(1);
  const output = deferred<string>();
  request.mockReturnValueOnce(output.promise);
  const first = capture.deliver(1, 0, 'in progress');
  await Promise.resolve();
  const next = expect(capture.deliver(1, 1, 'must not type')).rejects.toThrow('Stale');
  const cancelled = capture.cancel();
  output.resolve('inserted');
  await Promise.all([first, next, cancelled]);
  expect(request.mock.calls.filter(([command]) => command.method === 'deliver')).toHaveLength(1);
});
it('cleans up if recorder acceptance never arrives', async () => {
  vi.useFakeTimers();
  const { capture, request, hide } = setup();
  await capture.start('shortcut');
  await vi.advanceTimersByTimeAsync(15_000);
  expect(hide).toHaveBeenCalled();
  expect(request).toHaveBeenCalledWith({ method: 'reject', session: 1 });
});
it('does not let stale results reach a replacement session', async () => {
  const { capture } = setup();
  await capture.start('shortcut');
  await capture.accept(1);
  await capture.cancel();
  await capture.start('shortcut');
  await capture.accept(2);
  expect(() => capture.deliver(1, 0, 'stale')).toThrow('Stale');
  await expect(capture.deliver(2, 0, 'current')).resolves.toBe('inserted');
  await capture.cancel();
});
it('rejects concurrent recorder registration and cleans up on disconnect', async () => {
  const { capture, request } = setup();
  const unregister = capture.listen(() => {});
  expect(() => capture.listen(() => {})).toThrow('already registered');
  await capture.start('shortcut');
  unregister();
  await vi.waitFor(() => expect(request).toHaveBeenCalledWith({ method: 'reject', session: 1 }));
});
it('surfaces clipboard-only delivery without claiming text was inserted', async () => {
  const { capture, request } = setup();
  await capture.start('shortcut');
  await capture.accept(1);
  request.mockResolvedValueOnce('copied');
  await expect(capture.deliver(1, 0, 'text')).resolves.toBe('copied');
  await capture.finish(1);
});

it('retains the complete transcript when later delivery falls back to clipboard', async () => {
  const { capture, request } = setup();
  await capture.start('tray');
  await capture.accept(1);
  await capture.deliver(1, 0, 'First.');
  request.mockResolvedValueOnce('copied');
  await capture.deliver(1, 1, ' Second.');
  expect(request).toHaveBeenLastCalledWith({ method: 'copy', session: 1, text: 'First. Second.' });
  await capture.finish(1);
});

it('adopts native key-down focus and queues a hold release during recorder startup', async () => {
  const { capture, request, events } = setup();
  await capture.shortcut({ session: 9, pressed: true }, 'hold');
  await capture.shortcut({ session: 9, pressed: false }, 'hold');
  capture.listen((event) => events.push(event));
  expect(events).toEqual([
    { session: 9, action: 'start' },
    { session: 9, action: 'stop' },
  ]);
  expect(request).not.toHaveBeenCalled(); // never recapture after showing the widget
  await capture.cancel();
});
it('toggle ignores release and stops on the next press without replacing the destination', async () => {
  const { capture, request, events } = setup();
  capture.listen((event) => events.push(event));
  await capture.shortcut({ session: 9, pressed: true }, 'toggle');
  await capture.shortcut({ session: 9, pressed: false }, 'toggle');
  expect(events).toEqual([{ session: 9, action: 'start' }]);
  await capture.shortcut({ session: 10, pressed: true }, 'toggle');
  expect(events.at(-1)).toEqual({ session: 9, action: 'stop' });
  expect(request).toHaveBeenCalledWith({ method: 'reject', session: 10 });
  await capture.cancel();
});
it('a mode change during a held key cannot lose its release', async () => {
  const { capture, events } = setup();
  capture.listen((event) => events.push(event));
  await capture.shortcut({ session: 9, pressed: true }, 'hold');
  await capture.shortcut({ session: 9, pressed: false }, 'toggle');
  expect(events.at(-1)).toEqual({ session: 9, action: 'stop' });
  await capture.cancel();
});

it('restarts a visible completed session and preserves release during delivery cleanup', async () => {
  const { capture, request, events } = setup();
  capture.listen((event) => events.push(event));
  await capture.shortcut({ session: 9, pressed: true }, 'hold');
  await capture.accept(9);
  const delivery = deferred<string>();
  request.mockReturnValueOnce(delivery.promise);
  const first = capture.deliver(9, 0, 'finishing');
  await Promise.resolve();
  capture.phase(9, 'done');
  const replacement = capture.shortcut({ session: 10, pressed: true }, 'hold');
  await capture.shortcut({ session: 10, pressed: false }, 'hold');
  delivery.resolve('inserted');
  await first;
  await replacement;
  expect(events.slice(-3)).toEqual([
    { session: 9, action: 'cancel' },
    { session: 10, action: 'start' },
    { session: 10, action: 'stop' },
  ]);
  await capture.cancel();
});
it('allows retry from an error and ignores cancellation from the old recorder session', async () => {
  const { capture, events } = setup();
  capture.listen((event) => events.push(event));
  await capture.shortcut({ session: 9, pressed: true }, 'toggle');
  capture.phase(9, 'error');
  await capture.shortcut({ session: 10, pressed: true }, 'toggle');
  await capture.cancelFor(9);
  await capture.accept(10);
  expect(events.at(-1)).toEqual({ session: 10, action: 'start' });
  await capture.cancel();
});
it('preserves Tauri behavior by rejecting starts while transcription is still running', async () => {
  const { capture, request, events } = setup();
  capture.listen((event) => events.push(event));
  await capture.shortcut({ session: 9, pressed: true }, 'hold');
  capture.stop();
  await capture.shortcut({ session: 10, pressed: true }, 'hold');
  expect(request).toHaveBeenLastCalledWith({ method: 'reject', session: 10 });
  expect(events).toEqual([
    { session: 9, action: 'start' },
    { session: 9, action: 'stop' },
  ]);
  await capture.cancel();
});

it('finishes a failed delivery without replaying text or stranding the recorder', async () => {
  const { capture, request, hide } = setup();
  await capture.start('tray');
  await capture.accept(1);
  request.mockRejectedValueOnce(new Error('Lost acknowledgement'));
  await expect(capture.deliver(1, 0, 'Once')).rejects.toThrow('Lost acknowledgement');
  await expect(capture.deliver(1, 1, 'Later')).rejects.toThrow('Lost acknowledgement');

  await expect(capture.finish(1)).resolves.toBeUndefined();
  expect(request.mock.calls.filter(([command]) => command.method === 'deliver')).toHaveLength(1);
  expect(request).toHaveBeenLastCalledWith({ method: 'finish', session: 1 });
  expect(hide).toHaveBeenCalled();
  expect(capture.canStart()).toBe(true);
  await capture.start('tray');
  await capture.cancel();
});

it('releases the recorder even when native finish itself fails', async () => {
  const { capture, request, hide } = setup();
  await capture.start('tray');
  await capture.accept(1);
  request.mockRejectedValueOnce(new Error('Helper disconnected'));
  await expect(capture.finish(1)).rejects.toThrow('Helper disconnected');
  expect(hide).toHaveBeenCalled();
  expect(capture.canStart()).toBe(true);
});
