import React from 'react';
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import '../i18n';

// #1612 — "adjust the seconds of a single phrase's duration".
//
// The row showed `start` as an editable field and `end` as static grey text,
// so the table looked like it could move a line but never resize one; the only
// duration control was a drag handle on the waveform timeline. The end is now
// a peer of the start, and BOTH commit through the same move/resize path the
// timeline uses — previously the numeric start wrote `start` raw and skipped
// the speed recompute that dragging the same edge performs, so the two UIs
// disagreed about what changing a time means.

vi.mock('../api/hooks', () => ({ useArchetypes: vi.fn(() => ({ data: undefined })) }));
vi.mock('../api/archetypes', () => ({ useArchetypeAsProfile: vi.fn() }));

import DubSegmentRow from '../components/DubSegmentRow';

it('edits wrapping two-line text without seeking the player', () => {
  const props = makeProps();
  render(<DubSegmentRow {...props} />);
  const field = screen.getByDisplayValue('hola mundo');
  expect(field.tagName).toBe('TEXTAREA');
  expect(field).toHaveAttribute('rows', '2');
  fireEvent.click(field);
  fireEvent.change(field, { target: { value: 'first line\nsecond line' } });
  expect(props.onEditField).toHaveBeenCalledWith('s1', 'text', 'first line\nsecond line');
  expect(props.onSeek).not.toHaveBeenCalled();
});

function makeProps(over = {}) {
  return {
    seg: { id: 's1', start: 1, end: 3, text: 'hola mundo' },
    idx: 0,
    disabled: false,
    isActive: false,
    isDone: false,
    isPlaying: false,
    previewLoading: false,
    selected: false,
    profiles: [],
    speakerClones: {},
    onEditField: vi.fn(),
    onDelete: vi.fn(),
    onRestore: vi.fn(),
    onPreview: vi.fn(),
    onSelect: vi.fn(),
    onSplit: vi.fn(),
    onMerge: vi.fn(),
    onInsert: vi.fn(),
    onMoveResize: vi.fn(),
    canMerge: true,
    canMergePrev: true,
    onDirect: vi.fn(),
    onSeek: vi.fn(),
    timelineSelected: false,
    ...over,
  };
}

const timeFields = () => Array.from(document.querySelectorAll('input.seg-time-input'));

