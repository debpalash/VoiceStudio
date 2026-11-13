import { expect, it } from 'vitest';
import { queryClient } from './query';

it('pauses interval polling while the window is hidden', () => {
  expect(queryClient.getDefaultOptions().queries?.refetchIntervalInBackground).toBe(false);
});
