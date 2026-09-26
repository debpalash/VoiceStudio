import React from 'react';
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { fireEvent, render, screen } from '@testing-library/react';

import TranscriptionPicker from '../components/TranscriptionPicker';
import { TRANSCRIPTIONS_KEY, TRANSCRIPTION_EVENT } from '../utils/transcriptionsStore';

function seed(entries) {
  localStorage.setItem(TRANSCRIPTIONS_KEY, JSON.stringify(entries));
}

describe('TranscriptionPicker', () => {
  beforeEach(() => {
    localStorage.clear();
    window.HTMLElement.prototype.scrollIntoView = vi.fn();
  });

  it('renders the empty state when there are no transcriptions', () => {
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={vi.fn()} />);
    expect(screen.getByText(/No transcriptions yet/i)).toBeInTheDocument();
  });

  it('lists entries and hides empty-text rows', () => {
    seed([
      {
        id: 1,
        text: 'Hello world',
        language: 'en',
        duration_s: 3,
        timestamp: new Date().toISOString(),
      },
      { id: 2, text: '   ', language: 'en', timestamp: new Date().toISOString() }, // hidden
    ]);
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={vi.fn()} />);
    expect(screen.getByText('Hello world')).toBeInTheDocument();
    expect(
      screen.getAllByRole('button').filter((b) => b.className.includes('txn-picker__row')),
    ).toHaveLength(1);
  });

  it('clicking a row calls onPick with the original entry, then onClose', () => {
    const entry = { id: 7, text: 'Pick me', timestamp: new Date().toISOString() };
    seed([entry]);
    const onPick = vi.fn();
    const onClose = vi.fn();
    render(<TranscriptionPicker open onClose={onClose} onPick={onPick} />);
    fireEvent.click(screen.getByText('Pick me'));
    expect(onPick).toHaveBeenCalledWith(expect.objectContaining({ id: 7, text: 'Pick me' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('rows are real buttons (keyboard-activatable)', () => {
    seed([{ id: 1, text: 'Keyboard ready', timestamp: new Date().toISOString() }]);
    const onPick = vi.fn();
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={onPick} />);
    const row = screen.getByText('Keyboard ready').closest('button');
    expect(row).toBeInTheDocument();
    fireEvent.click(row); // a <button> activates on Enter/Space natively → click in jsdom
    expect(onPick).toHaveBeenCalled();
  });

  it('search filters by text + shows the search-specific empty state', () => {
    seed([
      { id: 1, text: 'alpha cat', timestamp: new Date().toISOString() },
      { id: 2, text: 'beta dog', timestamp: new Date().toISOString() },
    ]);
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={vi.fn()} />);
    const box = screen.getByPlaceholderText(/Search transcriptions/i);
    fireEvent.change(box, { target: { value: 'cat' } });
    expect(screen.getByText('alpha cat')).toBeInTheDocument();
    expect(screen.queryByText('beta dog')).not.toBeInTheDocument();
    fireEvent.change(box, { target: { value: 'zzz' } });
    expect(screen.getByText(/No transcriptions match/i)).toBeInTheDocument();
  });

  it('omits the time chip for an unparseable timestamp (no "Invalid Date")', () => {
    seed([{ id: 1, text: 'no time', timestamp: 'not-a-date' }]);
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={vi.fn()} />);
    expect(screen.getByText('no time')).toBeInTheDocument();
    expect(screen.queryByText(/Invalid Date/)).not.toBeInTheDocument();
  });

  it('refreshes on a transcription-added event while open', () => {
    render(<TranscriptionPicker open onClose={vi.fn()} onPick={vi.fn()} />);
    expect(screen.getByText(/No transcriptions yet/i)).toBeInTheDocument();
    seed([{ id: 9, text: 'live add', timestamp: new Date().toISOString() }]);
    fireEvent(window, new CustomEvent(TRANSCRIPTION_EVENT));
    expect(screen.getByText('live add')).toBeInTheDocument();
  });
});
