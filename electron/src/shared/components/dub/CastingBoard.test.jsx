import { useState } from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent, within } from '@testing-library/react';
import i18n from '../../i18n';
import CastingBoard from './CastingBoard';

vi.mock('react-hot-toast', () => ({
  default: { success: vi.fn(), error: vi.fn() },
}));

const t = i18n.t.bind(i18n);

const profiles = [
  { id: 'voice-a', name: 'Anna' },
  { id: 'voice-b', name: 'Ben' },
];

// SPEAKER_2 speaks segment 2 directly AND the second half of a merged row —
// the same shape the CAST <select> tests pin down: a drop must set the direct
// segment's profile_id and the merged row's part attribution (both mirrors)
// while leaving the merged row's own top-level voice alone.
const makeSegments = () => [
  { id: '1', speaker_id: 'SPEAKER_1', profile_id: '', text: 'one' },
  { id: '2', speaker_id: 'SPEAKER_2', profile_id: '', text: 'two' },
  {
    id: 'merged',
    speaker_id: 'SPEAKER_1',
    profile_id: 'voice-a',
    text: 'three four',
    merge_parts: [
      { textStart: 0, textEnd: 5, speaker_id: 'SPEAKER_1', profile_id: 'voice-a' },
      { textStart: 6, textEnd: 10, speaker_id: 'SPEAKER_2', profile_id: '' },
    ],
    merge_parts_original: [
      { textStart: 0, textEnd: 5, speaker_id: 'SPEAKER_1', profile_id: 'voice-a' },
      { textStart: 6, textEnd: 10, speaker_id: 'SPEAKER_2', profile_id: '' },
    ],
  },
];

function renderBoard(over = {}) {
  const props = {
    t,
    dubSegments: makeSegments(),
    setDubSegments: vi.fn(),
    speakerClones: {},
    profiles,
    ...over,
  };
  const utils = render(<CastingBoard {...props} />);
  return { ...utils, props };
}

const openBoard = () => fireEvent.click(screen.getByRole('button', { name: /casting board/i }));

describe('CastingBoard drag & drop', () => {
  it('uses SVG icons and clean labels for preset cards and searchable choices', () => {
    renderBoard();
    openBoard();
    const card = screen
      .getByTestId('casting-board')
      .querySelector('[data-profile="preset:narrator"]');
    expect(card).toHaveTextContent('Authoritative');
    expect(card.querySelector('svg')).toBeInTheDocument();
    expect(card.textContent).not.toMatch(/\p{Extended_Pictographic}/u);
    fireEvent.click(screen.getByRole('button', { name: /assign a voice to SPEAKER_1/i }));
    fireEvent.change(within(screen.getByRole('listbox')).getByRole('textbox'), {
      target: { value: 'Authoritative' },
    });
    const option = screen.getByRole('option', { name: 'Authoritative' });
    expect(option.querySelector('svg')).toBeInTheDocument();
  });
  it('labels restored automatic voices without requiring clone metadata', () => {
    renderBoard({
      dubSegments: [{ id: '1', speaker_id: 'Speaker 1', profile_id: 'auto:speaker_1' }],
    });
    expect(screen.getByRole('button', { name: 'Speaker 1' })).toHaveTextContent('Auto · Speaker 1');
  });
  it('dropping a voice chip on a speaker writes the same fields as the CAST select', () => {
    const { props } = renderBoard();
    openBoard();

    const row = screen.getByTestId('casting-board').querySelector('[data-speaker="SPEAKER_2"]');
    fireEvent.drop(row, { dataTransfer: { getData: () => 'voice-b' } });

    const updated = props.setDubSegments.mock.calls[0][0];
    expect(updated[0].profile_id).toBe(''); // other speaker untouched
    expect(updated[1].profile_id).toBe('voice-b'); // direct match
    expect(updated[2].profile_id).toBe('voice-a'); // merged row keeps its top voice
    expect(updated[2].merge_parts[1].profile_id).toBe('voice-b');
    expect(updated[2].merge_parts_original[1].profile_id).toBe('voice-b');
  });

  it('a dragged chip carries its profile id, and the Default chip a sentinel that maps back to ""', () => {
    const { props } = renderBoard();
    openBoard();
    const board = screen.getByTestId('casting-board');

    const carried = {};
    fireEvent.dragStart(board.querySelector('[data-profile="voice-b"]'), {
      dataTransfer: { setData: (k, v) => (carried[k] = v), effectAllowed: '' },
    });
    expect(carried['text/plain']).toBe('voice-b');

    const defaults = {};
    fireEvent.dragStart(board.querySelector('[data-profile=""]'), {
      dataTransfer: { setData: (k, v) => (defaults[k] = v), effectAllowed: '' },
    });
    expect(defaults['text/plain']).toBe('__default__');

    const row = board.querySelector('[data-speaker="SPEAKER_2"]');
    fireEvent.drop(row, { dataTransfer: { getData: () => defaults['text/plain'] } });
    expect(props.setDubSegments.mock.calls[0][0][1].profile_id).toBe('');
  });

  it('a drop with no payload is a no-op', () => {
    const { props } = renderBoard();
    openBoard();
    const row = screen.getByTestId('casting-board').querySelector('[data-speaker="SPEAKER_1"]');
    fireEvent.drop(row, { dataTransfer: { getData: () => '' } });
    expect(props.setDubSegments).not.toHaveBeenCalled();
  });

  it('ignores text/plain drops that are not one of the available voices', () => {
    const { props } = renderBoard();
    openBoard();
    const row = screen.getByTestId('casting-board').querySelector('[data-speaker="SPEAKER_1"]');
    fireEvent.drop(row, { dataTransfer: { getData: () => 'selected text from another app' } });
    expect(props.setDubSegments).not.toHaveBeenCalled();
  });
});

