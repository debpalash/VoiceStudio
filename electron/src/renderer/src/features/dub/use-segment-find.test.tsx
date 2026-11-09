import { act, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, expect, it, vi } from 'vitest';
import { replaceDubSegmentTexts } from './dub-session';
import { isFindBarTarget, useSegmentFind } from './use-segment-find';

vi.mock('./dub-session', () => ({ replaceDubSegmentTexts: vi.fn(() => 1) }));
vi.mock('sonner', () => ({ toast: { success: vi.fn() } }));

const segments = [
  { id: 'a', text: 'ACME one', text_original: 'ACME one' },
  { id: 'b', text: 'two', text_original: 'two' },
  { id: 'c', text: 'ACME three', text_original: 'ACME three' },
];

beforeEach(() => {
  vi.spyOn(window, 'requestAnimationFrame').mockImplementation((cb) => {
    cb(0);
    return 0;
  });
});
afterEach(() => vi.restoreAllMocks());

function setup() {
  const select = vi.fn();
  const scrollToRow = vi.fn();
  const hook = renderHook(() =>
    useSegmentFind({ segments, targetCode: 'es', select, scrollToRow }),
  );
  return { hook, select, scrollToRow };
}

it('lands the first Next on the first hit, then advances and scrolls to its row', () => {
  const { hook, select, scrollToRow } = setup();
  act(() => hook.result.current.bar.onQuery('acme'));
  // Unchanged originals are not searched twice.
  expect(hook.result.current.bar.matchCount).toBe(2);
  act(() => hook.result.current.bar.onNext());
  expect(hook.result.current.bar.matchIndex).toBe(0);
  expect(select).toHaveBeenLastCalledWith('a');
  expect(scrollToRow).toHaveBeenLastCalledWith(0);
  act(() => hook.result.current.bar.onNext());
  expect(select).toHaveBeenLastCalledWith('c');
  expect(scrollToRow).toHaveBeenLastCalledWith(2);
  expect(hook.result.current.holdsActive('c', 'text')).toBe(true);
  expect(hook.result.current.activeRange('c', 'text')).toEqual({ activeStart: 0, activeEnd: 4 });
});

it('commits replacements to the target language as one dub edit', () => {
  const { hook } = setup();
  act(() => {
    hook.result.current.bar.onQuery('ACME');
    hook.result.current.bar.onReplacement('Apex');
  });
  act(() => hook.result.current.bar.onReplaceAll());
  expect(replaceDubSegmentTexts).toHaveBeenCalledWith(
    [
      { id: 'a', text: 'Apex one' },
      { id: 'c', text: 'Apex three' },
    ],
    'es',
  );
});

it('recognises find-bar inputs so the transcript undo shortcut leaves them alone', () => {
  const bar = document.createElement('div');
  bar.dataset.slot = 'dub-find-bar';
  const input = document.createElement('input');
  bar.append(input);
  expect(isFindBarTarget(input)).toBe(true);
  expect(isFindBarTarget(document.createElement('textarea'))).toBe(false);
  expect(isFindBarTarget(null)).toBe(false);
});
