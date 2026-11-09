import { cleanup } from '@testing-library/react/pure';
import { setTimeout as nextTurn } from 'node:timers/promises';
import { vi } from 'vitest';

export async function cleanupReact() {
  cleanup();
  // Radix restores focus in a zero-delay unmount callback. Let it finish while
  // this JSDOM realm still owns both the container and the Event constructor.
  // Only advance the current fake-clock tick; unrelated future timers remain
  // subject to their tests' own cleanup instead of being blindly flushed.
  if (vi.isFakeTimers()) await vi.advanceTimersByTimeAsync(0);
  await nextTurn(0);
}
