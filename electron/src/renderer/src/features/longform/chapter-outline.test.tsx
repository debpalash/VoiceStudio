import { fireEvent, render, screen } from '@testing-library/react';
import { useRef, useState } from 'react';
import { expect, it, vi } from 'vitest';
import '@/i18n';
import { ChapterOutline, scrollTextareaToOffset } from './chapter-outline';

function OutlineHost({ script, onJump }: { script: string; onJump?: (start: number) => void }) {
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const [caret, setCaret] = useState(0);
  return (
    <>
      <ChapterOutline
        script={script}
        caret={caret}
        textareaRef={textareaRef}
        onJump={(start) => {
          setCaret(start);
          onJump?.(start);
        }}
      />
      <textarea ref={textareaRef} aria-label="Script" value={script} onChange={() => undefined} />
    </>
  );
}

const script = '# One\nHello world here.\n# Two\nFour more spoken words.';

it('lists chapters and jumps the script caret to the heading', () => {
  const onJump = vi.fn();
  render(<OutlineHost script={script} onJump={onJump} />);
  expect(screen.getByRole('navigation', { name: 'Chapters' })).toBeVisible();
  fireEvent.click(screen.getByRole('button', { name: 'Jump to Two' }));
  expect(onJump).toHaveBeenCalledWith(script.indexOf('# Two'));
  expect(screen.getByRole('button', { name: 'Jump to Two' })).toHaveAttribute(
    'aria-current',
    'true',
  );
  expect(screen.getByLabelText('Script')).toHaveProperty('selectionStart', script.indexOf('# Two'));
});

it('explains how to add chapters when the manuscript has no headings', () => {
  render(<OutlineHost script="just some words" />);
  expect(screen.getByText('Add a # heading to jump between chapters.')).toBeVisible();
  expect(screen.queryByRole('button', { name: /Jump to/ })).toBeNull();
});

it('scrolls by the wrapped layout position, not the newline count', () => {
  // One long paragraph wraps onto many displayed lines but has no newline, so
  // a line count would leave the heading after it offscreen.
  const value = `${'word '.repeat(400)}\n# Two\nBody`;
  const textarea = document.createElement('textarea');
  textarea.value = value;
  document.body.appendChild(textarea);
  Object.defineProperty(textarea, 'clientHeight', { configurable: true, value: 300 });
  Object.defineProperty(textarea, 'clientWidth', { configurable: true, value: 200 });
  let scrolled = -1;
  Object.defineProperty(textarea, 'scrollTop', {
    configurable: true,
    get: () => scrolled,
    set: (v: number) => {
      scrolled = v;
    },
  });
  const measured: string[] = [];
  const offsetTop = vi
    .spyOn(HTMLElement.prototype, 'offsetTop', 'get')
    .mockImplementation(function (this: HTMLElement) {
      if (this.dataset.caretMarker === undefined) return 0;
      const mirror = this.parentElement as HTMLElement;
      measured.push(mirror.style.width, mirror.style.whiteSpace);
      return 1400;
    });
  try {
    scrollTextareaToOffset(textarea, value.indexOf('# Two'));
    expect(measured).toEqual(['200px', 'pre-wrap']);
    expect(scrolled).toBe(1300);
    expect(textarea.selectionStart).toBe(value.indexOf('# Two'));
    expect(document.querySelector('[data-caret-marker]')).toBeNull();
  } finally {
    offsetTop.mockRestore();
    textarea.remove();
  }
});
