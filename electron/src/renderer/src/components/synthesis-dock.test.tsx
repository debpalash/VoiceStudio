import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { afterEach, expect, it } from 'vitest';
import { SynthesisDock } from './synthesis-dock';

afterEach(cleanup);

function DockHarness() {
  const [showOutput, setShowOutput] = useState(false);
  return (
    <SynthesisDock
      data-testid="dock"
      output={showOutput ? <section aria-label="Latest take">Latest take</section> : null}
    >
      <div data-testid="controls">
        <button type="button" onClick={() => setShowOutput(true)}>
          Synthesize Audio
        </button>
      </div>
    </SynthesisDock>
  );
}

it('keeps synthesis controls as the bottom dock when the latest take appears', () => {
  render(<DockHarness />);
  const dock = screen.getByTestId('dock');
  const controls = screen.getByTestId('controls');

  expect(dock).toHaveClass('mt-auto', 'shrink-0');
  expect(dock.lastElementChild).toBe(controls);

  fireEvent.click(screen.getByRole('button', { name: 'Synthesize Audio' }));

  expect(screen.getByRole('region', { name: 'Latest take' }).nextElementSibling).toBe(controls);
  expect(dock.lastElementChild).toBe(controls);
});
