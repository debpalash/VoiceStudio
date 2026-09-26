import React, { useRef, useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

// A trivial t() — components take t as a prop, so no i18n provider is needed.
const t = (k, o) =>
  o ? Object.entries(o).reduce((s, [kk, v]) => s.replace(`{{${kk}}}`, v), k) : k;

// Mock the shared VoiceSelector to a plain <select> so we can drive onChange.
vi.mock('../components/VoiceSelector', () => ({
  default: ({ value, onChange }) => (
    <select data-testid="voice-selector" value={value} onChange={(e) => onChange(e.target.value)}>
      <option value="">default</option>
      <option value="pid-1">Voice 1</option>
    </select>
  ),
}));

import CastPanel from '../components/audiobook/CastPanel';
import MarkupToolbar from '../components/audiobook/MarkupToolbar';
import StatsBar from '../components/audiobook/StatsBar';
import ValidationWarnings from '../components/audiobook/ValidationWarnings';

describe('CastPanel', () => {
  it('lists the script’s distinct voices and maps one', () => {
    const setVoiceCast = vi.fn();
    render(
      <CastPanel
        t={t}
        castNames={['Mara', 'Cole']}
        voiceCast={{}}
        setVoiceCast={setVoiceCast}
        profiles={[]}
      />,
    );
    expect(screen.getByText('Mara')).toBeInTheDocument();
    expect(screen.getByText('Cole')).toBeInTheDocument();

    fireEvent.change(screen.getAllByTestId('voice-selector')[0], {
      target: { value: 'pid-1' },
    });
    expect(setVoiceCast).toHaveBeenCalledWith('Mara', 'pid-1');
  });

  it('shows an empty hint when the script has no voice tags', () => {
    render(<CastPanel t={t} castNames={[]} voiceCast={{}} setVoiceCast={vi.fn()} profiles={[]} />);
    expect(screen.getByText('audiobook.cast_empty')).toBeInTheDocument();
  });
});

function ToolbarHarness({ initial = 'hello world' }) {
  const [text, setText] = useState(initial);
  const ref = useRef(null);
  return (
    <div>
      <MarkupToolbar t={t} textareaRef={ref} text={text} setText={setText} />
      <textarea
        ref={ref}
        aria-label="script"
        value={text}
        onChange={(e) => setText(e.target.value)}
      />
    </div>
  );
}

describe('MarkupToolbar', () => {
  it('inserts a token at the cursor', () => {
    render(<ToolbarHarness initial="hello world" />);
    const ta = screen.getByLabelText('script');
    ta.focus();
    ta.setSelectionRange(5, 5); // caret right after "hello"
    fireEvent.click(screen.getByRole('button', { name: 'audiobook.insert_pause' }));
    expect(ta.value).toBe('hello[pause 500ms] world');
  });

  it('wraps the current selection with a paired tag', () => {
    render(<ToolbarHarness initial="hello world" />);
    const ta = screen.getByLabelText('script');
    ta.focus();
    ta.setSelectionRange(0, 11); // select the whole thing
    fireEvent.click(screen.getByRole('button', { name: 'audiobook.insert_slow' }));
    expect(ta.value).toBe('[slow]hello world[/slow]');
  });

  it('inserts a [voice:NAME] placeholder', () => {
    render(<ToolbarHarness initial="" />);
    const ta = screen.getByLabelText('script');
    ta.focus();
    ta.setSelectionRange(0, 0);
    fireEvent.click(screen.getByRole('button', { name: 'audiobook.insert_voice' }));
    expect(ta.value).toBe('[voice:NAME]');
  });

  it('exposes an accessible dismissal control for the reactions menu', () => {
    render(<ToolbarHarness />);
    fireEvent.click(screen.getByRole('button', { name: 'audiobook.insert_reactions' }));
    const close = screen.getByRole('button', { name: 'common.close' });
    fireEvent.click(close);
    expect(screen.queryByRole('menu')).toBeNull();
  });
});

describe('StatsBar', () => {
  it('computes chapters/words and passes them to t()', () => {
    const spy = vi.fn(() => 'STATS');
    render(<StatsBar t={spy} text={'# One\nHello world here now.'} />);
    expect(spy).toHaveBeenCalledWith('audiobook.stats', {
      chapters: 1,
      words: '4',
      runtime: expect.any(String),
    });
  });
  it('renders nothing for an empty script', () => {
    const { container } = render(<StatsBar t={t} text={'   '} />);
    expect(container).toBeEmptyDOMElement();
  });
});

describe('ValidationWarnings', () => {
  it('renders warnings and dismisses', () => {
    const onDismiss = vi.fn();
    render(
      <ValidationWarnings
        t={t}
        warnings={[{ type: 'unknown_voice', name: 'Mara' }]}
        onDismiss={onDismiss}
      />,
    );
    expect(screen.getByText(/warn_unknown_voice/)).toBeInTheDocument();
    fireEvent.click(screen.getByLabelText('audiobook.dismiss'));
    expect(onDismiss).toHaveBeenCalled();
  });
  it('renders nothing when there are no warnings', () => {
    const { container } = render(<ValidationWarnings t={t} warnings={[]} onDismiss={vi.fn()} />);
    expect(container).toBeEmptyDOMElement();
  });
});
