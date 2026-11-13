import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { useRef } from 'react';
import { afterEach, expect, it, vi } from 'vitest';
import { VirtualGrid, VirtualList, gridColumns } from './virtual-list';

afterEach(() => vi.restoreAllMocks());

function mockSizes() {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(300);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(300);
}

function Harness({
  count,
  onPick,
  activeKey,
}: {
  count: number;
  onPick: (id: number) => void;
  activeKey?: number;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const items = Array.from({ length: count }, (_, id) => id);
  return (
    <div ref={ref} style={{ height: 300, overflow: 'auto' }}>
      <VirtualList
        aria-label="rows"
        items={items}
        scrollRef={ref}
        getKey={(id) => id}
        activeKey={activeKey}
        estimateSize={40}
        renderRow={(id) => (
          <button type="button" onClick={() => onPick(id)}>
            Row {id}
          </button>
        )}
      />
    </div>
  );
}

it('mounts only a window of rows for a 1000-item list and keeps rows clickable', async () => {
  mockSizes();
  const onPick = vi.fn();
  render(<Harness count={1000} onPick={onPick} />);
  const rows = await screen.findAllByRole('listitem');
  expect(rows.length).toBeGreaterThan(0);
  expect(rows.length).toBeLessThan(40);
  expect(screen.queryByRole('button', { name: 'Row 999' })).toBeNull();
  // Falsy items (0) are rows too.
  expect(screen.getByRole('button', { name: 'Row 0' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Row 3' }));
  expect(onPick).toHaveBeenCalledWith(3);
});

it('renders every row of a short list', () => {
  render(<Harness count={20} onPick={vi.fn()} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(20);
});

it('scrolls an unmounted active row into view', async () => {
  mockSizes();
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(40000);
  const scrollTo = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: scrollTo });
  const { rerender } = render(<Harness count={1000} onPick={vi.fn()} />);
  await screen.findAllByRole('listitem');
  scrollTo.mockClear();
  rerender(<Harness count={1000} onPick={vi.fn()} activeKey={500} />);
  await waitFor(() => expect(scrollTo).toHaveBeenCalled());
  expect(scrollTo.mock.calls[0][0].top).toBeGreaterThan(1000);
});

it('scrolls to an active row that is already set when the list mounts', async () => {
  mockSizes();
  vi.spyOn(HTMLElement.prototype, 'scrollHeight', 'get').mockReturnValue(40000);
  const scrollTo = vi.fn();
  Object.defineProperty(HTMLElement.prototype, 'scrollTo', { configurable: true, value: scrollTo });
  render(<Harness count={1000} onPick={vi.fn()} activeKey={500} />);
  await waitFor(() => expect(scrollTo).toHaveBeenCalled());
  expect(scrollTo.mock.calls.at(-1)![0].top).toBeGreaterThan(1000);
});

function GridHarness({ count, onPick }: { count: number; onPick: (id: number) => void }) {
  const ref = useRef<HTMLDivElement>(null);
  const items = Array.from({ length: count }, (_, id) => id);
  return (
    <div ref={ref} style={{ height: 300, overflow: 'auto' }}>
      <VirtualGrid
        aria-label="cards"
        items={items}
        scrollRef={ref}
        getKey={(id) => id}
        estimateRowSize={100}
        renderCard={(id) => (
          <button type="button" onClick={() => onPick(id)}>
            Card {id}
          </button>
        )}
      />
    </div>
  );
}

it('grid: mounts a window of cards, follows width, and keeps actions working', async () => {
  vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockReturnValue(300);
  vi.spyOn(HTMLElement.prototype, 'offsetWidth', 'get').mockReturnValue(900);
  const onPick = vi.fn();
  render(<GridHarness count={1000} onPick={onPick} />);
  const cards = await screen.findAllByRole('listitem');
  expect(cards.length).toBeLessThan(60);
  expect(screen.queryByRole('button', { name: 'Card 999' })).toBeNull();
  // 900px at 280px min + 12px gap = 3 columns, so the first row holds cards 0-2.
  expect(screen.getByRole('button', { name: 'Card 0' })).toBeTruthy();
  expect(screen.getByRole('button', { name: 'Card 2' })).toBeTruthy();
  fireEvent.click(screen.getByRole('button', { name: 'Card 2' }));
  expect(onPick).toHaveBeenCalledWith(2);
});

it('grid: column count follows the width rule', () => {
  expect(gridColumns(900, 280, 12)).toBe(3);
  expect(gridColumns(600, 280, 12)).toBe(2);
  expect(gridColumns(200, 280, 12)).toBe(1);
  expect(gridColumns(0, 280, 12)).toBe(1);
});

it('grid: renders every card of a short list', () => {
  render(<GridHarness count={20} onPick={vi.fn()} />);
  expect(screen.getAllByRole('listitem')).toHaveLength(20);
});
