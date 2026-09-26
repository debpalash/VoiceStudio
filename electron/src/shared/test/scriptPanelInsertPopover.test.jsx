// Regression guard (owner-reported): the ⊕ Insert popover used to open
// UPWARD (`bottom-[60px]`) from the script textarea — but ScriptPanel's only
// mount (CloneDesignTab) puts that input at the very top of the clone modal,
// so the tag list (max-h 280px, incl. the CMU phoneme chips) climbed straight
// out of the viewport with no way to see or scroll it. It must open BELOW
// the input (`top-[calc(100%+…)]`), where the panel's topmost placement
// guarantees room.
import { describe, it, expect } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import React, { useState } from 'react';
import ScriptPanel from '../components/clone/ScriptPanel';

function Host() {
  const [text, setText] = useState('');
  const [insertOpen, setInsertOpen] = useState(false);
  return (
    <ScriptPanel
      t={(k, opts) => opts?.defaultValue || k}
      defineMethod="audio"
      text={text}
      setText={setText}
      demoPresets={[]}
      insertOpen={insertOpen}
      setInsertOpen={setInsertOpen}
      insertTag={() => {}}
    />
  );
}

describe('ScriptPanel insert popover placement', () => {
  it('opens BELOW the input (top-anchored), never upward', () => {
    render(<Host />);
    fireEvent.click(screen.getByRole('button', { name: /insert/i }));
    const popover = document.querySelector('[class*="top-[calc(100%"]');
    expect(popover, 'popover must anchor below the panel via top-[calc(100%+…)]').toBeTruthy();
    expect(popover.className).not.toMatch(/bottom-\[60px\]/);
  });
});
