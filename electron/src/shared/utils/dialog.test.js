import { afterEach, describe, expect, it, vi } from 'vitest';

import { askConfirm } from './dialog';

describe('askConfirm', () => {
  afterEach(() => vi.restoreAllMocks());

  it.each([true, false])('resolves to the browser confirmation result %s', async (answer) => {
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(answer);
    await expect(
      askConfirm('Proceed?', 'Ignored title', {
        okLabel: 'Continue',
        cancelLabel: 'Stop',
      }),
    ).resolves.toBe(answer);
    expect(confirm).toHaveBeenCalledWith('Proceed?');
  });
});
