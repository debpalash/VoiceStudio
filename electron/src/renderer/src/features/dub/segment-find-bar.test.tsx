import { fireEvent, render, screen } from '@testing-library/react';
import { expect, it, vi } from 'vitest';
import '@/i18n';
import { SegmentFindBar } from './segment-find-bar';

it('walks matches and replaces the current spoken line', () => {
  const onNext = vi.fn();
  const onReplace = vi.fn();
  const onReplaceAll = vi.fn();
  render(
    <SegmentFindBar
      query="acme"
      replacement="Apex"
      matchIndex={0}
      matchCount={3}
      canReplace
      canReplaceAll
      onQuery={vi.fn()}
      onReplacement={vi.fn()}
      onPrev={vi.fn()}
      onNext={onNext}
      onReplace={onReplace}
      onReplaceAll={onReplaceAll}
    />,
  );
  expect(screen.getByText('1 of 3')).toBeVisible();
  fireEvent.keyDown(screen.getByLabelText('Search text…'), { key: 'Enter' });
  expect(onNext).toHaveBeenCalledOnce();
  fireEvent.click(screen.getByRole('button', { name: 'Replace' }));
  fireEvent.click(screen.getByRole('button', { name: 'Replace all' }));
  expect(onReplace).toHaveBeenCalledOnce();
  expect(onReplaceAll).toHaveBeenCalledOnce();
});

it('disables replace when the current hit is not spoken text', () => {
  render(
    <SegmentFindBar
      query="acme"
      replacement="Apex"
      matchIndex={0}
      matchCount={1}
      canReplace={false}
      onQuery={vi.fn()}
      onReplacement={vi.fn()}
      onPrev={vi.fn()}
      onNext={vi.fn()}
      onReplace={vi.fn()}
      onReplaceAll={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Replace' })).toBeDisabled();
});

it('disables replace all when no spoken match remains', () => {
  render(
    <SegmentFindBar
      query="acme"
      replacement="Apex"
      matchIndex={0}
      matchCount={1}
      canReplace={false}
      canReplaceAll={false}
      onQuery={vi.fn()}
      onReplacement={vi.fn()}
      onPrev={vi.fn()}
      onNext={vi.fn()}
      onReplace={vi.fn()}
      onReplaceAll={vi.fn()}
    />,
  );
  expect(screen.getByRole('button', { name: 'Replace all' })).toBeDisabled();
});

it('keeps replace folded into one slim row until asked for', () => {
  const onReplacement = vi.fn();
  render(
    <SegmentFindBar
      query="acme"
      replacement=""
      matchIndex={0}
      matchCount={1}
      canReplace
      onQuery={vi.fn()}
      onReplacement={onReplacement}
      onPrev={vi.fn()}
      onNext={vi.fn()}
      onReplace={vi.fn()}
      onReplaceAll={vi.fn()}
    />,
  );
  expect(screen.queryByLabelText('Replace with…')).toBeNull();
  const toggle = screen.getByRole('button', { name: 'Find and replace' });
  expect(toggle).toHaveAttribute('aria-expanded', 'false');
  fireEvent.click(toggle);
  expect(screen.getByRole('button', { name: 'Replace all' })).toBeVisible();
  fireEvent.click(toggle);
  expect(onReplacement).toHaveBeenCalledWith('');
  expect(screen.queryByRole('button', { name: 'Replace all' })).toBeNull();
});