describe('DubSegmentRow timing fields', () => {
  it('renders the end time as an editable peer of the start', () => {
    render(<DubSegmentRow {...makeProps()} />);
    const fields = timeFields();
    expect(fields).toHaveLength(2);
    expect(fields[0].value).toBe('0:01.0');
    expect(fields[1].value).toBe('0:03.0');
  });

  it('commits a new end through the timeline resize path', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value: '0:05.0' } });
    fireEvent.blur(end);

    expect(props.onMoveResize).toHaveBeenCalledWith('s1', { start: 1, end: 5 });
    expect(props.onEditField).not.toHaveBeenCalled();
  });

  it('commits a new start through the same path, not a raw field write', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const start = timeFields()[0];

    fireEvent.change(start, { target: { value: '0:02.0' } });
    fireEvent.blur(start);

    expect(props.onMoveResize).toHaveBeenCalledWith('s1', { start: 2, end: 3 });
    expect(props.onEditField).not.toHaveBeenCalled();
  });

  it('nudges either edge by 100 ms with accessible controls', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);

    const decrement = screen.getAllByRole('button', { name: /0\.1 seconds earlier/ });
    const increment = screen.getAllByRole('button', { name: /0\.1 seconds later/ });
    fireEvent.click(decrement[0]);
    fireEvent.click(increment[1]);

    expect(props.onMoveResize).toHaveBeenNthCalledWith(1, 's1', { start: 0.9, end: 3 });
    expect(props.onMoveResize).toHaveBeenNthCalledWith(2, 's1', { start: 1, end: 3.1 });
  });

  it('preserves the timeline minimum duration for typed and stepped edits', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const start = timeFields()[0];

    fireEvent.change(start, { target: { value: '2.8' } });
    fireEvent.blur(start);
    expect(props.onMoveResize).not.toHaveBeenCalled();

    const nearLimit = makeProps({ seg: { id: 's1', start: 2.7, end: 3, text: 'x' } });
    render(<DubSegmentRow {...nearLimit} />);
    fireEvent.click(screen.getAllByRole('button', { name: /start time 0\.1 seconds later/ })[1]);
    expect(nearLimit.onMoveResize).not.toHaveBeenCalled();
  });

  it('accepts an exact 300 ms boundary despite decimal rounding', () => {
    const props = makeProps({ seg: { id: 's1', start: 1, end: 3.3, text: 'x' } });
    render(<DubSegmentRow {...props} />);
    const start = timeFields()[0];

    fireEvent.change(start, { target: { value: '3.0' } });
    fireEvent.blur(start);

    expect(props.onMoveResize).toHaveBeenCalledWith('s1', { start: 3, end: 3.3 });
  });

  it('surfaces an adjacent overlap beside the timing controls', () => {
    render(<DubSegmentRow {...makeProps({ hasOverlap: true })} />);
    expect(
      screen.getByText('Overlaps an adjacent segment — both lines will play together'),
    ).toBeInTheDocument();
  });

  it('accepts raw seconds as well as m:ss.s', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value: '4.5' } });
    fireEvent.blur(end);

    expect(props.onMoveResize).toHaveBeenCalledWith('s1', { start: 1, end: 4.5 });
  });

  it('reverts an end that would not outlast the start', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value: '0:00.5' } });
    fireEvent.blur(end);

    expect(props.onMoveResize).not.toHaveBeenCalled();
    expect(end.value).toBe('0:03.0');
  });

  it('reverts a start that would run past the end', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const start = timeFields()[0];

    fireEvent.change(start, { target: { value: '0:09.0' } });
    fireEvent.blur(start);

    expect(props.onMoveResize).not.toHaveBeenCalled();
    expect(start.value).toBe('0:01.0');
  });

  it('reverts unparseable input', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value: 'later' } });
    fireEvent.blur(end);

    expect(props.onMoveResize).not.toHaveBeenCalled();
    expect(end.value).toBe('0:03.0');
  });

  it.each(['5junk', '1:99', '1:02junk', '-2'])('rejects malformed time %s', (value) => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value } });
    fireEvent.blur(end);

    expect(props.onMoveResize).not.toHaveBeenCalled();
    expect(end.value).toBe('0:03.0');
  });

  it('abandons an edit on Escape', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.change(end, { target: { value: '0:08.0' } });
    fireEvent.keyDown(end, { key: 'Escape' });

    expect(end.value).toBe('0:03.0');
    expect(props.onMoveResize).not.toHaveBeenCalled();
  });

  it('does not fire for an unchanged value', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.blur(end);
    expect(props.onMoveResize).not.toHaveBeenCalled();
  });

  it('leaves a time the field rounds for display where it is', () => {
    // Transcribed times carry hundredths; the field shows tenths. Tabbing
    // through it must not snap the edge to the shown value.
    const props = makeProps({ seg: { id: 's1', start: 1.23, end: 3.27, text: 'x' } });
    render(<DubSegmentRow {...props} />);
    const [start, end] = timeFields();
    expect(start.value).toBe('0:01.2');
    expect(end.value).toBe('0:03.3');

    fireEvent.blur(start);
    fireEvent.blur(end);

    expect(props.onMoveResize).not.toHaveBeenCalled();
  });

  it('commits the shown time when it is typed on purpose', () => {
    const props = makeProps({ seg: { id: 's1', start: 1.23, end: 3.27, text: 'x' } });
    render(<DubSegmentRow {...props} />);
    const end = timeFields()[1];

    fireEvent.focus(end);
    fireEvent.change(end, { target: { value: '' } });
    fireEvent.change(end, { target: { value: '0:03.3' } });
    fireEvent.blur(end);

    expect(props.onMoveResize).toHaveBeenCalledWith('s1', { start: 1.23, end: 3.3 });
  });
});

describe('DubSegmentRow merge shortcuts', () => {
  it('merges downwards on Ctrl+M', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const text = screen.getByDisplayValue('hola mundo');

    fireEvent.keyDown(text, { key: 'm', ctrlKey: true });
    expect(props.onMerge).toHaveBeenCalledWith('s1', 'next');
  });

  it('merges upwards on Ctrl+Shift+M', () => {
    const props = makeProps();
    render(<DubSegmentRow {...props} />);
    const text = screen.getByDisplayValue('hola mundo');

    fireEvent.keyDown(text, { key: 'M', ctrlKey: true, shiftKey: true });
    expect(props.onMerge).toHaveBeenCalledWith('s1', 'prev');
  });

  it('stays put when there is no previous row to merge into', () => {
    const props = makeProps({ canMergePrev: false });
    render(<DubSegmentRow {...props} />);
    const text = screen.getByDisplayValue('hola mundo');

    fireEvent.keyDown(text, { key: 'M', ctrlKey: true, shiftKey: true });
    expect(props.onMerge).not.toHaveBeenCalled();
  });
});
