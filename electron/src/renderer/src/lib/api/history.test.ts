import { beforeEach, expect, it, vi } from 'vitest';

const apiJson = vi.fn();
vi.mock('./client', () => ({ apiJson: (...a: unknown[]) => apiJson(...a), apiFetch: vi.fn() }));

import { fullTake, HISTORY_LIST_TEXT_LIMIT, listHistory } from './history';
import type { HistoryItem } from './types';

const take = (extra: Partial<HistoryItem> = {}) =>
  ({ id: 't1', text: 'hi', ...extra }) as HistoryItem;

beforeEach(() => apiJson.mockReset());

it('asks the list for clipped scripts so navigation never parses a whole book', async () => {
  apiJson.mockResolvedValue([]);
  await listHistory();
  expect(apiJson).toHaveBeenCalledWith(`/history?text_limit=${HISTORY_LIST_TEXT_LIMIT}`);
});

it('fetches the whole take only when the list clipped it', async () => {
  const whole = take({ text: 'whole script' });
  apiJson.mockResolvedValue(whole);
  const short = take();
  expect(await fullTake(short)).toBe(short);
  expect(apiJson).not.toHaveBeenCalled();
  expect(await fullTake(take({ text_truncated: true }))).toBe(whole);
  expect(apiJson).toHaveBeenCalledWith('/history/t1');
});
