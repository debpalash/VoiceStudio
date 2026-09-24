import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { FakeEventSource } from '@/test/fake-event-source';
import { reconnectDelay, useCallEvents } from './use-call-events';

beforeEach(() => {
  FakeEventSource.instances = [];
  vi.stubGlobal('EventSource', FakeEventSource);
  vi.useFakeTimers();
});
afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it('delivers named and unnamed events and closes for good on "ended"', () => {
  const onEvent = vi.fn();
  const { result } = renderHook(() => useCallEvents('c1', true, onEvent, vi.fn()));
  const source = FakeEventSource.latest();
  expect(source.url).toBe('/api/calls/c1/events');
  act(() => source.open());
  expect(result.current).toBe('open');
  act(() => source.emit({ type: 'status', status: 'ringing' }));
  act(() => source.emit({ state: 'speaking' }, 'agent_state'));
  act(() => source.emit({ type: 'ended', duration_s: 3 }));
  expect(onEvent.mock.calls.map(([event]) => event.type)).toEqual([
    'status',
    'agent_state',
    'ended',
  ]);
  expect(source.readyState).toBe(FakeEventSource.CLOSED);
  expect(result.current).toBe('closed');
});

it('reopens with backoff after the server drops the stream, then resyncs', () => {
  const onResync = vi.fn();
  const { result } = renderHook(() => useCallEvents('c1', true, vi.fn(), onResync));
  const first = FakeEventSource.latest();
  act(() => first.open());
  act(() => first.fail(true));
  expect(result.current).toBe('reconnecting');
  expect(FakeEventSource.instances).toHaveLength(1);
  act(() => vi.advanceTimersByTime(reconnectDelay(1)));
  expect(FakeEventSource.instances).toHaveLength(2);
  act(() => FakeEventSource.latest().open());
  expect(onResync).toHaveBeenCalledTimes(1);
  expect(result.current).toBe('open');
});

it('lets the browser retry transient drops without opening a second stream', () => {
  const { result } = renderHook(() => useCallEvents('c1', true, vi.fn(), vi.fn()));
  act(() => FakeEventSource.latest().open());
  act(() => FakeEventSource.latest().fail(false));
  expect(result.current).toBe('reconnecting');
  act(() => vi.advanceTimersByTime(30_000));
  expect(FakeEventSource.instances).toHaveLength(1);
});

it('closes the stream and cancels pending reconnects on unmount', () => {
  const { unmount } = renderHook(() => useCallEvents('c1', true, vi.fn(), vi.fn()));
  const source = FakeEventSource.latest();
  act(() => source.fail(true));
  unmount();
  act(() => vi.advanceTimersByTime(60_000));
  expect(FakeEventSource.instances).toHaveLength(1);
  expect(source.readyState).toBe(FakeEventSource.CLOSED);
});

it('stays idle without a live call', () => {
  const { result } = renderHook(() => useCallEvents('c1', false, vi.fn(), vi.fn()));
  expect(result.current).toBe('idle');
  expect(FakeEventSource.instances).toHaveLength(0);
});

it('keeps retrying at the capped delay after reporting the stream as lost', () => {
  const onResync = vi.fn();
  const { result } = renderHook(() => useCallEvents('c1', true, vi.fn(), onResync));
  for (let attempt = 1; attempt <= 9; attempt += 1) {
    act(() => FakeEventSource.latest().fail(true));
    act(() => vi.advanceTimersByTime(reconnectDelay(attempt)));
  }
  expect(result.current).toBe('closed');
  const opened = FakeEventSource.instances.length;
  act(() => FakeEventSource.latest().fail(true));
  act(() => vi.advanceTimersByTime(reconnectDelay(99)));
  expect(FakeEventSource.instances.length).toBe(opened + 1);
  expect(result.current).toBe('closed');
  act(() => FakeEventSource.latest().open());
  expect(result.current).toBe('open');
  expect(onResync).toHaveBeenCalled();
});
