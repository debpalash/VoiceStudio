import React, { useState } from 'react';
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import TrackManager from './TrackManager';

const copy = {
  'common.close': 'Close',
  'common.search': 'Search…',
  'dub.no_matches': 'No matches',
  'exportModal.primary': 'Primary',
  'exportModal.track_all': 'All',
  'exportModal.track_dubs_only': 'Dubs only',
  'exportModal.track_none': 'None',
  'exportModal.tracks': 'Tracks',
};
const t = (key) => copy[key] || key;
const tracks = Array.from({ length: 57 }, (_, index) => ({
  code: `l${index}`,
  label: `Language ${index}`,
  kind: 'dub',
}));

function Harness({ onChange }) {
  const [selection, setSelection] = useState({});
  const update = (next) => {
    setSelection((previous) => {
      const resolved = typeof next === 'function' ? next(previous) : next;
      onChange(resolved);
      return resolved;
    });
  };
  return (
    <TrackManager
      t={t}
      tracks={tracks}
      selection={selection}
      setSelection={update}
      primaryCode="l0"
    />
  );
}

describe('TrackManager', () => {
  it('keeps 57 tracks collapsed, searches the contained list, and restores focus', () => {
    const onChange = vi.fn();
    render(<Harness onChange={onChange} />);
    const trigger = screen.getByRole('button', { name: 'Tracks 57/57' });

    expect(screen.queryAllByRole('checkbox')).toHaveLength(0);
    fireEvent.click(trigger);
    expect(screen.getAllByRole('checkbox')).toHaveLength(57);
    fireEvent.change(screen.getByRole('textbox', { name: 'Search…' }), {
      target: { value: 'Language 56' },
    });
    expect(screen.getAllByRole('checkbox')).toHaveLength(1);
    fireEvent.click(screen.getByRole('checkbox'));
    expect(onChange).toHaveBeenLastCalledWith({ l56: false });

    fireEvent.change(screen.getByRole('textbox', { name: 'Search…' }), {
      target: { value: 'missing' },
    });
    expect(screen.getByText('No matches')).toBeInTheDocument();
    fireEvent.keyDown(document, { key: 'Escape' });
    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(trigger).toHaveFocus();
  });
});
