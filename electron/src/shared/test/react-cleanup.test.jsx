import React from 'react';
import { render } from '@testing-library/react/pure';
import { expect, it, vi } from 'vitest';
import { Dialog, DialogContent, DialogTitle } from '../components/ui/dialog';
import { cleanupReact } from './react-cleanup';

it.each([false, true])('finishes deferred focus-scope effects before returning (fake timers: %s)', async (fake) => {
  if (fake) vi.useFakeTimers();
  const onCloseAutoFocus = vi.fn((event) => event.preventDefault());
  const future = vi.fn();
  if (fake) setTimeout(future, 1000);
  try {
    render(
      <Dialog open>
        <DialogContent onCloseAutoFocus={onCloseAutoFocus} aria-describedby={undefined}>
          <DialogTitle>Cleanup fixture</DialogTitle>
        </DialogContent>
      </Dialog>,
    );
    await cleanupReact();
    expect(onCloseAutoFocus).toHaveBeenCalledTimes(1);
    expect(future).not.toHaveBeenCalled();
  } finally {
    if (fake) vi.clearAllTimers();
    vi.useRealTimers();
  }
});
