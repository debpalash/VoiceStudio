import React, { useRef } from 'react';
import { useAppStore } from '../../store';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import ScriptPanel from './ScriptPanel';

const originalClipboard = Object.getOwnPropertyDescriptor(navigator, 'clipboard');
afterEach(() => {
  if (originalClipboard) Object.defineProperty(navigator, 'clipboard', originalClipboard);
  else delete navigator.clipboard;
});
function Harness() {
  const text = useAppStore((state) => state.text);
  const setText = useAppStore((state) => state.setText);
  const textAreaRef = useRef(null);
  return (
    <ScriptPanel
      t={(key) => key}
      defineMethod="audio"
      text={text}
      setText={setText}
      textAreaRef={textAreaRef}
      demoPresets={[]}
      setShowDemoCoachmark={() => {}}
    />
  );
}
describe('ScriptPanel paste', () => {
  it('pastes at the selection without replacing the rest of the text', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText: vi.fn().mockResolvedValue('friend') },
    });
    useAppStore.getState().setText('Hello world');
    render(<Harness />);
    expect(screen.getByText('clone.text_label')).toBeInTheDocument();
    const field = screen.getByRole('textbox');
    field.setSelectionRange(6, 11);
    fireEvent.click(screen.getByRole('button', { name: 'clone.paste' }));
    await waitFor(() => expect(field).toHaveValue('Hello friend'));
  });
  it('uses the current selection when clipboard access resolves', async () => {
    let resolveClipboard;
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: {
        readText: () =>
          new Promise((resolve) => {
            resolveClipboard = resolve;
          }),
      },
    });
    useAppStore.getState().setText('Hello world');
    render(<Harness />);
    const field = screen.getByRole('textbox');
    field.setSelectionRange(6, 11);
    fireEvent.click(screen.getByRole('button', { name: 'clone.paste' }));
    fireEvent.change(field, { target: { value: 'New greeting' } });
    field.setSelectionRange(0, 3);
    resolveClipboard('Fresh');
    await waitFor(() => expect(field).toHaveValue('Fresh greeting'));
  });
  it('preserves text and offers keyboard paste when clipboard access fails', async () => {
    Object.defineProperty(navigator, 'clipboard', {
      configurable: true,
      value: { readText: vi.fn().mockRejectedValue(new Error('denied')) },
    });
    useAppStore.getState().setText('Hello world');
    render(<Harness />);
    fireEvent.click(screen.getByRole('button', { name: 'clone.paste' }));
    expect(await screen.findByRole('alert')).toHaveTextContent('clone.paste_failed');
    expect(screen.getByRole('textbox')).toHaveValue('Hello world');
  });
});