describe('CastingBoard keyboard path', () => {
  it('Enter on a speaker opens a listbox; arrows + Enter assign the picked voice', () => {
    const { props } = renderBoard();
    openBoard();

    // The row button is the keyboard entry point (native Enter/Space → click).
    fireEvent.click(screen.getByRole('button', { name: /assign a voice to SPEAKER_2/i }));
    const listbox = screen.getByRole('listbox');
    const options = within(listbox).getAllByRole('option');
    expect(options[0]).toHaveTextContent('Default');
    expect(options[1]).toHaveTextContent('Anna');
    expect(options[2]).toHaveTextContent('Ben');

    fireEvent.keyDown(within(listbox).getByRole('textbox'), { key: 'ArrowDown' });
    fireEvent.keyDown(within(listbox).getByRole('textbox'), { key: 'ArrowDown' });
    fireEvent.keyDown(within(listbox).getByRole('textbox'), { key: 'Enter' });

    expect(screen.queryByRole('listbox')).toBeNull();
    const updated = props.setDubSegments.mock.calls[0][0];
    expect(updated[1].profile_id).toBe('voice-b');
    expect(updated[2].merge_parts[1].profile_id).toBe('voice-b');
  });

  it('Escape closes the listbox without assigning', () => {
    const { props } = renderBoard();
    openBoard();
    fireEvent.click(screen.getByRole('button', { name: /assign a voice to SPEAKER_1/i }));
    fireEvent.keyDown(within(screen.getByRole('listbox')).getByRole('textbox'), { key: 'Escape' });
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(props.setDubSegments).not.toHaveBeenCalled();
  });

  it.each([
    ['Tab', false],
    ['Shift+Tab', true],
  ])('%s closes the listbox without trapping focus', (_label, shiftKey) => {
    renderBoard();
    openBoard();
    const trigger = screen.getByRole('button', { name: /assign a voice to SPEAKER_1/i });
    fireEvent.click(trigger);
    const listbox = screen.getByRole('listbox');

    expect(fireEvent.keyDown(within(listbox).getByRole('textbox'), { key: 'Tab', shiftKey })).toBe(
      true,
    );
    expect(screen.queryByRole('listbox')).toBeNull();
    expect(trigger).not.toHaveFocus();
  });
});

describe('CastingBoard ↔ dropdown sync', () => {
  function Harness() {
    const [segments, setSegments] = useState(makeSegments());
    return (
      <CastingBoard
        t={t}
        dubSegments={segments}
        setDubSegments={setSegments}
        speakerClones={{}}
        profiles={profiles}
      />
    );
  }

  it('a drop updates the pre-existing CAST dropdown for that speaker', () => {
    render(<Harness />);
    openBoard();

    const selects = document.querySelectorAll('.dub-cast__select');
    expect(selects[1]).toHaveTextContent(t('dub.default'));

    const row = screen.getByTestId('casting-board').querySelector('[data-speaker="SPEAKER_2"]');
    fireEvent.drop(row, { dataTransfer: { getData: () => 'voice-b' } });

    expect(document.querySelectorAll('.dub-cast__select')[1]).toHaveTextContent('Ben');
    expect(within(row).getByRole('button')).toHaveTextContent('Ben');
  });
});

describe('CastingBoard auto-clone chip', () => {
  it('shows the from-video chip and lists the auto clone first for cloned speakers', () => {
    renderBoard({ speakerClones: { SPEAKER_1: { duration: 6.24 } } });
    openBoard();

    const row = screen.getByTestId('casting-board').querySelector('[data-speaker="SPEAKER_1"]');
    expect(within(row).getByTitle(/from video · 6\.2s/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /assign a voice to SPEAKER_1/i }));
    const options = within(screen.getByRole('listbox')).getAllByRole('option');
    expect(options[0]).toHaveTextContent(/from video · 6\.2s/i);
    expect(options[1]).toHaveTextContent('Default');
  });

  it('keeps a speaker addressable when only merge_parts_original remains', () => {
    const segments = [
      {
        id: 'merged',
        speaker_id: 'SPEAKER_1',
        profile_id: 'voice-a',
        text: 'one two',
        merge_parts: [],
        merge_parts_original: [
          { speaker_id: 'SPEAKER_1', profile_id: 'voice-a' },
          { speaker_id: 'SPEAKER_2', profile_id: '' },
        ],
      },
    ];
    const { props } = renderBoard({ dubSegments: segments });
    openBoard();
    expect(
      screen.getByRole('button', { name: /assign a voice to SPEAKER_2/i }),
    ).toBeInTheDocument();

    const selects = document.querySelectorAll('.dub-cast__select');
    fireEvent.click(selects[1]);
    fireEvent.mouseDown(screen.getByRole('option', { name: 'Ben', exact: true }));

    const updated = props.setDubSegments.mock.calls[0][0];
    expect(updated[0].profile_id).toBe('voice-a');
    expect(updated[0].merge_parts).toEqual([]);
    expect(updated[0].merge_parts_original[1].profile_id).toBe('voice-b');
  });

  it('renders nothing when no segment carries a speaker', () => {
    const { container } = renderBoard({
      dubSegments: [{ id: '1', text: 'no speakers here' }],
    });
    expect(container).toBeEmptyDOMElement();
  });
});
