import { render, screen } from '@testing-library/react';
import { expect, it } from 'vitest';
import { DisabledReason, firstReason } from './disabled-reason';

it('describes a disabled button with its reason and shows it on hover', () => {
  render(
    <DisabledReason reason="Enter some text first.">
      {(describedBy) => (
        <button type="button" disabled aria-describedby={describedBy}>
          Compare
        </button>
      )}
    </DisabledReason>,
  );
  const button = screen.getByRole('button', { name: 'Compare' });
  expect(button).toHaveAccessibleDescription('Enter some text first.');
  expect(button.parentElement).toHaveAttribute('title', 'Enter some text first.');
});

it('adds nothing around an available control', () => {
  const { container } = render(
    <DisabledReason reason={null}>
      {(describedBy) => (
        <button type="button" aria-describedby={describedBy}>
          Go
        </button>
      )}
    </DisabledReason>,
  );
  expect(container.innerHTML).toBe('<button type="button">Go</button>');
});

it('picks the first failing check', () => {
  expect(
    firstReason([
      [false, 'a'],
      [true, 'b'],
      [true, 'c'],
    ]),
  ).toBe('b');
  expect(firstReason([[false, 'a']])).toBeNull();
});
